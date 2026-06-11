#include "GammaClient.h"
#include <spdlog/spdlog.h>
#include <boost/json.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <boost/asio/ssl.hpp>
#include <algorithm>
#include <ctime>
#include <set>

namespace trading {

namespace beast = boost::beast;
namespace http = beast::http;

GammaClient::GammaClient(boost::asio::io_context& ioc, boost::asio::ssl::context& ctx) : ioc_(ioc), ctx_(ctx) {}

std::optional<double> GammaClient::fetch_token_price(const std::string& token_id, const std::string& side) {
    // Mirrors Python: GET clob.polymarket.com/price?token_id={id}&side={side}
    // Resolution order matches Python exactly:
    //   1. WS cache in StateStore (caller checked first)
    //   2. This REST call (blocking ~200ms, run on gamma_ioc only)
    try {
        std::string target = "/price?token_id=" + token_id + "&side=" + side;
        std::string body = http_get("clob.polymarket.com", target);
        auto jv = boost::json::parse(body);
        if (!jv.is_object()) return std::nullopt;
        const auto& obj = jv.as_object();
        if (!obj.contains("price")) return std::nullopt;
        double price = 0.0;
        const auto& pv = obj.at("price");
        if (pv.is_double())      price = pv.as_double();
        else if (pv.is_string()) price = std::stod(std::string(pv.as_string()));
        else if (pv.is_int64()) price = static_cast<double>(pv.as_int64());
        if (price <= 0.0 || price >= 1.0) return std::nullopt;
        return price;
    } catch (const std::exception& e) {
        spdlog::debug("GammaClient::fetch_token_price failed for {}: {}", token_id.substr(0,16), e.what());
        return std::nullopt;
    }
}

std::string GammaClient::http_get(const std::string& host, const std::string& target) {
    std::lock_guard<std::mutex> lock(http_mutex_);
    boost::asio::ip::tcp::resolver resolver(ioc_);
    boost::asio::ssl::stream<beast::tcp_stream> stream(ioc_, ctx_);
    SSL_set_tlsext_host_name(stream.native_handle(), host.c_str());
    beast::get_lowest_layer(stream).expires_after(std::chrono::seconds(10));
    auto const results = resolver.resolve(host, "443");
    beast::get_lowest_layer(stream).connect(results);
    
    beast::get_lowest_layer(stream).expires_after(std::chrono::seconds(10));
    stream.handshake(boost::asio::ssl::stream_base::client);

    http::request<http::string_body> req{http::verb::get, target, 11};
    req.set(http::field::host, host);
    req.set(http::field::user_agent, "Mozilla/5.0");
    
    beast::get_lowest_layer(stream).expires_after(std::chrono::seconds(10));
    http::write(stream, req);

    beast::flat_buffer buffer;
    http::response<http::string_body> res;
    beast::get_lowest_layer(stream).expires_after(std::chrono::seconds(10));
    http::read(stream, buffer, res);
    beast::error_code ec;
    stream.shutdown(ec);
    return res.body();
}

std::optional<double> GammaClient::fetch_binance_price(const std::string& symbol) {
    try {
        std::string target = "/api/v3/ticker/price?symbol=" + symbol;
        std::string body = http_get("api.binance.com", target);
        auto jv = boost::json::parse(body);
        if (!jv.is_object() || !jv.as_object().contains("price")) return std::nullopt;
        return std::stod(std::string(jv.as_object().at("price").as_string()));
    } catch (const std::exception& e) {
        spdlog::debug("GammaClient::fetch_binance_price failed for {}: {}", symbol, e.what());
        return std::nullopt;
    }
}

std::optional<MarketInfo> GammaClient::probe_slug(const std::string& asset, long long ts, int window_minutes) {
    if (window_minutes != 5 && window_minutes != 15) return std::nullopt;
    std::string slug = asset + "-updown-" + std::to_string(window_minutes) + "m-" + std::to_string(ts);
    const long long window_seconds = window_minutes * 60LL;
    std::string target = "/events?slug=" + slug;

    std::string body;
    try {
        body = http_get("gamma-api.polymarket.com", target);
    } catch (...) { return std::nullopt; }

    boost::json::value jv;
    try { jv = boost::json::parse(body); } catch (...) { return std::nullopt; }

    if (!jv.is_array() || jv.as_array().empty()) return std::nullopt;

    auto const& event = jv.as_array().at(0).as_object();
    if (!event.contains("markets") || event.at("markets").as_array().empty())
        return std::nullopt;

    auto const& gm = event.at("markets").as_array().at(0).as_object();

    // Get condition ID
    if (!gm.contains("conditionId")) return std::nullopt;
    std::string condition_id = std::string(gm.at("conditionId").as_string());

    // Get end date
    std::string end_date = "";
    if (gm.contains("endDate")) end_date = std::string(gm.at("endDate").as_string());

    // Validate timing — must not be expired and not too far in future
    double secs_remaining = 0;
    if (!end_date.empty()) {
        struct tm tm = {};
        int parsed = sscanf(end_date.c_str(), "%d-%d-%dT%d:%d:%dZ",
               &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
               &tm.tm_hour, &tm.tm_min, &tm.tm_sec);
        if (parsed < 6) {
            spdlog::warn("GammaClient: Failed to parse endDate '{}'", end_date);
            return std::nullopt;
        }
        tm.tm_year -= 1900; tm.tm_mon -= 1;
        double end_ts = static_cast<double>(timegm(&tm));
        double now_ts = static_cast<double>(std::time(nullptr));
        secs_remaining = end_ts - now_ts;
        if (secs_remaining < -10 || secs_remaining > window_seconds * 2) return std::nullopt;
    }

    // Check liquidity
    double liquidity = 0;
    if (event.contains("liquidity")) {
        auto& lv = event.at("liquidity");
        if (lv.is_double()) liquidity = lv.as_double();
        else if (lv.is_int64()) liquidity = static_cast<double>(lv.as_int64());
        else if (lv.is_string()) try { liquidity = std::stod(std::string(lv.as_string())); } catch(...) {}
    }
    if (liquidity < 1000.0) return std::nullopt;

    // Parse clobTokenIds from gamma market object (faster than CLOB API call)
    std::string yes_token, no_token;
    if (gm.contains("clobTokenIds")) {
        std::string tok_str = std::string(gm.at("clobTokenIds").as_string());
        try {
            auto tokens = boost::json::parse(tok_str).as_array();
            if (tokens.size() >= 2) {
                yes_token = std::string(tokens[0].as_string());
                no_token  = std::string(tokens[1].as_string());
            }
        } catch (...) {}
    }

    // Fallback to CLOB API if tokens not in gamma
    if (yes_token.empty()) {
        try {
            std::string clob_body = http_get("clob.polymarket.com", "/markets/" + condition_id);
            auto cj = boost::json::parse(clob_body).as_object();
            if (!cj.contains("tokens")) return std::nullopt;
            for (const auto& t : cj.at("tokens").as_array()) {
                auto const& tok = t.as_object();
                std::string outcome = tok.contains("outcome") ? std::string(tok.at("outcome").as_string()) : "";
                std::string tid = tok.contains("token_id") ? std::string(tok.at("token_id").as_string()) : "";
                if (outcome == "Up" || outcome == "Yes") yes_token = tid;
                else no_token = tid;
            }
        } catch (...) { return std::nullopt; }
    }

    if (yes_token.empty() || no_token.empty()) return std::nullopt;

    // Parse prices from outcomePrices
    double yes_price = 0.5, no_price = 0.5;
    if (gm.contains("outcomePrices")) {
        try {
            std::string prices_str = std::string(gm.at("outcomePrices").as_string());
            auto prices = boost::json::parse(prices_str).as_array();
            if (prices.size() >= 2) {
                yes_price = std::stod(std::string(prices[0].as_string()));
                no_price  = std::stod(std::string(prices[1].as_string()));
            }
        } catch (...) {}
    }

    MarketInfo info;
    info.condition_id = condition_id;
    info.question     = gm.contains("question") ? std::string(gm.at("question").as_string()) : slug;
    info.yes_token_id = yes_token;
    info.no_token_id  = no_token;
    info.yes_price    = yes_price;
    info.no_price     = no_price;
    info.asset         = asset;
    info.window_minutes = window_minutes;
    info.end_date_iso  = end_date;

    // Polymarket Up/Down markets are neg-risk markets — they require orders
    // signed against the NEG_RISK exchange address, not the CTF Exchange.
    // "negRisk" is set in the Gamma API market object.
    // Fallback: treat any "updown" slug as neg-risk (all 5m/15m markets are).
    if (gm.contains("negRisk")) {
        const auto& nrv = gm.at("negRisk");
        if (nrv.is_bool())    info.is_neg_risk = nrv.as_bool();
        else if (nrv.is_int64()) info.is_neg_risk = nrv.as_int64() != 0;
    } else {
        // Fallback: slug-based detection for updown markets
        info.is_neg_risk = (slug.find("updown") != std::string::npos);
    }

    if (!end_date.empty()) {
        struct tm tm = {};
        sscanf(end_date.c_str(), "%d-%d-%dT%d:%d:%dZ",
               &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
               &tm.tm_hour, &tm.tm_min, &tm.tm_sec);
        tm.tm_year -= 1900; tm.tm_mon -= 1;
        info.end_date_ts = static_cast<double>(timegm(&tm));
    }

    spdlog::info("GammaClient: Slug hit {} | {} | {:.0f}s left | liq=${:.0f}",
                 slug, info.question.substr(0, 50), secs_remaining, liquidity);
    return info;
}

std::vector<MarketInfo> GammaClient::fetch_updown_markets(const std::string& asset, int window_minutes) {
    std::vector<MarketInfo> results;
    try {
        if (window_minutes != 5 && window_minutes != 15) return results;

        std::string a_l = asset;
        std::transform(a_l.begin(), a_l.end(), a_l.begin(), ::tolower);

        long long now_ts = static_cast<long long>(std::time(nullptr));
        long long window = static_cast<long long>(window_minutes) * 60LL;
        long long base = (now_ts / window) * window;

        // Probe prev, current, next, next+1 windows — same as Python
        std::set<std::string> seen;
        for (int offset : {-1, 0, 1, 2}) {
            long long ts = base + offset * window;
            auto result = probe_slug(a_l, ts, window_minutes);
            if (result && !seen.count(result->condition_id)) {
                seen.insert(result->condition_id);
                results.push_back(*result);
            }
        }

        // Sort by soonest expiry first (most in-progress window first)
        std::sort(results.begin(), results.end(), [](const MarketInfo& a, const MarketInfo& b) {
            return a.end_date_ts < b.end_date_ts;
        });

        spdlog::info("GammaClient: Found {} active {} {}m markets via slug probe.",
                     results.size(), asset, window_minutes);
    } catch (const std::exception& e) {
        spdlog::error("GammaClient error for {}: {}", asset, e.what());
    }
    return results;
}

} // namespace trading
