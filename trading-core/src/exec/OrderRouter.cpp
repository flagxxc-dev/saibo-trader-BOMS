#include "OrderRouter.h"
#include <boost/json.hpp>
#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/ssl/error.hpp>
#include <boost/asio/ssl/stream.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <boost/beast/ssl.hpp>
#include <boost/beast/version.hpp>
#include <spdlog/spdlog.h>
#include <fmt/core.h>
#include <chrono>
#include <random>
#include <cmath>
#include <thread>

namespace trading {
namespace exec {

namespace {
constexpr double kMinLegUsdc = 1.0;
constexpr double kFloatTol = 1e-6;
constexpr double kMinFillShares = 0.01;
constexpr double kDepthFillRatio = 0.90;
constexpr double kMaxAskPriceSlack = 1.02;

bool leg_meets_minimum(double price, double size_shares) {
    return price > 0.0 && size_shares * price >= kMinLegUsdc - kFloatTol;
}
} // namespace

OrderRouter::OrderRouter(boost::asio::io_context& ioc, 
                        boost::asio::ssl::context& ctx,
                        trading::StateStore& store,
                        risk::RiskManager& risk_manager,
                        const std::string& clob_api_url, 
                        const std::string& chain_id_str,
                        const std::string& verifying_contract,
                        const std::string& private_key_hex,
                        const std::string& signer_address,
                        const std::string& funder_address,
                        bool paper_mode,
                        const std::string& api_key,
                        const std::string& api_secret,
                        const std::string& api_passphrase,
                        const std::string& neg_risk_exchange,
                        bool live_dh_dry_run)
    : ioc_(ioc), ctx_(ctx), store_(store), risk_manager_(risk_manager),
      clob_api_url_(clob_api_url), signer_address_(signer_address), funder_address_(funder_address), 
      paper_mode_(paper_mode),
      live_dh_dry_run_(live_dh_dry_run && !paper_mode),
      api_key_(api_key), api_secret_(api_secret), api_passphrase_(api_passphrase),
      neg_risk_exchange_(neg_risk_exchange) {
    
    if (!paper_mode_ && api_key_.empty()) {
        spdlog::critical("[FATAL] Live trading enabled but POLY_API_KEY is missing! Run derive_and_update_keys.py first.");
        throw std::runtime_error("Missing API credentials for live trading");
    }
    if (live_dh_dry_run_) {
        spdlog::info("[LIVE DH] Dry-run ON — REST book validation only, no CLOB orders will be sent");
    }
    
    signer_ = std::make_unique<EIP712Signer>(std::stoull(chain_id_str), verifying_contract, private_key_hex);
    if (!neg_risk_exchange_.empty()) {
        signer_neg_risk_ = std::make_unique<EIP712Signer>(std::stoull(chain_id_str), neg_risk_exchange_, private_key_hex);
    }
}

OrderRouter::~OrderRouter() {}

Order OrderRouter::build_order(const std::string& token_id, double price, double size_shares, uint8_t side) const {
    Order order;
    order.salt = generate_salt();
    order.maker = funder_address_;
    order.signer = signer_address_;
    order.taker = "0x0000000000000000000000000000000000000000";
    order.tokenId = token_id;

    uint64_t scale = 1000000;
    if (side == 0) {
        order.makerAmount = std::to_string(static_cast<uint64_t>(size_shares * price * scale));
        order.takerAmount = std::to_string(static_cast<uint64_t>(size_shares * scale));
    } else {
        order.makerAmount = std::to_string(static_cast<uint64_t>(size_shares * scale));
        order.takerAmount = std::to_string(static_cast<uint64_t>(size_shares * price * scale));
    }

    auto now = std::chrono::system_clock::now();
    auto exp = now + std::chrono::seconds(60);
    order.expiration = std::to_string(std::chrono::duration_cast<std::chrono::seconds>(exp.time_since_epoch()).count());
    order.side = side;
    order.signatureType = (funder_address_ == signer_address_ ? 0 : 1);
    order.timestamp = std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count());
    order.metadata = "0x0000000000000000000000000000000000000000000000000000000000000000";
    order.builder = "0x0000000000000000000000000000000000000000000000000000000000000000";
    return order;
}

bool OrderRouter::submit_order(const std::string& token_id, double price, double size, uint8_t side, bool is_neg_risk) {
    try {
        Order order = build_order(token_id, price, size, side);
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            return simulate_paper_order(order, sig, "", "", 0.0, "MANUAL", "", is_neg_risk);
        }
        LegFillResult fill = execute_rest_order(order, sig, is_neg_risk, true, "", "", 0.0, "MANUAL", "");
        return fill.success;
    } catch (const std::exception& e) {
        spdlog::error("Order signature failed: {}", e.what());
        return false;
    }
}

double OrderRouter::query_ask_depth_shares(const std::string& token_id, double price) {
    if (price <= 0.0) return -1.0;
    auto book = fetch_book_object(token_id);
    if (!book) return -1.0;
    if (!book->contains("asks") || !book->at("asks").is_array()) return -1.0;

    const double max_price = price * kMaxAskPriceSlack;
    double shares_available = 0.0;
    for (const auto& level_v : book->at("asks").as_array()) {
        if (!level_v.is_object()) continue;
        const auto& level = level_v.as_object();
        if (!level.contains("price") || !level.contains("size")) continue;
        double p = std::stod(std::string(level.at("price").as_string()));
        double s = std::stod(std::string(level.at("size").as_string()));
        if (p <= max_price) {
            shares_available += s;
        }
    }
    return shares_available;
}

std::optional<boost::json::object> OrderRouter::fetch_book_object(const std::string& token_id) {
    namespace beast = boost::beast;
    namespace http = beast::http;

    std::string host = "clob.polymarket.com";
    std::string target = "/book?token_id=" + token_id;

    try {
        std::lock_guard<std::mutex> lock(http_mutex_);

        boost::asio::ip::tcp::resolver resolver(ioc_);
        beast::ssl_stream<beast::tcp_stream> stream(ioc_, ctx_);
        if (!SSL_set_tlsext_host_name(stream.native_handle(), host.c_str())) return std::nullopt;
        auto const results = resolver.resolve(host, "443");
        beast::get_lowest_layer(stream).connect(results);
        stream.handshake(boost::asio::ssl::stream_base::client);

        http::request<http::string_body> req{http::verb::get, target, 11};
        req.set(http::field::host, host);
        req.set(http::field::user_agent, "PolymarketBot/1.0");

        http::write(stream, req);
        beast::flat_buffer buffer;
        http::response<http::string_body> res;
        http::read(stream, buffer, res);
        beast::error_code ec;
        stream.shutdown(ec);

        if (res.result() != http::status::ok) return std::nullopt;

        auto jv = boost::json::parse(res.body());
        if (!jv.is_object()) return std::nullopt;
        return jv.as_object();
    } catch (const std::exception& e) {
        spdlog::warn("fetch_book_object failed for {}: {}", token_id.substr(0, 12), e.what());
        return std::nullopt;
    }
}

BookAskInfo OrderRouter::parse_book_asks(const boost::json::object& book) const {
    BookAskInfo info;
    if (!book.contains("asks") || !book.at("asks").is_array()) return info;
    const auto& asks = book.at("asks").as_array();
    if (asks.empty()) return info;

    double best = 1.0;
    for (const auto& level_v : asks) {
        if (!level_v.is_object()) continue;
        const auto& level = level_v.as_object();
        if (!level.contains("price")) continue;
        double p = std::stod(std::string(level.at("price").as_string()));
        if (p > 0.0 && p < best) best = p;
    }
    if (best >= 1.0) return info;

    info.best_ask = best;
    const double max_price = best * kMaxAskPriceSlack;
    for (const auto& level_v : asks) {
        if (!level_v.is_object()) continue;
        const auto& level = level_v.as_object();
        if (!level.contains("price") || !level.contains("size")) continue;
        double p = std::stod(std::string(level.at("price").as_string()));
        double s = std::stod(std::string(level.at("size").as_string()));
        if (p <= max_price) {
            info.depth_shares += s;
        }
    }
    info.ok = true;
    return info;
}

BookAskInfo OrderRouter::fetch_book_ask_info(const std::string& token_id) {
    auto book = fetch_book_object(token_id);
    if (!book) return {};
    return parse_book_asks(*book);
}

bool OrderRouter::check_book_depth(const std::string& token_id, double price, double size_shares) {
    if (size_shares <= 0.0) return false;
    double available = query_ask_depth_shares(token_id, price);
    if (available < 0.0) return false;
    return available >= size_shares * kDepthFillRatio;
}

LegFillResult OrderRouter::execute_dh_leg_buy(const std::string& token_id, double price, double size_shares, bool is_neg_risk) {
    try {
        Order order = build_order(token_id, price, size_shares, 0);
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            LegFillResult r;
            r.success = simulate_paper_order(order, sig, "", "", 0.0, "MANUAL", "", is_neg_risk);
            if (r.success) {
                r.price = price;
                r.size_shares = size_shares;
            }
            return r;
        }
        return execute_rest_order(order, sig, is_neg_risk, false);
    } catch (const std::exception& e) {
        spdlog::error("DH leg buy failed: {}", e.what());
        return {};
    }
}

LegFillResult OrderRouter::execute_unwind_sell(const std::string& token_id, double price, double size_shares, bool is_neg_risk) {
    try {
        Order order = build_order(token_id, price, size_shares, 1);
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            LegFillResult r;
            r.success = true;
            r.price = price;
            r.size_shares = size_shares;
            return r;
        }
        return execute_rest_order(order, sig, is_neg_risk, false);
    } catch (const std::exception& e) {
        spdlog::error("Unwind sell failed: {}", e.what());
        return {};
    }
}

bool OrderRouter::submit_dump_hedge_order(const DumpHedgeSignal& signal, double size_shares) {
    std::string dh_id = "DH-" + signal.asset + "-" + std::to_string(static_cast<uint64_t>(signal.timestamp));
    bool is_neg_risk = signal.market.is_neg_risk;

    if (!leg_meets_minimum(signal.yes_price, size_shares) || !leg_meets_minimum(signal.no_price, size_shares)) {
        spdlog::warn("[DH] Skipped {} — leg below ${:.2f} exchange minimum (size {:.2f})",
                     signal.asset, kMinLegUsdc, size_shares);
        return false;
    }

    if (paper_mode_) {
        risk::DumpHedgePosition dh_pos;
        dh_pos.dh_id = dh_id;
        dh_pos.asset = signal.asset;
        dh_pos.market_question = signal.market.question;
        dh_pos.yes_token_id = signal.yes_token_id;
        dh_pos.no_token_id = signal.no_token_id;
        dh_pos.yes_entry_price = signal.yes_price;
        dh_pos.no_entry_price = signal.no_price;
        dh_pos.combined_entry_price = signal.combined_price;
        dh_pos.size_shares = size_shares;
        dh_pos.combined_cost_usdc = signal.combined_price * size_shares;
        double fee_per_share = store_.compute_dh_entry_fee_per_share(
            signal.yes_price, signal.no_price, signal.yes_token_id, signal.no_token_id);
        double entry_fees = fee_per_share * size_shares;
        dh_pos.locked_profit_usdc = (1.0 - signal.combined_price) * size_shares - entry_fees;
        dh_pos.opened_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        dh_pos.end_date_ts = signal.market.end_date_ts;
        dh_pos.paper_mode = true;
        dh_pos.is_neg_risk = is_neg_risk;
        dh_pos.window_minutes = signal.market.window_minutes;
        dh_pos.condition_id = signal.market.condition_id;

        risk_manager_.register_dh_open(dh_pos);
        spdlog::info("[PAPER DH] OPENED | {} {}m | Entry: {:.4f} | Locked Profit: ${:.2f}",
                     signal.asset, signal.market.window_minutes, signal.combined_price, dh_pos.locked_profit_usdc);
        store_.push_telemetry(fmt::format("[DH] OPEN {} | sum {:.4f} | {:.2f} shares | locked ${:.2f}",
            signal.asset, signal.combined_price, size_shares, dh_pos.locked_profit_usdc));
        return true;
    }

    spdlog::info("[LIVE DH] Book-aware exec for {} (neg_risk={}, dry_run={})",
                 signal.asset, is_neg_risk, live_dh_dry_run_);

    BookAskInfo yes_book = fetch_book_ask_info(signal.yes_token_id);
    BookAskInfo no_book = fetch_book_ask_info(signal.no_token_id);
    if (!yes_book.ok || !no_book.ok) {
        spdlog::error("[LIVE DH] Empty ask side for {} — yes_book={} no_book={}",
                      signal.asset, yes_book.ok, no_book.ok);
        return false;
    }

    const double exec_yes = yes_book.best_ask;
    const double exec_no = no_book.best_ask;
    const double exec_combined = exec_yes + exec_no;

    spdlog::info(
        "[LIVE DH] Book vs WS {} | WS YES:{:.4f} NO:{:.4f} SUM:{:.4f} | "
        "BOOK YES:{:.4f} NO:{:.4f} SUM:{:.4f} | depth YES:{:.2f} NO:{:.2f}",
        signal.asset,
        signal.yes_price, signal.no_price, signal.combined_price,
        exec_yes, exec_no, exec_combined,
        yes_book.depth_shares, no_book.depth_shares);

    const double sum_target = store_.get_dh_sum_target();
    const double min_discount = store_.get_dh_min_discount();
    double fee_per_share = store_.compute_dh_entry_fee_per_share(
        exec_yes, exec_no, signal.yes_token_id, signal.no_token_id);
    double exec_discount = 1.0 - exec_combined - fee_per_share;

    if (exec_combined > sum_target + kFloatTol) {
        spdlog::warn(
            "[LIVE DH] Book sum {:.4f} > target {:.4f} for {} — edge gone at best ask (drift YES:{:+.4f} NO:{:+.4f})",
            exec_combined, sum_target, signal.asset,
            exec_yes - signal.yes_price, exec_no - signal.no_price);
        return false;
    }
    if (exec_discount + kFloatTol < min_discount) {
        spdlog::warn(
            "[LIVE DH] Book discount {:.4f} < min {:.4f} for {} (fees {:.4f}/share)",
            exec_discount, min_discount, signal.asset, fee_per_share);
        return false;
    }

    const double min_order_usdc = risk_manager_.get_min_order_size();
    double try_shares = std::min(
        size_shares,
        std::min(yes_book.depth_shares, no_book.depth_shares) / kDepthFillRatio);
    if (try_shares + kFloatTol < size_shares) {
        spdlog::info(
            "[LIVE DH] Book resize {} | {:.2f} -> {:.2f} shares | yes_depth={:.2f} no_depth={:.2f}",
            signal.asset, size_shares, try_shares, yes_book.depth_shares, no_book.depth_shares);
    }

    while (try_shares >= kMinFillShares) {
        const double combined_cost = try_shares * exec_combined;
        if (combined_cost + kFloatTol < min_order_usdc) {
            spdlog::warn(
                "[LIVE DH] Book allows {:.2f} shares but ${:.2f} < MIN_ORDER ${:.2f} for {} — aborting.",
                try_shares, combined_cost, min_order_usdc, signal.asset);
            return false;
        }
        if (!leg_meets_minimum(exec_yes, try_shares) || !leg_meets_minimum(exec_no, try_shares)) {
            spdlog::warn("[LIVE DH] Resized {:.2f} shares below leg minimum for {} — aborting.", try_shares, signal.asset);
            return false;
        }
        if (yes_book.depth_shares + kFloatTol >= try_shares * kDepthFillRatio &&
            no_book.depth_shares + kFloatTol >= try_shares * kDepthFillRatio) {
            size_shares = try_shares;
            break;
        }
        try_shares *= 0.5;
    }

    if (try_shares < kMinFillShares) {
        spdlog::error(
            "[LIVE DH] Insufficient book depth at best ask for {} — aborting (yes={:.2f} no={:.2f} need {:.2f}).",
            signal.asset, yes_book.depth_shares, no_book.depth_shares, size_shares * kDepthFillRatio);
        return false;
    }

    if (live_dh_dry_run_) {
        const double shadow_cost = exec_combined * size_shares;
        const double shadow_locked = exec_discount * size_shares;
        spdlog::info(
            "[LIVE DH SHADOW] WOULD OPEN | {} {}m | YES@{:.4f} NO@{:.4f} SUM:{:.4f} | "
            "{:.2f} shares | cost ${:.2f} | locked ${:.2f} | WS drift YES:{:+.4f} NO:{:+.4f}",
            signal.asset, signal.market.window_minutes,
            exec_yes, exec_no, exec_combined,
            size_shares, shadow_cost, shadow_locked,
            exec_yes - signal.yes_price, exec_no - signal.no_price);
        store_.push_telemetry(fmt::format(
            "[DH SHADOW] {} | book sum {:.4f} | {:.2f} sh | locked ${:.2f} | no order sent",
            signal.asset, exec_combined, size_shares, shadow_locked));
        return false;
    }

    LegFillResult yes_fill = execute_dh_leg_buy(signal.yes_token_id, exec_yes, size_shares, is_neg_risk);
    if (!yes_fill.success || yes_fill.size_shares < kMinFillShares) {
        spdlog::error("[LIVE DH] YES leg failed for {} (filled {:.4f})", signal.asset, yes_fill.size_shares);
        return false;
    }

    LegFillResult no_fill = execute_dh_leg_buy(signal.no_token_id, exec_no, size_shares, is_neg_risk);
    if (!no_fill.success || no_fill.size_shares < kMinFillShares) {
        spdlog::error("[LIVE DH] NO leg failed for {} after YES filled — unwinding YES", signal.asset);
        LegFillResult unwind = execute_unwind_sell(signal.yes_token_id, exec_yes, yes_fill.size_shares, is_neg_risk);
        if (unwind.success) {
            spdlog::warn("[LIVE DH] YES leg unwound successfully for {}", signal.asset);
            store_.push_telemetry(fmt::format("[DH] ROLLBACK {} | YES leg sold back", signal.asset));
        } else {
            spdlog::critical("[LIVE DH] YES leg filled but unwind FAILED for {} — manual intervention required", signal.asset);
            store_.push_telemetry(fmt::format("[DH] CRITICAL {} | YES filled, unwind failed", signal.asset));
        }
        return false;
    }

    double filled_shares = std::min(yes_fill.size_shares, no_fill.size_shares);
    double combined_price = yes_fill.price + no_fill.price;
    double combined_cost = yes_fill.price * filled_shares + no_fill.price * filled_shares;
    double fee_per_share = store_.compute_dh_entry_fee_per_share(
        yes_fill.price, no_fill.price, signal.yes_token_id, signal.no_token_id);
    double entry_fees = fee_per_share * filled_shares;
    double locked_profit = (1.0 - combined_price) * filled_shares - entry_fees;

    risk::DumpHedgePosition dh_pos;
    dh_pos.dh_id = dh_id;
    dh_pos.asset = signal.asset;
    dh_pos.market_question = signal.market.question;
    dh_pos.yes_token_id = signal.yes_token_id;
    dh_pos.no_token_id = signal.no_token_id;
    dh_pos.yes_entry_price = yes_fill.price;
    dh_pos.no_entry_price = no_fill.price;
    dh_pos.combined_entry_price = combined_price;
    dh_pos.size_shares = filled_shares;
    dh_pos.combined_cost_usdc = combined_cost;
    dh_pos.locked_profit_usdc = locked_profit;
    dh_pos.opened_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    dh_pos.end_date_ts = signal.market.end_date_ts;
    dh_pos.paper_mode = false;
    dh_pos.is_neg_risk = is_neg_risk;
    dh_pos.window_minutes = signal.market.window_minutes;
    dh_pos.condition_id = signal.market.condition_id;

    risk_manager_.register_dh_open(dh_pos);
    spdlog::info("[LIVE DH] OPENED | {} {}m | YES {:.4f} NO {:.4f} | Cost ${:.2f} | Locked ${:.2f}",
                 signal.asset, signal.market.window_minutes, yes_fill.price, no_fill.price, combined_cost, locked_profit);
    store_.push_telemetry(fmt::format("[DH] LIVE OPEN {} | sum {:.4f} | {:.2f} shares | locked ${:.2f}",
        signal.asset, combined_price, filled_shares, locked_profit));
    return true;
}

void OrderRouter::submit_close_order(const std::string& order_id, const std::string& token_id, double current_price, double size, const std::string& asset, const std::string& question, double end_date_ts, const std::string& strategy, bool is_neg_risk) {
    try {
        Order order = build_order(token_id, current_price, size, 1);
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            simulate_paper_order(order, sig, asset, question, end_date_ts, strategy, order_id, is_neg_risk);
        } else {
            execute_rest_order(order, sig, is_neg_risk, true, asset, question, end_date_ts, strategy, order_id);
        }
    } catch (const std::exception& e) {
        spdlog::error("Close order signature failed: {}", e.what());
    }
}

bool OrderRouter::simulate_paper_order(const Order& order, const Signature& sig, const std::string& asset, const std::string& question, double end_date_ts, const std::string& strategy, const std::string& original_order_id, bool is_neg_risk, const std::string& direction) {
    (void)sig;
    (void)is_neg_risk;
    if (order.side == 0) {
        double price = std::stod(order.makerAmount) / std::stod(order.takerAmount);
        double size_shares = std::stod(order.takerAmount) / 1000000.0;
        double cost = price * size_shares;

        risk::Position pos;
        pos.order_id = "paper_" + order.salt;
        pos.token_id = order.tokenId;
        pos.market_question = question;
        pos.side = "BUY";
        pos.entry_price = price;
        pos.size_shares = size_shares;
        pos.cost_usdc = cost;
        pos.opened_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        pos.end_date_ts = end_date_ts;
        pos.asset = asset;
        pos.strategy = strategy;
        pos.paper_mode = true;
        pos.is_neg_risk = is_neg_risk;
        pos.direction = direction;

        risk_manager_.register_trade_open(pos);
        spdlog::info("[PAPER TRADE] FILLED | {} | {} | Strategy: {} | Price: {:.4f} | Size: {:.2f} | Cost: ${:.2f}",
                     asset, question, strategy, price, size_shares, cost);
    } else {
        double price = std::stod(order.takerAmount) / std::stod(order.makerAmount);
        risk_manager_.register_trade_close(original_order_id, price);
        spdlog::info("[PAPER TRADE] CLOSED | {} | {} | Strategy: {} | Exit Price: {:.4f}",
                     asset, question, strategy, price);
    }
    return true;
}

LegFillResult OrderRouter::execute_rest_order(
    const Order& order,
    const Signature& sig,
    bool is_neg_risk,
    bool register_position,
    const std::string& asset,
    const std::string& question,
    double end_date_ts,
    const std::string& strategy,
    const std::string& original_order_id
) {
    namespace beast = boost::beast;
    namespace http = beast::http;

    LegFillResult result;

    boost::json::object root;
    boost::json::object ord;
    ord["salt"] = std::stoull(order.salt);
    ord["maker"] = order.maker;
    ord["signer"] = order.signer;
    ord["taker"] = order.taker;
    ord["tokenId"] = order.tokenId;
    ord["makerAmount"] = order.makerAmount;
    ord["takerAmount"] = order.takerAmount;
    ord["expiration"] = std::stoull(order.expiration);
    ord["side"] = order.side == 0 ? "BUY" : "SELL";
    ord["signatureType"] = (funder_address_ != signer_address_ && !signer_address_.empty()) ? 1 : 0;
    ord["timestamp"] = order.timestamp;
    ord["signature"] = sig.rsv_hex;
    root["order"] = std::move(ord);
    root["owner"] = api_key_;
    root["orderType"] = "FAK";
    root["postOnly"] = false;

    std::string payload = boost::json::serialize(root);

    try {
        std::lock_guard<std::mutex> lock(http_mutex_);

        std::string host = "clob.polymarket.com";
        std::string target = "/order";

        boost::asio::ip::tcp::resolver resolver(ioc_);
        beast::ssl_stream<beast::tcp_stream> stream(ioc_, ctx_);

        if (!SSL_set_tlsext_host_name(stream.native_handle(), host.c_str())) {
            throw std::runtime_error("Failed to set SNI hostname");
        }

        auto const results = resolver.resolve(host, "443");
        beast::get_lowest_layer(stream).connect(results);
        stream.handshake(boost::asio::ssl::stream_base::client);

        http::request<http::string_body> req{http::verb::post, target, 11};
        req.set(http::field::host, host);
        req.set(http::field::user_agent, "PolymarketBot/1.0");
        req.set(http::field::content_type, "application/json");
        if (!api_key_.empty()) {
            std::string timestamp = std::to_string(std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count());
            std::string signature = compute_hmac_signature(timestamp, "POST", target, payload);

            req.set("POLY_API_KEY", api_key_);
            req.set("POLY_PASSPHRASE", api_passphrase_);
            req.set("POLY_TIMESTAMP", timestamp);
            req.set("POLY_SIGNATURE", signature);
            req.set("POLY_ADDRESS", signer_address_);
        }
        req.body() = payload;
        req.prepare_payload();

        http::write(stream, req);

        beast::flat_buffer buffer;
        http::response<http::string_body> res;
        http::read(stream, buffer, res);

        double target_price = 0.0;
        double size_shares = 0.0;
        if (order.side == 0) {
            target_price = std::stod(order.makerAmount) / std::stod(order.takerAmount);
            size_shares = std::stod(order.takerAmount) / 1000000.0;
        } else {
            target_price = std::stod(order.takerAmount) / std::stod(order.makerAmount);
            size_shares = std::stod(order.makerAmount) / 1000000.0;
        }

        beast::error_code ec;
        stream.shutdown(ec);

        if (res.result() != http::status::ok && res.result() != http::status::created) {
            spdlog::error("[LIVE EXEC] Order REJECTED: {} | Body: {}", res.result_int(), res.body());
            return result;
        }

        auto response_json = boost::json::parse(res.body()).as_object();
        spdlog::info("[LIVE EXEC] Response: {}", res.body());

        const bool success = !response_json.contains("success") || response_json.at("success").as_bool();
        std::string error_msg;
        if (response_json.contains("errorMsg") && response_json.at("errorMsg").is_string()) {
            error_msg = std::string(response_json.at("errorMsg").as_string());
        }
        if (!success || !error_msg.empty()) {
            spdlog::error("[LIVE EXEC] Order rejected by CLOB: {}", error_msg.empty() ? "success=false" : error_msg);
            return result;
        }

        std::string status;
        if (response_json.contains("status") && response_json.at("status").is_string()) {
            status = std::string(response_json.at("status").as_string());
        }
        if (status == "unmatched") {
            spdlog::warn("[LIVE EXEC] FAK unmatched — no liquidity");
            return result;
        }

        double actual_price = target_price;
        double filled_size = 0.0;

        if (response_json.contains("price")) {
            actual_price = std::stod(std::string(response_json["price"].as_string()));
        }
        if (response_json.contains("size_matched")) {
            filled_size = parse_matched_size(response_json.at("size_matched"));
        } else if (response_json.contains("sizeMatched")) {
            filled_size = parse_matched_size(response_json.at("sizeMatched"));
        } else if (status == "matched" || status == "filled") {
            filled_size = size_shares;
        }

        const std::string order_id = extract_order_id(response_json);
        if (!order_id.empty()) {
            auto polled = poll_order_fill(order_id, actual_price, size_shares);
            if (polled.ok) {
                if (polled.price > 0.0) actual_price = polled.price;
                if (polled.size_shares > 0.0) filled_size = polled.size_shares;
                if (polled.status == "unmatched" || polled.status == "cancelled") {
                    spdlog::warn("[LIVE EXEC] Order {} terminal status={} after poll", order_id.substr(0, 16), polled.status);
                    return result;
                }
            }
        }

        if (filled_size <= 0) {
            spdlog::warn("[LIVE EXEC] 0 size matched after poll");
            return result;
        }

        result.success = true;
        result.price = actual_price;
        result.size_shares = filled_size;

        if (!register_position) {
            return result;
        }

        if (order.side == 0) {
            risk::Position pos;
            pos.order_id = "live_" + order.salt;
            pos.token_id = order.tokenId;
            pos.market_question = question;
            pos.side = "BUY";
            pos.entry_price = actual_price;
            pos.size_shares = filled_size;
            pos.cost_usdc = actual_price * filled_size;
            pos.opened_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
            pos.end_date_ts = end_date_ts;
            pos.asset = asset;
            pos.strategy = strategy;
            pos.paper_mode = false;
            pos.is_neg_risk = is_neg_risk;

            risk_manager_.register_trade_open(pos);
            spdlog::info("[LIVE EXEC] BUY FILLED | {} | {:.4f} x {:.2f}", asset, actual_price, filled_size);
        } else {
            risk_manager_.register_trade_close(original_order_id, actual_price);
            spdlog::info("[LIVE EXEC] SELL FILLED | {} | {:.4f}", asset, actual_price);
        }
        return result;
    } catch (const std::exception& e) {
        spdlog::error("[LIVE EXEC] Network error: {}", e.what());
        return result;
    }
}

double OrderRouter::parse_matched_size(const boost::json::value& raw) const {
    double sm = 0.0;
    if (raw.is_string()) sm = std::stod(std::string(raw.as_string()));
    else if (raw.is_double()) sm = raw.as_double();
    else if (raw.is_int64()) sm = static_cast<double>(raw.as_int64());
    else if (raw.is_uint64()) sm = static_cast<double>(raw.as_uint64());
    if (sm > 1000.0) sm /= 1000000.0;
    return sm;
}

std::string OrderRouter::extract_order_id(const boost::json::object& obj) const {
    auto pick = [&](const char* key) -> std::string {
        if (!obj.contains(key)) return "";
        const auto& v = obj.at(key);
        if (v.is_string()) return std::string(v.as_string());
        return "";
    };
    std::string id = pick("orderID");
    if (id.empty()) id = pick("orderId");
    if (id.empty()) id = pick("id");
    return id;
}

std::string OrderRouter::authenticated_http_get(const std::string& target) {
    namespace beast = boost::beast;
    namespace http = beast::http;

    std::lock_guard<std::mutex> lock(http_mutex_);

    const std::string host = "clob.polymarket.com";
    boost::asio::ip::tcp::resolver resolver(ioc_);
    beast::ssl_stream<beast::tcp_stream> stream(ioc_, ctx_);

    if (!SSL_set_tlsext_host_name(stream.native_handle(), host.c_str())) {
        throw std::runtime_error("Failed to set SNI hostname");
    }

    auto const results = resolver.resolve(host, "443");
    beast::get_lowest_layer(stream).connect(results);
    stream.handshake(boost::asio::ssl::stream_base::client);

    http::request<http::string_body> req{http::verb::get, target, 11};
    req.set(http::field::host, host);
    req.set(http::field::user_agent, "PolymarketBot/1.0");
    if (!api_key_.empty()) {
        const std::string timestamp = std::to_string(std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
        const std::string signature = compute_hmac_signature(timestamp, "GET", target, "");
        req.set("POLY_API_KEY", api_key_);
        req.set("POLY_PASSPHRASE", api_passphrase_);
        req.set("POLY_TIMESTAMP", timestamp);
        req.set("POLY_SIGNATURE", signature);
        req.set("POLY_ADDRESS", signer_address_);
    }

    http::write(stream, req);
    beast::flat_buffer buffer;
    http::response<http::string_body> res;
    http::read(stream, buffer, res);
    beast::error_code ec;
    stream.shutdown(ec);

    if (res.result() != http::status::ok) {
        throw std::runtime_error("GET " + target + " HTTP " + std::to_string(res.result_int()));
    }
    return res.body();
}

OrderRouter::PolledFill OrderRouter::poll_order_fill(
    const std::string& order_id, double fallback_price, double requested_shares)
{
    PolledFill out;
    out.price = fallback_price;

    constexpr int kMaxAttempts = 5;
    for (int attempt = 0; attempt < kMaxAttempts; ++attempt) {
        if (attempt > 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
        }
        try {
            const std::string body = authenticated_http_get("/order/" + order_id);
            const auto obj = boost::json::parse(body).as_object();

            if (obj.contains("status") && obj.at("status").is_string()) {
                out.status = std::string(obj.at("status").as_string());
            }
            if (obj.contains("price")) {
                const auto& pv = obj.at("price");
                if (pv.is_string()) out.price = std::stod(std::string(pv.as_string()));
                else if (pv.is_double()) out.price = pv.as_double();
            }
            if (obj.contains("size_matched")) {
                out.size_shares = parse_matched_size(obj.at("size_matched"));
            } else if (obj.contains("sizeMatched")) {
                out.size_shares = parse_matched_size(obj.at("sizeMatched"));
            } else if (out.status == "matched" || out.status == "filled") {
                out.size_shares = requested_shares;
            }

            out.ok = true;
            if (out.size_shares > 0.0) {
                spdlog::debug("[LIVE EXEC] poll {} attempt {} | status={} size={:.4f}",
                              order_id.substr(0, 16), attempt + 1, out.status, out.size_shares);
                return out;
            }
            if (out.status == "unmatched" || out.status == "cancelled" || out.status == "expired") {
                return out;
            }
        } catch (const std::exception& e) {
            spdlog::debug("[LIVE EXEC] poll {} attempt {} failed: {}", order_id.substr(0, 16), attempt + 1, e.what());
        }
    }
    return out;
}

EIP712Signer& OrderRouter::pick_signer(bool is_neg_risk) const {
    if (is_neg_risk && signer_neg_risk_) {
        return *signer_neg_risk_;
    }
    return *signer_;
}

std::string OrderRouter::generate_salt() const {
    static std::mutex salt_mutex;
    static std::mt19937 gen(std::random_device{}());
    static std::uniform_int_distribution<uint32_t> dis;
    std::lock_guard<std::mutex> lock(salt_mutex);
    return std::to_string(dis(gen));
}

std::string OrderRouter::compute_hmac_signature(const std::string& timestamp, const std::string& method, const std::string& path, const std::string& body) {
    std::string message = timestamp + method + path + body;
    auto decoded_secret = base64_decode(api_secret_);
    
    unsigned char hash[EVP_MAX_MD_SIZE];
    unsigned int hash_len;
    
    HMAC(EVP_sha256(), decoded_secret.data(), decoded_secret.size(),
         reinterpret_cast<const unsigned char*>(message.c_str()), message.length(),
         hash, &hash_len);
    
    return base64_encode(hash, hash_len);
}

std::string OrderRouter::base64_encode(const unsigned char* input, int length) {
    BIO *bio, *b64;
    BUF_MEM *bufferPtr;

    b64 = BIO_new(BIO_f_base64());
    bio = BIO_new(BIO_s_mem());
    bio = BIO_push(b64, bio);

    BIO_set_flags(bio, BIO_FLAGS_BASE64_NO_NL);
    BIO_write(bio, input, length);
    BIO_flush(bio);
    BIO_get_mem_ptr(bio, &bufferPtr);
    BIO_set_close(bio, BIO_NOCLOSE);

    std::string result(bufferPtr->data, bufferPtr->length);
    BIO_free_all(bio);

    return result;
}

std::vector<unsigned char> OrderRouter::base64_decode(const std::string& input) {
    BIO *bio, *b64;
    int decodeLen = calc_decode_length(input);
    std::vector<unsigned char> buffer(decodeLen);

    bio = BIO_new_mem_buf(input.c_str(), -1);
    b64 = BIO_new(BIO_f_base64());
    bio = BIO_push(b64, bio);

    BIO_set_flags(bio, BIO_FLAGS_BASE64_NO_NL);
    int actualLen = BIO_read(bio, buffer.data(), static_cast<int>(input.length()));
    buffer.resize(actualLen);
    BIO_free_all(bio);

    return buffer;
}

int OrderRouter::calc_decode_length(const std::string& b64input) {
    int len = static_cast<int>(b64input.size());
    int padding = 0;

    if (len > 0 && b64input[len-1] == '=' && len > 1 && b64input[len-2] == '=')
        padding = 2;
    else if (len > 0 && b64input[len-1] == '=')
        padding = 1;

    return (len * 3) / 4 - padding;
}

} // namespace exec
} // namespace trading
