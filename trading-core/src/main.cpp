#include <iostream>
#include <fstream>
#include <string>
#include <unordered_set>
#include <spdlog/spdlog.h>
#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>
#include <thread>
#include <chrono>
#include <mutex>
#include <algorithm>
#include "state/StateStore.h"
#include "feeds/BinanceFeed.h"
#include "feeds/PolymarketFeed.h"
#include "feeds/GammaClient.h"
#include "risk/RiskManager.h"
#include "signals/DumpHedgeDetector.h"
#include "exec/OrderRouter.h"
#include "state/PaperStateStore.h"
#include <spdlog/sinks/basic_file_sink.h>
#include <fmt/core.h>
#include <atomic>
#include <boost/beast.hpp>
#include <boost/beast/ssl.hpp>
#include <boost/json.hpp>

using namespace trading;
namespace beast = boost::beast;
namespace http = beast::http;
namespace net = boost::asio;
namespace ssl = net::ssl;
using tcp = net::ip::tcp;

// Query USDC balance from Polygon RPC (on-chain)
double fetch_usdc_balance_for_contract(const std::string& funder_address, const std::string& usdc_contract, const std::string& label) {
    try {
        std::string addr = funder_address;
        if (addr.substr(0, 2) == "0x") addr = addr.substr(2);
        // Lowercase the address for consistency
        std::transform(addr.begin(), addr.end(), addr.begin(), ::tolower);
        std::string padded_addr = std::string(64 - addr.size(), '0') + addr;
        std::string call_data = "0x70a08231" + padded_addr;

        boost::json::object rpc_req;
        rpc_req["jsonrpc"] = "2.0";
        rpc_req["method"] = "eth_call";
        rpc_req["id"] = 1;
        boost::json::object call_obj;
        call_obj["to"] = usdc_contract;
        call_obj["data"] = call_data;
        rpc_req["params"] = boost::json::array{call_obj, "latest"};
        std::string body = boost::json::serialize(rpc_req);

        net::io_context ioc;
        ssl::context ctx{ssl::context::sslv23_client};
        ctx.set_default_verify_paths();
        tcp::resolver resolver(ioc);
        beast::ssl_stream<beast::tcp_stream> stream(ioc, ctx);
        
        if (!SSL_set_tlsext_host_name(stream.native_handle(), "polygon-rpc.com")) {
            return -1;
        }
        auto const results = resolver.resolve("polygon-rpc.com", "443");
        beast::get_lowest_layer(stream).connect(results);
        stream.handshake(ssl::stream_base::client);

        http::request<http::string_body> req{http::verb::post, "/", 11};
        req.set(http::field::host, "polygon-rpc.com");
        req.set(http::field::content_type, "application/json");
        req.body() = body;
        req.prepare_payload();
        http::write(stream, req);

        beast::flat_buffer buffer;
        http::response<http::string_body> res;
        http::read(stream, buffer, res);

        auto jv = boost::json::parse(res.body());
        auto& obj = jv.as_object();
        
        // Check for RPC errors
        if (obj.contains("error")) {
            spdlog::warn("RPC error for {}: {}", label, res.body());
            beast::error_code ec;
            stream.shutdown(ec);
            return -1;
        }
        
        if (!obj.contains("result")) {
            spdlog::warn("RPC response missing 'result' for {}: {}", label, res.body());
            beast::error_code ec;
            stream.shutdown(ec);
            return -1;
        }
        
        std::string hex_result = std::string(obj.at("result").as_string());
        
        // Handle "0x" or empty results
        if (hex_result.empty() || hex_result == "0x" || hex_result == "0x0") {
            beast::error_code ec;
            stream.shutdown(ec);
            return 0.0;
        }
        
        unsigned long long raw_balance = std::stoull(hex_result, nullptr, 16);
        double balance = static_cast<double>(raw_balance) / 1000000.0;
        
        beast::error_code ec;
        stream.shutdown(ec);
        return balance;
    } catch (const std::exception& e) {
        spdlog::error("Failed to fetch {} balance: {}", label, e.what());
        return -1;
    }
}

double fetch_usdc_balance(const std::string& funder_address) {
    // pUSD (V2 collateral), USDC.e (legacy), native USDC
    double bal_pusd = fetch_usdc_balance_for_contract(funder_address, "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb", "pUSD");
    if (bal_pusd >= 0) {
        spdlog::info("pUSD balance: ${:.2f}", bal_pusd);
    }

    double bal = fetch_usdc_balance_for_contract(funder_address, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "USDC.e");
    if (bal >= 0) {
        spdlog::info("USDC.e balance: ${:.2f}", bal);
    }

    double bal2 = fetch_usdc_balance_for_contract(funder_address, "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "USDC");
    if (bal2 >= 0) {
        spdlog::info("USDC (native) balance: ${:.2f}", bal2);
    }

    double total = 0;
    if (bal_pusd >= 0) total += bal_pusd;
    if (bal >= 0) total += bal;
    if (bal2 >= 0) total += bal2;

    if (bal_pusd < 0 && bal < 0 && bal2 < 0) return -1;
    return total;
}

std::unordered_map<std::string, std::string> load_env(const std::string& filepath) {
    std::unordered_map<std::string, std::string> env;
    std::ifstream file(filepath);
    if (!file.is_open()) {
        spdlog::warn("Could not open {} - using defaults", filepath);
        return env;
    }
    std::string line;
    while (std::getline(file, line)) {
        if (line.empty() || line[0] == '#') continue;
        auto pos = line.find('=');
        if (pos != std::string::npos) {
            std::string key = line.substr(0, pos);
            std::string val = line.substr(pos + 1);
            key.erase(0, key.find_first_not_of(" \t\r\n"));
            key.erase(key.find_last_not_of(" \t\r\n") + 1);
            val.erase(0, val.find_first_not_of(" \t\r\n"));
            val.erase(val.find_last_not_of(" \t\r\n") + 1);
            if (val.size() >= 2 && ((val.front() == '"' && val.back() == '"') || (val.front() == '\'' && val.back() == '\''))) {
                val = val.substr(1, val.size() - 2);
            }
            env[key] = val;
        }
    }
    return env;
}

static std::unordered_set<std::string> g_redeem_triggered;
static std::mutex g_redeem_mutex;

static bool env_flag_true(const std::unordered_map<std::string, std::string>& env, const std::string& key, bool default_val) {
    auto it = env.find(key);
    if (it == env.end()) return default_val;
    std::string v = it->second;
    std::transform(v.begin(), v.end(), v.begin(), ::tolower);
    if (v == "false" || v == "0" || v == "no" || v == "off") return false;
    if (v == "true" || v == "1" || v == "yes" || v == "on") return true;
    return default_val;
}

static void sync_live_balance(risk::RiskManager& risk_manager) {
#ifdef _WIN32
    FILE* pipe = popen("python fetch_balance.py", "r");
#else
    FILE* pipe = popen("python3 fetch_balance.py 2>/dev/null", "r");
#endif
    if (!pipe) return;
    char buf[128];
    if (fgets(buf, sizeof(buf), pipe)) {
        try {
            double new_bal = std::stod(std::string(buf));
            if (new_bal > 0) risk_manager.update_balance(new_bal);
        } catch (...) {}
    }
    pclose(pipe);
}

static void attempt_onchain_redeem_async(
    const std::string& condition_id,
    const std::string& dh_id,
    StateStore& store,
    risk::RiskManager& risk_manager
) {
    if (condition_id.empty() || condition_id.size() < 10) {
        spdlog::warn("Redeem skipped for {} — missing condition_id", dh_id);
        return;
    }

    {
        std::lock_guard<std::mutex> lock(g_redeem_mutex);
        if (g_redeem_triggered.count(condition_id)) return;
        g_redeem_triggered.insert(condition_id);
    }

    std::thread([condition_id, dh_id, &store, &risk_manager]() {
        spdlog::info("AUTO-REDEEM starting | {} | condition {}", dh_id, condition_id.substr(0, 18));
        store.push_telemetry(fmt::format("REDEEM START {} | {}", dh_id, condition_id.substr(0, 18)));

#ifdef _WIN32
        std::string cmd = "python redeem_positions.py \"" + condition_id + "\"";
#else
        std::string cmd = "python3 redeem_positions.py \"" + condition_id + "\" 2>/dev/null";
#endif
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) {
            spdlog::error("AUTO-REDEEM popen failed for {}", dh_id);
            return;
        }

        std::string output;
        char buf[512];
        while (fgets(buf, sizeof(buf), pipe)) {
            output += buf;
        }
        pclose(pipe);

        try {
            auto jv = boost::json::parse(output);
            auto obj = jv.as_object();
            bool ok = obj.contains("success") && obj.at("success").as_bool();
            std::string msg = obj.contains("message") ? std::string(obj.at("message").as_string()) : "";
            std::string tx = obj.contains("tx_hash") && obj.at("tx_hash").is_string()
                ? std::string(obj.at("tx_hash").as_string()) : "";

            if (ok) {
                spdlog::info("AUTO-REDEEM OK | {} | tx {}", dh_id, tx.empty() ? "n/a" : tx.substr(0, 20));
                store.push_telemetry(fmt::format("REDEEM OK {} | tx {}", dh_id, tx.empty() ? "n/a" : tx.substr(0, 18)));
                sync_live_balance(risk_manager);
            } else {
                spdlog::critical("AUTO-REDEEM FAILED | {} | {}", dh_id, msg);
                store.push_telemetry(fmt::format("REDEEM FAIL {} | {}", dh_id, msg));
            }
        } catch (const std::exception& e) {
            spdlog::error("AUTO-REDEEM parse error | {} | raw: {}", dh_id, output.substr(0, 200));
        }
    }).detach();
}

void check_and_close_dh_positions(
    risk::RiskManager& risk_manager,
    StateStore& store,
    bool auto_redeem_enabled
) {
    auto now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();

    auto open_dh = risk_manager.get_open_dh_positions();
    for (const auto& [id, p] : open_dh) {
        if (now < p.end_date_ts) continue;

        std::string condition_id = p.condition_id;
        bool is_live = !p.paper_mode;

        auto live_y = store.get_token_price(p.yes_token_id);
        auto live_n = store.get_token_price(p.no_token_id);

        bool use_structural = false;
        double ey = 0.0;
        double en = 0.0;

        if (!live_y || !live_n) {
            use_structural = true;
        } else {
            ey = (live_y->price >= 0.5) ? 1.0 : 0.0;
            en = (live_n->price >= 0.5) ? 1.0 : 0.0;
            if (ey + en != 1.0) {
                use_structural = true;
            }
        }

        if (use_structural) {
            double proceeds = p.combined_cost_usdc + p.locked_profit_usdc;
            risk_manager.register_dh_close(
                id,
                p.yes_entry_price,
                p.no_entry_price,
                "Market resolved (structural)",
                std::nullopt,
                proceeds);
            store.push_telemetry(fmt::format(
                "SETTLED {} DH RESOLVED | PnL ${:+.2f} (locked) | {}",
                p.asset, p.locked_profit_usdc, p.market_question));
            spdlog::info(
                "DH expiry structural settle | {} | proceeds ${:.2f} | locked ${:.2f}",
                id, proceeds, p.locked_profit_usdc);
        } else {
            risk_manager.register_dh_close(id, ey, en, "EXPIRED");
            store.push_telemetry(fmt::format(
                "SETTLED {} DH @ YES={:.0f} NO={:.0f} | {}",
                p.asset, ey, en, p.market_question));
        }

        if (is_live && auto_redeem_enabled && !condition_id.empty()) {
            attempt_onchain_redeem_async(condition_id, id, store, risk_manager);
        } else if (is_live && condition_id.empty()) {
            spdlog::warn("Live DH {} closed without condition_id — cannot auto-redeem", id);
        }
    }
}

static bool parse_config_bool(const std::string& v) {
    std::string lower = v;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
    return !(lower == "false" || lower == "0" || lower == "no" || lower == "off");
}

static bool apply_dh_asset_config(StateStore& store, const std::string& k, const std::string& v) {
    const bool enabled = parse_config_bool(v);
    if (k == "DH_ENABLE_5M_BTC") {
        store.set_dh_asset_enabled(5, "btc", enabled);
        store.push_telemetry(fmt::format("CONFIG DH_ENABLE_5M_BTC={}", enabled ? "true" : "false"));
        return true;
    }
    if (k == "DH_ENABLE_5M_ETH") {
        store.set_dh_asset_enabled(5, "eth", enabled);
        store.push_telemetry(fmt::format("CONFIG DH_ENABLE_5M_ETH={}", enabled ? "true" : "false"));
        return true;
    }
    if (k == "DH_ENABLE_5M_SOL") {
        store.set_dh_asset_enabled(5, "sol", enabled);
        store.push_telemetry(fmt::format("CONFIG DH_ENABLE_5M_SOL={}", enabled ? "true" : "false"));
        return true;
    }
    if (k == "DH_ENABLE_15M_BTC") {
        store.set_dh_asset_enabled(15, "btc", enabled);
        store.push_telemetry(fmt::format("CONFIG DH_ENABLE_15M_BTC={}", enabled ? "true" : "false"));
        return true;
    }
    if (k == "DH_ENABLE_15M_ETH") {
        store.set_dh_asset_enabled(15, "eth", enabled);
        store.push_telemetry(fmt::format("CONFIG DH_ENABLE_15M_ETH={}", enabled ? "true" : "false"));
        return true;
    }
    return false;
}

static void apply_runtime_config(
    const std::string& path,
    risk::RiskManager& risk_manager,
    StateStore& store,
    std::mutex& detector_mutex,
    std::unique_ptr<DumpHedgeDetector>& dh_detector
) {
    std::ifstream file(path);
    if (!file.is_open()) return;

    std::string content((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    file.close();

    boost::json::value jv;
    try {
        jv = boost::json::parse(content);
    } catch (const std::exception& e) {
        spdlog::warn("Runtime config parse error: {}", e.what());
        std::remove(path.c_str());
        return;
    }

    auto& obj = jv.as_object();

    if (obj.contains("control") && obj.at("control").is_string()) {
        std::string action = std::string(obj.at("control").as_string());
        std::string reason = obj.contains("reason") ? std::string(obj.at("reason").as_string()) : "Web control";
        if (action == "pause") {
            risk_manager.pause(reason);
            store.push_telemetry(fmt::format("CONFIG PAUSE | {}", reason));
        } else if (action == "resume") {
            if (risk_manager.resume()) {
                store.push_telemetry("CONFIG RESUME | trading enabled");
            }
        } else if (action == "reset_kill") {
            if (risk_manager.reset_kill_switch(true)) {
                store.push_telemetry("CONFIG RESET_KILL | kill switch cleared");
            }
        }
    }

    if (obj.contains("patch") && obj.at("patch").is_object()) {
        const auto& patch = obj.at("patch").as_object();
        double sum_target = store.get_dh_sum_target();
        double min_discount = store.get_dh_min_discount();
        double cooldown = store.get_dh_cooldown_seconds();
        double min_secs = store.get_dh_min_seconds_remaining();
        bool dh_changed = false;

        for (const auto& [key, val] : patch) {
            if (!val.is_string()) continue;
            std::string k = std::string(key);
            std::string v = std::string(val.as_string());
            try {
                if (k == "RISK_MAX_POSITION_FRACTION") {
                    risk_manager.set_max_position_fraction(std::stod(v));
                    store.push_telemetry(fmt::format("CONFIG RISK_MAX_POSITION_FRACTION={}", v));
                } else if (k == "RISK_DAILY_LOSS_LIMIT") {
                    risk_manager.set_daily_loss_limit(std::stod(v));
                    store.push_telemetry(fmt::format("CONFIG RISK_DAILY_LOSS_LIMIT={}", v));
                } else if (k == "RISK_TOTAL_DRAWDOWN_KILL") {
                    risk_manager.set_total_drawdown_kill(std::stod(v));
                    store.push_telemetry(fmt::format("CONFIG RISK_TOTAL_DRAWDOWN_KILL={}", v));
                } else if (k == "RISK_MAX_CONCURRENT_POSITIONS") {
                    risk_manager.set_max_concurrent_positions(std::stoi(v));
                    store.push_telemetry(fmt::format("CONFIG RISK_MAX_CONCURRENT_POSITIONS={}", v));
                } else if (k == "FEE_RATE") {
                    double fr = std::stod(v);
                    risk_manager.set_fee_rate(fr);
                    store.set_fee_rate(fr);
                    store.push_telemetry(fmt::format("CONFIG FEE_RATE={}", v));
                } else if (k == "DH_SUM_TARGET") {
                    sum_target = std::stod(v);
                    dh_changed = true;
                } else if (k == "DH_MIN_DISCOUNT") {
                    min_discount = std::stod(v);
                    dh_changed = true;
                } else if (k == "DH_COOLDOWN_SECONDS") {
                    cooldown = std::stod(v);
                    dh_changed = true;
                } else if (k == "DH_MIN_SECONDS_REMAINING") {
                    min_secs = std::stod(v);
                    dh_changed = true;
                } else if (k == "BINANCE_FEED_ENABLED") {
                    bool enabled = parse_config_bool(v);
                    store.set_binance_feed_enabled(enabled);
                    store.push_telemetry(fmt::format("CONFIG BINANCE_FEED_ENABLED={}", enabled ? "true" : "false"));
                } else if (k == "DH_ENABLE_5M") {
                    bool enabled = parse_config_bool(v);
                    store.set_dh_window_enabled(enabled, store.dh_enable_15m());
                    store.push_telemetry(fmt::format("CONFIG DH_ENABLE_5M={}", enabled ? "true" : "false"));
                } else if (k == "DH_ENABLE_15M") {
                    bool enabled = parse_config_bool(v);
                    store.set_dh_window_enabled(store.dh_enable_5m(), enabled);
                    store.push_telemetry(fmt::format("CONFIG DH_ENABLE_15M={}", enabled ? "true" : "false"));
                } else if (apply_dh_asset_config(store, k, v)) {
                }
            } catch (const std::exception& e) {
                spdlog::warn("Failed to apply config {}={}: {}", k, v, e.what());
            }
        }

        if (dh_changed) {
            store.set_dh_config(sum_target, min_discount);
            store.set_dh_timing(cooldown, min_secs);
            std::lock_guard<std::mutex> lock(detector_mutex);
            if (dh_detector) {
                dh_detector->set_sum_target(sum_target);
                dh_detector->set_min_discount(min_discount);
                dh_detector->set_cooldown_seconds(cooldown);
                dh_detector->set_min_seconds_remaining(min_secs);
            }
            store.push_telemetry(fmt::format(
                "CONFIG DH | sum<={:.3f} disc>={:.3f} cd={:.0f}s min_rem={:.0f}s",
                sum_target, min_discount, cooldown, min_secs));
        }
    }

    std::remove(path.c_str());
}

int main() {
    try {
        auto file_sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>("bot.log", true);
        auto logger = std::make_shared<spdlog::logger>("bot", file_sink);
        spdlog::set_default_logger(logger);
        spdlog::set_level(spdlog::level::debug);
        spdlog::set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%l] %v");

        auto env = load_env(".env");
        bool paper_mode = true;
        if (env.count("PAPER_MODE")) {
            std::string pm = env["PAPER_MODE"];
            std::transform(pm.begin(), pm.end(), pm.begin(), ::tolower);
            if (pm == "false" || pm == "0") paper_mode = false;
        }
        
        std::string polymarket_host = env.count("POLYMARKET_HOST") ? env["POLYMARKET_HOST"] : "https://clob.polymarket.com";
        std::string polymarket_chain_id = env.count("POLYMARKET_CHAIN_ID") ? env["POLYMARKET_CHAIN_ID"] : "137";
        std::string polymarket_signer = env.count("POLYMARKET_SIGNER") ? env["POLYMARKET_SIGNER"] : "";
        std::string polymarket_funder = env.count("POLYMARKET_FUNDER") ? env["POLYMARKET_FUNDER"] : "";
        
        // Default signer to funder if missing, or vice-versa
        if (polymarket_signer.empty() && !polymarket_funder.empty()) polymarket_signer = polymarket_funder;
        if (polymarket_funder.empty() && !polymarket_signer.empty()) polymarket_funder = polymarket_signer;

        double starting_balance = 1000.0;
        if (paper_mode && env.count("PAPER_STARTING_BALANCE")) {
            starting_balance = std::stod(env["PAPER_STARTING_BALANCE"]);
        } else if (!paper_mode) {
            // Auto-detect balance via Python SDK (most reliable method)
            starting_balance = 0.0;
            spdlog::info("Fetching Polymarket balance via SDK...");
#ifdef _WIN32
            FILE* pipe = popen("python fetch_balance.py", "r");
#else
            FILE* pipe = popen("python3 fetch_balance.py 2>/dev/null", "r");
#endif
            if (pipe) {
                char buf[128];
                if (fgets(buf, sizeof(buf), pipe)) {
                    try {
                        starting_balance = std::stod(std::string(buf));
                        spdlog::info("Detected Polymarket balance: ${:.2f}", starting_balance);
                    } catch (...) {
                        spdlog::warn("Could not parse balance output: {}", buf);
                    }
                }
                pclose(pipe);
            }
            if (starting_balance <= 0) {
                spdlog::info("SDK returned $0.00, falling back to on-chain RPC for funder address...");
                starting_balance = fetch_usdc_balance(polymarket_funder);
            }
            if (starting_balance <= 0) {
                spdlog::warn("Polymarket balance is $0.00. Deposit pUSD/USDC to the proxy wallet to trade.");
            }
        }

        std::string polymarket_pk = env.count("POLYMARKET_PRIVATE_KEY") ? env["POLYMARKET_PRIVATE_KEY"] : "";
        if (!paper_mode) {
            if (polymarket_pk.empty() ||
                polymarket_pk == "0x0000000000000000000000000000000000000000000000000000000000000001" ||
                polymarket_pk == "0xYourWalletPrivateKey") {
                spdlog::critical("[FATAL] Live mode requires a valid POLYMARKET_PRIVATE_KEY in .env");
                return 1;
            }
            if (polymarket_funder.empty() || polymarket_funder == "0xYourPolygonWalletAddress") {
                spdlog::critical("[FATAL] Live mode requires POLYMARKET_FUNDER in .env");
                return 1;
            }
        } else if (polymarket_pk.empty()) {
            polymarket_pk = "0x0000000000000000000000000000000000000000000000000000000000000001";
        }
        // V2 Exchange addresses (April 2026 migration)
        const std::string V2_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B";
        const std::string V2_NEG_RISK = "0xe2222d279d744050d28e00520010520000310F59";
        
        std::string verifying_contract = V2_EXCHANGE;

        bool auto_redeem = !paper_mode && env_flag_true(env, "AUTO_REDEEM", true);
        bool live_dh_dry_run = !paper_mode && env_flag_true(env, "LIVE_DH_DRY_RUN", false);

        spdlog::info("Starting Core v3.0 (DH-only) | Mode: {} | Bal: ${:.2f} | Auto-redeem: {} | DH dry-run: {}",
                     paper_mode ? "PAPER" : "LIVE", starting_balance,
                     auto_redeem ? "on" : "off",
                     live_dh_dry_run ? "on" : "off");

        boost::asio::io_context feed_ioc;
        boost::asio::ssl::context feed_ctx{boost::asio::ssl::context::sslv23_client};
        feed_ctx.set_default_verify_paths();

        boost::asio::io_context gamma_ioc;
        boost::asio::ssl::context gamma_ctx{boost::asio::ssl::context::sslv23_client};
        gamma_ctx.set_default_verify_paths();

        double max_pos = env.count("RISK_MAX_POSITION_FRACTION") ? std::stod(env["RISK_MAX_POSITION_FRACTION"]) : 0.08;
        double daily_loss = env.count("RISK_DAILY_LOSS_LIMIT") ? std::stod(env["RISK_DAILY_LOSS_LIMIT"]) : 0.20;
        double drawdown = env.count("RISK_TOTAL_DRAWDOWN_KILL") ? std::stod(env["RISK_TOTAL_DRAWDOWN_KILL"]) : 0.40;
        int max_concurrent = env.count("RISK_MAX_CONCURRENT_POSITIONS") ? std::stoi(env["RISK_MAX_CONCURRENT_POSITIONS"]) : 3;
        double min_order = env.count("MIN_ORDER_SIZE") ? std::stod(env["MIN_ORDER_SIZE"]) : 5.0;
        double fee_rate = env.count("FEE_RATE") ? std::stod(env["FEE_RATE"]) : 0.018;
        double dh_sum_target = env.count("DH_SUM_TARGET") ? std::stod(env["DH_SUM_TARGET"]) : 0.95;
        double dh_min_discount = env.count("DH_MIN_DISCOUNT") ? std::stod(env["DH_MIN_DISCOUNT"]) : 0.03;
        double dh_cooldown = env.count("DH_COOLDOWN_SECONDS") ? std::stod(env["DH_COOLDOWN_SECONDS"]) : 30.0;
        double dh_min_secs = env.count("DH_MIN_SECONDS_REMAINING") ? std::stod(env["DH_MIN_SECONDS_REMAINING"]) : 60.0;

        const std::string strategy = "dump_hedge";

        bool binance_feed_enabled = true;
        if (env.count("BINANCE_FEED_ENABLED")) {
            std::string bf = env["BINANCE_FEED_ENABLED"];
            std::transform(bf.begin(), bf.end(), bf.begin(), ::tolower);
            binance_feed_enabled = !(bf == "false" || bf == "0" || bf == "no" || bf == "off");
        }

        spdlog::info("Strategy: DH only | DH sum<={:.2f} disc>={:.2f} | Binance chart: {}",
                     dh_sum_target, dh_min_discount, binance_feed_enabled ? "on" : "off");

        std::string poly_api_key = env.count("POLY_API_KEY") ? env["POLY_API_KEY"] : "";
        std::string poly_api_secret = env.count("POLY_API_SECRET") ? env["POLY_API_SECRET"] : "";
        std::string poly_api_passphrase = env.count("POLY_PASSPHRASE") ? env["POLY_PASSPHRASE"] : "";
        std::string neg_risk_exchange = V2_NEG_RISK;

        if (!paper_mode && poly_api_key.empty()) {
            spdlog::critical("[FATAL] Live trading enabled but POLY_API_KEY is missing!");
            spdlog::critical("Please run 'python derive_and_update_keys.py' first to generate API credentials.");
            return 1;
        }

        StateStore store;
        store.set_paper_mode(paper_mode);
        if (!paper_mode) {
            store.push_telemetry(fmt::format("💰 BALANCE SYNCED | ${:.2f}", starting_balance));
        }
        risk::RiskManager risk_manager(starting_balance, max_pos, daily_loss, drawdown, max_concurrent, true, 3, 5, 0.02, 300.0, min_order);
        risk_manager.set_fee_rate(fee_rate);

        bool paper_state_persist = true;
        if (env.count("PAPER_STATE_PERSIST")) {
            std::string ps = env["PAPER_STATE_PERSIST"];
            std::transform(ps.begin(), ps.end(), ps.begin(), ::tolower);
            paper_state_persist = !(ps == "false" || ps == "0" || ps == "no" || ps == "off");
        }
        std::string paper_state_path = env.count("PAPER_STATE_PATH") ? env["PAPER_STATE_PATH"] : "logs/paper_state.json";

        if (paper_mode && paper_state_persist) {
            if (persistence::load_paper_state(risk_manager, paper_state_path)) {
                spdlog::info("Paper session resumed | Balance: ${:.2f} | Open: {} | DH trades: {}",
                    risk_manager.get_current_balance(),
                    risk_manager.get_open_position_count(),
                    risk_manager.get_total_dh_trades());
                store.push_telemetry(fmt::format("PAPER STATE RESTORED | ${:.2f}", risk_manager.get_current_balance()));
            } else {
                spdlog::info("Paper state: fresh session (no snapshot at {})", paper_state_path);
            }
        }
        int legacy_la = risk_manager.close_legacy_la_positions();
        if (legacy_la > 0) {
            spdlog::warn("Closed {} legacy LA open position(s) — LA strategy removed", legacy_la);
            store.push_telemetry(fmt::format("LEGACY LA CLOSED | {} position(s)", legacy_la));
        }

        store.set_risk_manager(&risk_manager);
        store.set_fee_rate(fee_rate);
        store.set_strategy(strategy);
        store.set_dh_config(dh_sum_target, dh_min_discount);
        store.set_dh_timing(dh_cooldown, dh_min_secs);
        store.set_dh_window_enabled(
            env_flag_true(env, "DH_ENABLE_5M", true),
            env_flag_true(env, "DH_ENABLE_15M", true));
        store.set_dh_asset_enabled(5, "btc", env_flag_true(env, "DH_ENABLE_5M_BTC", true));
        store.set_dh_asset_enabled(5, "eth", env_flag_true(env, "DH_ENABLE_5M_ETH", true));
        store.set_dh_asset_enabled(5, "sol", env_flag_true(env, "DH_ENABLE_5M_SOL", true));
        store.set_dh_asset_enabled(15, "btc", env_flag_true(env, "DH_ENABLE_15M_BTC", true));
        store.set_dh_asset_enabled(15, "eth", env_flag_true(env, "DH_ENABLE_15M_ETH", true));
        store.set_binance_feed_enabled(binance_feed_enabled);

        exec::OrderRouter router(feed_ioc, feed_ctx, store, risk_manager, polymarket_host, polymarket_chain_id, verifying_contract, polymarket_pk, polymarket_signer, polymarket_funder, paper_mode, poly_api_key, poly_api_secret, poly_api_passphrase, neg_risk_exchange, live_dh_dry_run);

        GammaClient gamma(gamma_ioc, gamma_ctx);
        std::shared_ptr<BinanceFeed> btc_feed;
        std::shared_ptr<BinanceFeed> eth_feed;
        std::shared_ptr<BinanceFeed> sol_feed;
        if (binance_feed_enabled) {
            btc_feed = std::make_shared<BinanceFeed>(feed_ioc, feed_ctx, store, "btcusdt");
            eth_feed = std::make_shared<BinanceFeed>(feed_ioc, feed_ctx, store, "ethusdt");
            sol_feed = std::make_shared<BinanceFeed>(feed_ioc, feed_ctx, store, "solusdt");
        }

        auto feed_work = boost::asio::make_work_guard(feed_ioc);
        std::thread feed_thread([&feed_ioc]() { feed_ioc.run(); });

        std::mutex detector_mutex;
        std::unique_ptr<DumpHedgeDetector> dh_detector;

        auto poly_feed = std::make_shared<PolymarketFeed>(feed_ioc, feed_ctx, store);

        poly_feed->set_tick_callback([&](const std::string& /*token_id*/) {
            std::lock_guard<std::mutex> lock(detector_mutex);
            double now_ms = std::chrono::duration<double, std::milli>(std::chrono::system_clock::now().time_since_epoch()).count();
            if (!dh_detector) return;
            auto signal = dh_detector->evaluate(now_ms);
            if (!signal) return;

            for (const auto& [id, p] : risk_manager.get_open_dh_positions()) {
                if (p.asset == signal->asset) return;
            }

            double max_allowed_usdc = risk_manager.get_current_balance() * risk_manager.get_max_position_fraction();
            double size_shares = max_allowed_usdc / signal->combined_price;
            if (!risk_manager.can_open_dh_position(signal->combined_price * size_shares).first) return;

            store.push_signal(fmt::format("DH SIGNAL {} | YES:{:.4f} NO:{:.4f} SUM:{:.4f} DISC:{:.1f}%",
                signal->asset, signal->yes_price, signal->no_price,
                signal->combined_price, signal->discount * 100.0));

            if (!router.submit_dump_hedge_order(*signal, size_shares)) return;
        });

        // Start feeds only after all callbacks are ready
        if (binance_feed_enabled) {
            btc_feed->start();
            eth_feed->start();
            sol_feed->start();
        }
        poly_feed->start();

        std::atomic<bool> is_refreshing{false};
        auto refresh_markets = [&]() {
            if (is_refreshing.exchange(true)) return;
            try {
                std::vector<MarketInfo> all_m;
                auto b5 = gamma.fetch_updown_markets("btc", 5);
                auto e5 = gamma.fetch_updown_markets("eth", 5);
                auto s5 = gamma.fetch_updown_markets("sol", 5);
                auto b15 = gamma.fetch_updown_markets("btc", 15);
                auto e15 = gamma.fetch_updown_markets("eth", 15);
                all_m.insert(all_m.end(), b5.begin(), b5.end());
                all_m.insert(all_m.end(), e5.begin(), e5.end());
                all_m.insert(all_m.end(), s5.begin(), s5.end());
                all_m.insert(all_m.end(), b15.begin(), b15.end());
                all_m.insert(all_m.end(), e15.begin(), e15.end());

                store.update_markets(all_m);
                std::unordered_set<std::string> fee_seen;
                int fee_markets = 0;
                for (const auto& m : all_m) {
                    if (m.condition_id.empty() || fee_seen.count(m.condition_id)) continue;
                    fee_seen.insert(m.condition_id);
                    if (gamma.fetch_and_cache_market_fees(m.condition_id, store)) ++fee_markets;
                }
                {
                    std::lock_guard<std::mutex> lock(detector_mutex);
                    dh_detector = std::make_unique<DumpHedgeDetector>(store, all_m, dh_sum_target, dh_min_discount, dh_min_secs, dh_cooldown);
                    dh_detector->set_fee_rate(fee_rate);
                }
                std::vector<std::string> tokens;
                for (const auto& m : all_m) { tokens.push_back(m.yes_token_id); tokens.push_back(m.no_token_id); }
                if (!tokens.empty()) poly_feed->subscribe(tokens);
                store.push_telemetry(fmt::format("MARKETS REFRESHED | {} markets | {} tokens | fee_curve {}",
                    all_m.size(), tokens.size(), fee_markets));
            } catch (const std::exception& e) {
                spdlog::error("Refresh markets failed: {}", e.what());
            }
            is_refreshing = false;
        };

        refresh_markets();
        std::this_thread::sleep_for(std::chrono::seconds(3));
        auto last_market_refresh = std::chrono::system_clock::now();
        
        // Start balance sync thread
        std::thread balance_thread([&]() {
            while (true) {
                std::this_thread::sleep_for(std::chrono::seconds(60));
                if (!paper_mode) {
#ifdef _WIN32
                    FILE* pipe = popen("python fetch_balance.py", "r");
#else
                    FILE* pipe = popen("python3 fetch_balance.py 2>/dev/null", "r");
#endif
                    if (pipe) {
                        char buf[128];
                        if (fgets(buf, sizeof(buf), pipe)) {
                            try {
                                double new_bal = std::stod(std::string(buf));
                                if (new_bal > 0) {
                                    risk_manager.update_balance(new_bal);
                                }
                            } catch (...) {}
                        }
                        pclose(pipe);
                    }
                }
            }
        });
        balance_thread.detach();

        auto poll_binance_rest = [&]() {
            struct SymMap { const char* sym; void (StateStore::*upd)(const PriceTick&); };
            SymMap maps[] = {
                {"BTCUSDT", &StateStore::update_btc_price},
                {"ETHUSDT", &StateStore::update_eth_price},
                {"SOLUSDT", &StateStore::update_sol_price},
            };
            for (const auto& m : maps) {
                auto px = gamma.fetch_binance_price(m.sym);
                if (!px || *px <= 0) continue;
                PriceTick tick;
                tick.price = *px;
                tick.timestamp_ms = std::chrono::duration<double, std::milli>(
                    std::chrono::system_clock::now().time_since_epoch()).count();
                tick.received_at = std::chrono::duration<double>(
                    std::chrono::system_clock::now().time_since_epoch()).count();
                tick.volume = 0;
                (store.*(m.upd))(tick);
            }
        };

        auto last_binance_rest = std::chrono::system_clock::now() - std::chrono::seconds(10);
        bool rest_fallback_logged = false;
        auto last_paper_save = std::chrono::system_clock::now();

        while (true) {
            auto loop_start = std::chrono::system_clock::now();
            if (loop_start - last_market_refresh > std::chrono::seconds(60)) {
                last_market_refresh = loop_start;
                std::thread([&refresh_markets]() { refresh_markets(); }).detach();
            }
            // REST fallback when Binance WS is blocked (common in Docker/region)
            if (binance_feed_enabled && loop_start - last_binance_rest > std::chrono::seconds(2)) {
                last_binance_rest = loop_start;
                auto btc = store.get_latest_btc_price();
                if (!btc || btc->price <= 0) {
                    if (!rest_fallback_logged) {
                        spdlog::warn("Binance WS unavailable — using REST price polling");
                        rest_fallback_logged = true;
                    }
                    poll_binance_rest();
                }
            }
            risk_manager.is_trading_allowed(); // Trigger resume checks even if no signals fire
            apply_runtime_config("logs/runtime_config.json", risk_manager, store, detector_mutex, dh_detector);
            check_and_close_dh_positions(risk_manager, store, auto_redeem);
            if (paper_mode && paper_state_persist && loop_start - last_paper_save > std::chrono::seconds(10)) {
                last_paper_save = loop_start;
                persistence::save_paper_state(risk_manager, paper_state_path);
            }
            std::cout << store.get_dashboard_json() << std::endl;
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }

        feed_work.reset();
        if (feed_thread.joinable()) feed_thread.join();
    } catch (const std::exception& e) {
        spdlog::critical("Fatal error: {}", e.what());
        return 1;
    }
    return 0;
}
