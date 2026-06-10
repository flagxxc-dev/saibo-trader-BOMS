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
#include <thread>

namespace trading {
namespace exec {

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
                        const std::string& neg_risk_exchange)
    : ioc_(ioc), ctx_(ctx), store_(store), risk_manager_(risk_manager),
      clob_api_url_(clob_api_url), signer_address_(signer_address), funder_address_(funder_address), 
      paper_mode_(paper_mode),
      api_key_(api_key), api_secret_(api_secret), api_passphrase_(api_passphrase),
      neg_risk_exchange_(neg_risk_exchange) {
    
    if (!paper_mode_ && api_key_.empty()) {
        spdlog::critical("[FATAL] Live trading enabled but POLY_API_KEY is missing! Run derive_and_update_keys.py first.");
        throw std::runtime_error("Missing API credentials for live trading");
    }
    
    signer_ = std::make_unique<EIP712Signer>(std::stoull(chain_id_str), verifying_contract, private_key_hex);
    if (!neg_risk_exchange_.empty()) {
        signer_neg_risk_ = std::make_unique<EIP712Signer>(std::stoull(chain_id_str), neg_risk_exchange_, private_key_hex);
    }
}

OrderRouter::~OrderRouter() {}

bool OrderRouter::submit_order(const std::string& token_id, double price, double size, uint8_t side, bool is_neg_risk) {
    Order order;
    order.salt = generate_salt();
    order.maker = funder_address_;
    order.signer = signer_address_;
    order.taker = "0x0000000000000000000000000000000000000000";
    order.tokenId = token_id;
    
    uint64_t scale = 1000000;
    if (side == 0) { // BUY
        order.makerAmount = std::to_string(static_cast<uint64_t>(size * price * scale));
        order.takerAmount = std::to_string(static_cast<uint64_t>(size * scale));
    } else { // SELL
        order.makerAmount = std::to_string(static_cast<uint64_t>(size * scale));
        order.takerAmount = std::to_string(static_cast<uint64_t>(size * price * scale));
    }

    auto now = std::chrono::system_clock::now();
    auto exp = now + std::chrono::seconds(60);
    order.expiration = std::to_string(std::chrono::duration_cast<std::chrono::seconds>(exp.time_since_epoch()).count());
    
    order.side = side; 
    order.signatureType = (funder_address_ == signer_address_ ? 0 : 1); 
    
    order.timestamp = std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count());
    order.metadata = "0x0000000000000000000000000000000000000000000000000000000000000000";
    order.builder = "0x0000000000000000000000000000000000000000000000000000000000000000";

    try {
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            return simulate_paper_order(order, sig, "", "", 0.0, "MANUAL", "", is_neg_risk);
        } else {
            return execute_rest_order(order, sig, "", "", 0.0, "MANUAL", "", is_neg_risk);
        }
    } catch (const std::exception& e) {
        spdlog::error("Order signature failed: {}", e.what());
        return false;
    }
}

void OrderRouter::submit_latency_arb_order(const LatencyArbSignal& signal, double size_shares) {
    Order order;
    order.salt = generate_salt();
    order.maker = funder_address_;
    order.signer = signer_address_;
    order.taker = "0x0000000000000000000000000000000000000000";
    order.tokenId = signal.token_id;
    
    uint64_t scale = 1000000;
    order.makerAmount = std::to_string(static_cast<uint64_t>(size_shares * signal.polymarket_price * scale));
    order.takerAmount = std::to_string(static_cast<uint64_t>(size_shares * scale));

    auto now = std::chrono::system_clock::now();
    auto exp = now + std::chrono::seconds(60);
    order.expiration = std::to_string(std::chrono::duration_cast<std::chrono::seconds>(exp.time_since_epoch()).count());
    
    order.side = 0; // BUY
    order.signatureType = (funder_address_ == signer_address_ ? 0 : 1); 
    
    order.timestamp = std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count());
    order.metadata = "0x0000000000000000000000000000000000000000000000000000000000000000";
    order.builder = "0x0000000000000000000000000000000000000000000000000000000000000000";

    try {
        bool is_neg_risk = signal.market.is_neg_risk;
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            std::string dir = (signal.token_id == signal.market.yes_token_id) ? "UP" : "DOWN";
            simulate_paper_order(order, sig, signal.asset, signal.market.question, signal.market.end_date_ts, "LA", "", is_neg_risk, dir);
        } else {
            // Run live execution in a separate thread to avoid blocking the main loop
            auto order_copy = order;
            auto sig_copy = sig;
            auto asset_copy = signal.asset;
            auto question_copy = signal.market.question;
            double end_ts = signal.market.end_date_ts;
            std::thread([this, order_copy, sig_copy, asset_copy, question_copy, end_ts, is_neg_risk]() {
                execute_rest_order(order_copy, sig_copy, asset_copy, question_copy, end_ts, "LA", "", is_neg_risk);
            }).detach();
        }
    } catch (const std::exception& e) {
        spdlog::error("Order signature failed: {}", e.what());
    }
}

bool OrderRouter::check_book_depth(const std::string& token_id, double price, double size) {
    // Basic check for resting liquidity using the /book endpoint
    namespace beast = boost::beast;
    namespace http = beast::http;
    
    std::string host = "clob.polymarket.com";
    std::string target = "/book?token_id=" + token_id;

    try {
        boost::asio::ip::tcp::resolver resolver(ioc_);
        beast::ssl_stream<beast::tcp_stream> stream(ioc_, ctx_);
        if(!SSL_set_tlsext_host_name(stream.native_handle(), host.c_str())) return false;
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

        if (res.result() != http::status::ok) return false;
        
        auto jv = boost::json::parse(res.body());
        auto obj = jv.as_object();
        // Just verify the book exists for now to avoid latency. 
        // A full depth sum could be added here.
        if (obj.contains("bids") && obj.contains("asks")) return true;
        return false;
    } catch (...) {
        return false;
    }
}

void OrderRouter::submit_dump_hedge_order(const DumpHedgeSignal& signal, double size_shares) {
    std::string dh_id = "DH-" + signal.asset + "-" + std::to_string(static_cast<uint64_t>(signal.timestamp));
    bool is_neg_risk = signal.market.is_neg_risk;

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
        double entry_fees = dh_pos.combined_cost_usdc * risk_manager_.get_fee_rate();
        dh_pos.locked_profit_usdc = (1.0 - signal.combined_price) * size_shares - entry_fees;
        dh_pos.opened_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        dh_pos.end_date_ts = signal.market.end_date_ts;
        dh_pos.paper_mode = true;
        dh_pos.is_neg_risk = is_neg_risk;

        risk_manager_.register_dh_open(dh_pos);
        spdlog::info("[PAPER DH] OPENED | {} | Entry: {:.4f} | Locked Profit: ${:.2f}", signal.asset, signal.combined_price, dh_pos.locked_profit_usdc);
    } else {
        spdlog::info("[LIVE DH] Initiating atomic dual-leg submission for {}... (neg_risk={})", signal.asset, is_neg_risk);

        // 1. Pre-flight depth check
        if (!check_book_depth(signal.yes_token_id, signal.yes_price, size_shares) ||
            !check_book_depth(signal.no_token_id, signal.no_price, size_shares)) {
            spdlog::error("[LIVE DH] Pre-flight depth check failed for {}. Aborting DH open.", signal.asset);
            return;
        }

        // 2. Parallel Submission
        bool yes_filled = false;
        bool no_filled = false;

        std::thread yes_thread([&]() {
            yes_filled = submit_order(signal.yes_token_id, signal.yes_price, size_shares, 0, is_neg_risk);
        });
        std::thread no_thread([&]() {
            no_filled = submit_order(signal.no_token_id, signal.no_price, size_shares, 0, is_neg_risk);
        });

        yes_thread.join();
        no_thread.join();

        // 3. Rollback on partial fill
        if (yes_filled && !no_filled) {
            spdlog::error("[LIVE DH] NO leg failed but YES leg filled! EMERGENCY ROLLBACK for {}.", signal.asset);
            submit_close_order("live_dh_" + signal.yes_token_id, signal.yes_token_id, signal.yes_price, size_shares, signal.asset, signal.market.question, signal.market.end_date_ts, "DH_ROLLBACK", is_neg_risk);
            return;
        }
        if (!yes_filled && no_filled) {
            spdlog::error("[LIVE DH] YES leg failed but NO leg filled! EMERGENCY ROLLBACK for {}.", signal.asset);
            submit_close_order("live_dh_" + signal.no_token_id, signal.no_token_id, signal.no_price, size_shares, signal.asset, signal.market.question, signal.market.end_date_ts, "DH_ROLLBACK", is_neg_risk);
            return;
        }
        if (!yes_filled && !no_filled) {
            spdlog::warn("[LIVE DH] Both legs failed for {}. No position opened.", signal.asset);
            return;
        }

        // Register in RiskManager as a combined position
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
        dh_pos.locked_profit_usdc = (1.0 - signal.combined_price) * size_shares;
        dh_pos.opened_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        dh_pos.end_date_ts = signal.market.end_date_ts;
        dh_pos.paper_mode = false;
        dh_pos.is_neg_risk = is_neg_risk;

        risk_manager_.register_dh_open(dh_pos);
        spdlog::info("[LIVE DH] REGISTERED | {} | Total Cost: ${:.2f}", signal.asset, dh_pos.combined_cost_usdc);
    }
}

void OrderRouter::submit_close_order(const std::string& order_id, const std::string& token_id, double current_price, double size, const std::string& asset, const std::string& question, double end_date_ts, const std::string& strategy, bool is_neg_risk) {
    Order order;
    order.salt = generate_salt();
    order.maker = funder_address_;
    order.signer = signer_address_;
    order.taker = "0x0000000000000000000000000000000000000000";
    order.tokenId = token_id;
    
    uint64_t scale = 1000000;
    order.makerAmount = std::to_string(static_cast<uint64_t>(size * scale));
    order.takerAmount = std::to_string(static_cast<uint64_t>(size * current_price * scale));

    auto now = std::chrono::system_clock::now();
    auto exp = now + std::chrono::seconds(60);
    order.expiration = std::to_string(std::chrono::duration_cast<std::chrono::seconds>(exp.time_since_epoch()).count());
    
    order.side = 1; // SELL
    order.signatureType = (funder_address_ == signer_address_ ? 0 : 1); 
    
    order.timestamp = std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count());
    order.metadata = "0x0000000000000000000000000000000000000000000000000000000000000000";
    order.builder = "0x0000000000000000000000000000000000000000000000000000000000000000";

    try {
        Signature sig = pick_signer(is_neg_risk).sign_order(order);
        if (paper_mode_) {
            simulate_paper_order(order, sig, asset, question, end_date_ts, strategy, order_id, is_neg_risk);
        } else {
            execute_rest_order(order, sig, asset, question, end_date_ts, strategy, order_id, is_neg_risk);
        }
    } catch (const std::exception& e) {
        spdlog::error("Close order signature failed: {}", e.what());
    }
}

bool OrderRouter::simulate_paper_order(const Order& order, const Signature& sig, const std::string& asset, const std::string& question, double end_date_ts, const std::string& strategy, const std::string& original_order_id, bool is_neg_risk, const std::string& direction) {
    if (order.side == 0) { // BUY
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
    } else { // SELL
        double price = std::stod(order.takerAmount) / std::stod(order.makerAmount);
        risk_manager_.register_trade_close(original_order_id, price);
        spdlog::info("[PAPER TRADE] CLOSED | {} | {} | Strategy: {} | Exit Price: {:.4f}",
                     asset, question, strategy, price);
    }
    return true;
}

bool OrderRouter::execute_rest_order(const Order& order, const Signature& sig, const std::string& asset, const std::string& question, double end_date_ts, const std::string& strategy, const std::string& original_order_id, bool is_neg_risk) {
    namespace beast = boost::beast;
    namespace http = beast::http;

    boost::json::object root;
    boost::json::object ord;
    ord["salt"] = std::stoull(order.salt);  // SDK sends salt as integer, not string
    ord["maker"] = order.maker;
    ord["signer"] = order.signer;
    ord["taker"] = order.taker;
    ord["tokenId"] = order.tokenId;
    ord["makerAmount"] = order.makerAmount;
    ord["takerAmount"] = order.takerAmount;
    ord["expiration"] = std::stoull(order.expiration); // typically a number in JSON body
    ord["side"] = order.side == 0 ? "BUY" : "SELL";
    ord["signatureType"] = (funder_address_ != signer_address_ && !signer_address_.empty()) ? 1 : 0;
    ord["timestamp"] = order.timestamp;
    ord["signature"] = sig.rsv_hex;  // signature goes INSIDE the order object
    root["order"] = std::move(ord);
    root["owner"] = api_key_;        // owner is the API key UUID, NOT the wallet address
    root["orderType"] = "FAK";
    root["postOnly"] = false;

    std::string payload = boost::json::serialize(root);
    
    spdlog::info("[LIVE EXEC] Dispatching order to Polymarket CLOB: {}", payload);

    try {
        // Simple synchronous POST via Beast
        std::string host = "clob.polymarket.com";
        std::string target = "/order";

        boost::asio::ip::tcp::resolver resolver(ioc_);
        beast::ssl_stream<beast::tcp_stream> stream(ioc_, ctx_);

        if(!SSL_set_tlsext_host_name(stream.native_handle(), host.c_str())) {
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
        if (order.side == 0) { // BUY
            target_price = std::stod(order.makerAmount) / std::stod(order.takerAmount);
            size_shares = std::stod(order.takerAmount) / 1000000.0;
        } else { // SELL
            target_price = std::stod(order.takerAmount) / std::stod(order.makerAmount);
            size_shares = std::stod(order.makerAmount) / 1000000.0;
        }

        beast::error_code ec;
        stream.shutdown(ec);

        if (res.result() != http::status::ok && res.result() != http::status::created) {
            spdlog::error("[LIVE EXEC] Order REJECTED by CLOB: {} | Body: {}", res.result_int(), res.body());
            return false;
        }

        auto response_json = boost::json::parse(res.body()).as_object();
        spdlog::info("[LIVE EXEC] Order Response: {}", res.body());

        double actual_price = target_price;
        double filled_size = 0.0;

        if (response_json.contains("price")) {
            actual_price = std::stod(std::string(response_json["price"].as_string()));
        }
        if (response_json.contains("size_matched")) {
            filled_size = std::stod(std::string(response_json["size_matched"].as_string())) / 1000000.0;
        } else if (response_json.contains("status") && response_json["status"].as_string() == "filled") {
            filled_size = size_shares; // Fallback if status is filled
        }

        if (filled_size <= 0) {
            spdlog::warn("[LIVE EXEC] Order accepted by CLOB but 0 size matched. No position opened.");
            return false;
        }

        if (order.side == 0) { // BUY
            double slippage = (actual_price - target_price) / target_price;
            
            // Register position with ACTUAL data
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
            spdlog::info("[LIVE EXEC] Trade FILLED | {} | Price: {:.4f} | Size: {:.2f} | Slippage: {:.4f}%", 
                         asset, actual_price, filled_size, slippage * 100.0);
        } else { // SELL
            risk_manager_.register_trade_close(original_order_id, actual_price);
            spdlog::info("[LIVE EXEC] Trade CLOSED | {} | Price: {:.4f}", asset, actual_price);
        }
        return true;
    } catch (const std::exception& e) {
        spdlog::error("[LIVE EXEC] Network error during order submission: {}", e.what());
        return false;
    }
}

EIP712Signer& OrderRouter::pick_signer(bool is_neg_risk) const {
    // Polymarket has two on-chain exchange contracts with different EIP-712 domain separators:
    //   CTF Exchange   (signer_)          — standard binary markets
    //   Neg-Risk Adapter (signer_neg_risk_) — Up/Down and other neg-risk markets
    // Signing with the wrong contract produces "order_version_mismatch" from the CLOB.
    if (is_neg_risk && signer_neg_risk_) {
        return *signer_neg_risk_;
    }
    return *signer_;
}

std::string OrderRouter::generate_salt() const {
    // Mutex required: called from detached order threads simultaneously.
    // Static RNG is shared state — concurrent calls without locking are UB.
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
    int actualLen = BIO_read(bio, buffer.data(), input.length());
    buffer.resize(actualLen);
    BIO_free_all(bio);

    return buffer;
}

int OrderRouter::calc_decode_length(const std::string& b64input) {
    int len = b64input.size();
    int padding = 0;

    if (len > 0 && b64input[len-1] == '=' && len > 1 && b64input[len-2] == '=')
        padding = 2;
    else if (len > 0 && b64input[len-1] == '=')
        padding = 1;

    return (len * 3) / 4 - padding;
}

} // namespace exec
} // namespace trading
