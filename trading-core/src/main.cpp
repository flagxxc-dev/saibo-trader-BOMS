#include <iostream>
#include <fstream>
#include <string>
#include <unordered_map>
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
#include "risk/KellySizer.h"
#include "signals/LatencyArbDetector.h"
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
    // Try USDC.e first (bridged), then native USDC
    double bal = fetch_usdc_balance_for_contract(funder_address, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "USDC.e");
    if (bal >= 0) {
        spdlog::info("USDC.e balance: ${:.2f}", bal);
    }
    
    double bal2 = fetch_usdc_balance_for_contract(funder_address, "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "USDC");
    if (bal2 >= 0) {
        spdlog::info("USDC (native) balance: ${:.2f}", bal2);
    }
    
    // Return the sum of both (user might have funds in either)
    double total = 0;
    if (bal >= 0) total += bal;
    if (bal2 >= 0) total += bal2;
    
    if (bal < 0 && bal2 < 0) return -1; // Both failed
    return total;
}

struct ExitConfig {
    double near_win_price = 0.92;
    double near_loss_price = 0.08;
    double take_profit_price = 0.72;
    double take_profit_pnl = 0.15;
    double stop_loss_pnl = -0.18;
    double position_timeout_seconds = 270.0; // 4.5 minutes
    double trailing_stop_activation = 0.06;
    double trailing_stop_distance = 0.04;
};

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

void check_and_close_positions(risk::RiskManager& risk_manager, StateStore& store, exec::OrderRouter& router, const ExitConfig& cfg) {
    auto now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    
    // 1. Process Latency Arb (LA) positions - with Early Exit logic
    auto open_la = risk_manager.get_open_positions();
    for (auto& [id, p] : open_la) {
        if (p.strategy != "LA") continue;

        auto live_bid = store.get_token_bid(p.token_id);
        double current_price = live_bid ? live_bid->price : 0.0;
        double current_pnl_pct = (p.entry_price > 0) ? (current_price - p.entry_price) / p.entry_price : 0.0;
        double age = now - p.opened_at;

        // Update peak price
        if (current_price > p.peak_price) {
            p.peak_price = current_price;
            risk_manager.update_peak_price(id, current_price);
        }
        
        double peak_pnl_pct = (p.entry_price > 0) ? (p.peak_price - p.entry_price) / p.entry_price : 0.0;
        double drawdown_from_peak = (p.peak_price > 0) ? (p.peak_price - current_price) / p.peak_price : 0.0;

        bool should_exit = false;
        std::string exit_reason = "";

        if (now >= p.end_date_ts) {
            should_exit = true;
            exit_reason = "EXPIRED";
        } else if (cfg.stop_loss_pnl < 0 && current_pnl_pct <= cfg.stop_loss_pnl) {
            should_exit = true;
            exit_reason = fmt::format("Stop loss: {:.1f}%", current_pnl_pct * 100.0);
        } else if (peak_pnl_pct >= cfg.trailing_stop_activation && drawdown_from_peak >= cfg.trailing_stop_distance) {
            should_exit = true;
            exit_reason = fmt::format("Trailing stop: peak {:.3f} -> {:.3f} (-{:.1f}%)", p.peak_price, current_price, drawdown_from_peak * 100.0);
        } else if (current_price >= cfg.near_win_price) {
            should_exit = true;
            exit_reason = fmt::format("Near resolution win: {:.3f}", current_price);
        } else if (current_price <= cfg.near_loss_price && current_price > 0) {
            should_exit = true;
            exit_reason = fmt::format("Near resolution loss: {:.3f}", current_price);
        } else if ((current_price >= cfg.take_profit_price && current_pnl_pct > 0) || current_pnl_pct >= cfg.take_profit_pnl) {
            should_exit = true;
            exit_reason = fmt::format("Take profit: {:.1f}%", current_pnl_pct * 100.0);
        } else if (age >= cfg.position_timeout_seconds) {
            should_exit = true;
            exit_reason = fmt::format("Timeout: {:.0f}s", age);
        }

        if (should_exit && current_price > 0) {
            if (now >= p.end_date_ts) {
                // Resolution at expiry (Polymarket settle)
                double exit_price = (current_price >= 0.5) ? 1.0 : 0.0;
                risk_manager.register_trade_close(id, exit_price);
                store.push_telemetry(fmt::format("SETTLED {} {} @ {:.2f} | {}", p.asset, exit_price >= 1.0 ? "WIN" : "LOSS", exit_price, p.market_question));
            } else {
                // Dynamic Early Exit — only submit SELL if order value meets minimum size
                double close_proceeds = current_price * p.size_shares;
                double min_order = risk_manager.get_min_order_size();
                if (close_proceeds < min_order) {
                    // Value too small for the exchange to accept — let it ride to expiry
                    store.push_telemetry(fmt::format("LA SKIP CLOSE {} | {} | ${:.2f} < min ${:.2f}",
                        p.asset, exit_reason, close_proceeds, min_order));
                } else {
                    router.submit_close_order(id, p.token_id, current_price, p.size_shares, p.asset, p.market_question, p.end_date_ts, "LA", p.is_neg_risk);
                    store.push_telemetry(fmt::format("LA EARLY EXIT {} | {} | PnL: {:.1f}%", p.asset, exit_reason, current_pnl_pct * 100.0));
                }
            }
        }
    }

    // 2. Process Dump Hedge (DH) positions - only on expiry
    auto open_dh = risk_manager.get_open_dh_positions();
    for (const auto& [id, p] : open_dh) {
        if (now >= p.end_date_ts) {
            auto live_y = store.get_token_price(p.yes_token_id);
            auto live_n = store.get_token_price(p.no_token_id);
            double ey = live_y ? ((live_y->price >= 0.5) ? 1.0 : 0.0) : 0.5;
            double en = live_n ? ((live_n->price >= 0.5) ? 1.0 : 0.0) : 0.5;
            risk_manager.register_dh_close(id, ey, en, "EXPIRED");
            std::string dh_outcome = (ey >= 1.0 || en >= 1.0) ? "WIN" : "LOSS";
            store.push_telemetry(fmt::format("SETTLED {} {} @ {:.2f} | {}",
                p.asset, dh_outcome, std::max(ey, en), p.market_question));
        }
    }
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
                spdlog::warn("Polymarket balance is $0.00. Deposit USDC to start trading.");
            }
        }

        std::string polymarket_pk = env.count("POLYMARKET_PRIVATE_KEY") ? env["POLYMARKET_PRIVATE_KEY"] : "0x0000000000000000000000000000000000000000000000000000000000000001";
        // V2 Exchange addresses (April 2026 migration)
        const std::string V2_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B";
        const std::string V2_NEG_RISK = "0xe2222d279d744050d28e00520010520000310F59";
        
        std::string verifying_contract = V2_EXCHANGE;

        ExitConfig exit_cfg;
        if (env.count("NEAR_WIN_PRICE")) exit_cfg.near_win_price = std::stod(env["NEAR_WIN_PRICE"]);
        if (env.count("NEAR_LOSS_PRICE")) exit_cfg.near_loss_price = std::stod(env["NEAR_LOSS_PRICE"]);
        if (env.count("TAKE_PROFIT_PRICE")) exit_cfg.take_profit_price = std::stod(env["TAKE_PROFIT_PRICE"]);
        if (env.count("TAKE_PROFIT_PNL")) exit_cfg.take_profit_pnl = std::stod(env["TAKE_PROFIT_PNL"]);
        if (env.count("STOP_LOSS_PNL")) exit_cfg.stop_loss_pnl = std::stod(env["STOP_LOSS_PNL"]);
        if (env.count("POSITION_TIMEOUT_SECONDS")) exit_cfg.position_timeout_seconds = std::stod(env["POSITION_TIMEOUT_SECONDS"]);
        if (env.count("TRAILING_STOP_ACTIVATION")) exit_cfg.trailing_stop_activation = std::stod(env["TRAILING_STOP_ACTIVATION"]);
        if (env.count("TRAILING_STOP_DISTANCE")) exit_cfg.trailing_stop_distance = std::stod(env["TRAILING_STOP_DISTANCE"]);

        spdlog::info("Starting Core v2.2 | Mode: {} | Bal: ${:.2f}", paper_mode ? "PAPER" : "LIVE", starting_balance);

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
        double entry_price_min = env.count("ENTRY_PRICE_MIN") ? std::stod(env["ENTRY_PRICE_MIN"]) : 0.38;
        double entry_price_max = env.count("ENTRY_PRICE_MAX") ? std::stod(env["ENTRY_PRICE_MAX"]) : 0.62;
        double la_min_edge = env.count("EDGE_MIN_EDGE_THRESHOLD") ? std::stod(env["EDGE_MIN_EDGE_THRESHOLD"]) : 0.04;
        double la_cooldown = env.count("EDGE_COOLDOWN_SECONDS") ? std::stod(env["EDGE_COOLDOWN_SECONDS"]) : 5.0;
        double la_min_secs = env.count("EDGE_MIN_SECONDS_REMAINING") ? std::stod(env["EDGE_MIN_SECONDS_REMAINING"]) : 60.0;
        double la_fair_strength = env.count("EDGE_MIN_FAIR_VALUE_STRENGTH") ? std::stod(env["EDGE_MIN_FAIR_VALUE_STRENGTH"]) : 0.05;
        double dh_sum_target = env.count("DH_SUM_TARGET") ? std::stod(env["DH_SUM_TARGET"]) : 0.95;
        double dh_min_discount = env.count("DH_MIN_DISCOUNT") ? std::stod(env["DH_MIN_DISCOUNT"]) : 0.03;
        double dh_cooldown = env.count("DH_COOLDOWN_SECONDS") ? std::stod(env["DH_COOLDOWN_SECONDS"]) : 30.0;
        double dh_min_secs = env.count("DH_MIN_SECONDS_REMAINING") ? std::stod(env["DH_MIN_SECONDS_REMAINING"]) : 60.0;

        std::string strategy = env.count("STRATEGY") ? env["STRATEGY"] : "dump_hedge";
        std::transform(strategy.begin(), strategy.end(), strategy.begin(), ::tolower);
        const bool use_la = (strategy == "latency_arb" || strategy == "both");
        const bool use_dh = (strategy == "dump_hedge" || strategy == "both");
        if (!use_la && !use_dh) {
            spdlog::critical("STRATEGY must be latency_arb, dump_hedge, or both. Got: {}", strategy);
            return 1;
        }

        bool binance_feed_enabled = true;
        if (env.count("BINANCE_FEED_ENABLED")) {
            std::string bf = env["BINANCE_FEED_ENABLED"];
            std::transform(bf.begin(), bf.end(), bf.begin(), ::tolower);
            binance_feed_enabled = !(bf == "false" || bf == "0" || bf == "no" || bf == "off");
        }
        const bool use_binance = use_la || binance_feed_enabled;

        spdlog::info("Strategy mode: {} | LA: {} | DH: {} | Binance feed: {}",
                     strategy, use_la ? "on" : "off", use_dh ? "on" : "off",
                     use_binance ? (use_la ? "on (LA)" : "on (display)") : "off");
        spdlog::info("Strategy | DH sum<={:.2f} disc>={:.2f} | LA edge>={:.2f} cd={:.0f}s | Entry {:.2f}-{:.2f}",
                     dh_sum_target, dh_min_discount, la_min_edge, la_cooldown, entry_price_min, entry_price_max);

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

        store.set_risk_manager(&risk_manager);
        store.set_fee_rate(fee_rate);
        store.set_strategy(strategy);
        store.set_dh_config(dh_sum_target, dh_min_discount);
        store.set_binance_feed_enabled(use_binance);
        KellySizer kelly_sizer(0.5, 0.08);

        exec::OrderRouter router(feed_ioc, feed_ctx, store, risk_manager, polymarket_host, polymarket_chain_id, verifying_contract, polymarket_pk, polymarket_signer, polymarket_funder, paper_mode, poly_api_key, poly_api_secret, poly_api_passphrase, neg_risk_exchange);

        GammaClient gamma(gamma_ioc, gamma_ctx);
        std::shared_ptr<BinanceFeed> btc_feed;
        std::shared_ptr<BinanceFeed> eth_feed;
        std::shared_ptr<BinanceFeed> sol_feed;
        if (use_binance) {
            btc_feed = std::make_shared<BinanceFeed>(feed_ioc, feed_ctx, store, "btcusdt");
            eth_feed = std::make_shared<BinanceFeed>(feed_ioc, feed_ctx, store, "ethusdt");
            sol_feed = std::make_shared<BinanceFeed>(feed_ioc, feed_ctx, store, "solusdt");
        }

        // Feeds will be started after callbacks are registered

        auto feed_work = boost::asio::make_work_guard(feed_ioc);
        std::thread feed_thread([&feed_ioc]() { feed_ioc.run(); });

        std::mutex detector_mutex;

        std::vector<std::unique_ptr<LatencyArbDetector>> la_detectors;
        auto price_resolver = [&gamma](const std::string& token_id, const std::string& side) { return gamma.fetch_token_price(token_id, side); };
        if (use_la) {
            la_detectors.push_back(std::make_unique<LatencyArbDetector>(store, std::vector<MarketInfo>{}, la_min_edge, la_min_secs, la_cooldown, 2.7, "btc", price_resolver));
            la_detectors.push_back(std::make_unique<LatencyArbDetector>(store, std::vector<MarketInfo>{}, la_min_edge, la_min_secs, la_cooldown, 2.7, "eth", price_resolver));
            la_detectors.push_back(std::make_unique<LatencyArbDetector>(store, std::vector<MarketInfo>{}, la_min_edge, la_min_secs, la_cooldown, 2.7, "sol", price_resolver));
        }

        std::unique_ptr<DumpHedgeDetector> dh_detector;
        auto eval_la_for_asset = [&](const std::string& sym) {
            if (!use_la) return;
            std::string asset = "btc";
            if (sym.find("eth") != std::string::npos) asset = "eth";
            else if (sym.find("sol") != std::string::npos) asset = "sol";

            std::lock_guard<std::mutex> lock(detector_mutex);
            double now_ms = std::chrono::duration<double, std::milli>(std::chrono::system_clock::now().time_since_epoch()).count();
            for (auto& det : la_detectors) {
                if (det->asset() != asset) continue;
                auto signal = det->evaluate(now_ms);
                if (signal) {
                    auto kelly = kelly_sizer.calculate(risk_manager.get_current_balance(), signal->fair_value, signal->polymarket_price);
                    if (kelly && risk_manager.can_open_position(kelly->position_size_usdc).first) {
                        router.submit_latency_arb_order(*signal, kelly->position_size_usdc / signal->polymarket_price);
                        std::string la_dir = (signal->token_id == signal->market.yes_token_id) ? "YES" : "NO";
                        store.push_signal(fmt::format("LA SIGNAL {} {} | Edge:{:.3f} Fair:{:.3f} PM:{:.4f}",
                            signal->asset, la_dir, signal->edge, signal->fair_value, signal->polymarket_price));
                        store.push_telemetry(fmt::format("[LA] PLACED {} {} @ {:.4f} | ${:.2f}",
                            signal->asset, la_dir, signal->polymarket_price, kelly->position_size_usdc));
                    }
                }
            }
        };

        auto poly_feed = std::make_shared<PolymarketFeed>(feed_ioc, feed_ctx, store);

        if (use_la) {
            btc_feed->set_tick_callback([&](const std::string& sym, double) { eval_la_for_asset(sym); });
            eth_feed->set_tick_callback([&](const std::string& sym, double) { eval_la_for_asset(sym); });
            sol_feed->set_tick_callback([&](const std::string& sym, double) { eval_la_for_asset(sym); });
        }

        poly_feed->set_tick_callback([&](const std::string& token_id) {
            if (!use_dh) return;
            std::lock_guard<std::mutex> lock(detector_mutex);
            double now_ms = std::chrono::duration<double, std::milli>(std::chrono::system_clock::now().time_since_epoch()).count();
            if (dh_detector) {
                auto signal = dh_detector->evaluate(now_ms);
                if (signal) {
                    double max_allowed_usdc = risk_manager.get_current_balance() * 0.08;
                    double size_shares = max_allowed_usdc / signal->combined_price;
                    if (risk_manager.can_open_dh_position(signal->combined_price * size_shares).first) {
                        router.submit_dump_hedge_order(*signal, size_shares);
                        store.push_signal(fmt::format("DH SIGNAL {} | YES:{:.4f} NO:{:.4f} SUM:{:.4f} DISC:{:.1f}%",
                            signal->asset, signal->yes_price, signal->no_price,
                            signal->combined_price, signal->discount * 100.0));
                        store.push_telemetry(fmt::format("[DH] PLACED {} @ {:.4f} | {:.2f} shares | ${:.2f}",
                            signal->asset, signal->yes_price, size_shares, signal->yes_price * size_shares));
                        store.push_telemetry(fmt::format("[DH] PLACED {} @ {:.4f} | {:.2f} shares | ${:.2f}",
                            signal->asset, signal->no_price, size_shares, signal->no_price * size_shares));
                    }
                }
            }
        });

        // Start feeds only after all callbacks are ready
        if (use_binance) {
            btc_feed->start();
            eth_feed->start();
            sol_feed->start();
        }
        poly_feed->start();

        std::atomic<bool> is_refreshing{false};
        auto refresh_markets = [&]() {
            if (is_refreshing.exchange(true)) return;
            try {
                // NOTE: Do NOT call gamma_ioc.restart() here. The price_resolver_ lambda
                // (called from Binance feed threads) uses gamma_ioc for synchronous HTTP.
                // restart() while those ops are in flight is undefined behaviour.
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

                std::vector<MarketInfo> la_m;
                for (const auto& m : all_m) {
                    if (m.window_minutes == 5) la_m.push_back(m);
                }

                store.update_markets(all_m);
                {
                    std::lock_guard<std::mutex> lock(detector_mutex);
                    if (use_la) {
                        for (auto& det : la_detectors) {
                            det->set_active_markets(la_m);
                            det->set_entry_price_range(entry_price_min, entry_price_max);
                            det->set_min_fair_value_strength(la_fair_strength);
                            det->set_fee_rate(fee_rate);
                        }
                    }
                    if (use_dh) {
                        // DH trades 5m (btc/eth/sol) + 15m (btc/eth) — all discovered markets
                        dh_detector = std::make_unique<DumpHedgeDetector>(store, all_m, dh_sum_target, dh_min_discount, dh_min_secs, dh_cooldown);
                        dh_detector->set_fee_rate(fee_rate);
                    }
                }
                std::vector<std::string> tokens;
                for (const auto& m : all_m) { tokens.push_back(m.yes_token_id); tokens.push_back(m.no_token_id); }
                if (!tokens.empty()) poly_feed->subscribe(tokens);
                store.push_telemetry(fmt::format("MARKETS REFRESHED | {} markets | {} tokens",
                    all_m.size(), tokens.size()));
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
            struct SymMap { const char* sym; const char* asset; void (StateStore::*upd)(const PriceTick&); };
            SymMap maps[] = {
                {"BTCUSDT", "btcusdt", &StateStore::update_btc_price},
                {"ETHUSDT", "ethusdt", &StateStore::update_eth_price},
                {"SOLUSDT", "solusdt", &StateStore::update_sol_price},
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
                eval_la_for_asset(m.asset);
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
            if (use_binance && loop_start - last_binance_rest > std::chrono::seconds(2)) {
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
            check_and_close_positions(risk_manager, store, router, exit_cfg);
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
