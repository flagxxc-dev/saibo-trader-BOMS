#pragma once

#include "EIP712Signer.h"
#include "../state/StateStore.h"
#include "../risk/RiskManager.h"
#include "../signals/Signal.h"
#include <string>
#include <memory>
#include <mutex>
#include <optional>
#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>
#include <openssl/hmac.h>
#include <openssl/sha.h>
#include <openssl/evp.h>
#include <openssl/bio.h>
#include <openssl/buffer.h>
#include <boost/json.hpp>

namespace trading {
namespace exec {

struct LegFillResult {
    bool success = false;
    double price = 0.0;
    double size_shares = 0.0;
};

struct BookAskInfo {
    bool ok = false;
    double best_ask = 0.0;
    double depth_shares = 0.0;
};

class OrderRouter {
public:
    OrderRouter(boost::asio::io_context& ioc, 
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
                const std::string& api_key = "",
                const std::string& api_secret = "",
                const std::string& api_passphrase = "",
                const std::string& neg_risk_exchange = "",
                bool live_dh_dry_run = false);

    ~OrderRouter();

    bool submit_order(const std::string& token_id, 
                      double price, 
                      double size, 
                      uint8_t side,
                      bool is_neg_risk = false);

    bool check_book_depth(const std::string& token_id, double price, double size_shares);

    // Sum of ask sizes at or below price * 1.02; -1 on fetch/parse failure.
    double query_ask_depth_shares(const std::string& token_id, double price);

    // Returns true if a DH position was opened (paper or live).
    bool submit_dump_hedge_order(const DumpHedgeSignal& signal, double size_shares);

    void submit_close_order(const std::string& order_id, const std::string& token_id, double current_price, double size, const std::string& asset, const std::string& question, double end_date_ts, const std::string& strategy, bool is_neg_risk = false);

private:
    boost::asio::io_context& ioc_;
    boost::asio::ssl::context& ctx_;
    trading::StateStore& store_;
    risk::RiskManager& risk_manager_;
    mutable std::mutex http_mutex_;

    std::string clob_api_url_;
    std::string signer_address_;
    std::string funder_address_;
    bool paper_mode_;
    bool live_dh_dry_run_;
    std::string api_key_;
    std::string api_secret_;
    std::string api_passphrase_;
    std::string neg_risk_exchange_;
    
    std::unique_ptr<EIP712Signer> signer_;
    std::unique_ptr<EIP712Signer> signer_neg_risk_;

    Order build_order(const std::string& token_id, double price, double size_shares, uint8_t side) const;

    // When register_position=false, sends to CLOB only (used for DH legs / unwind).
    LegFillResult execute_rest_order(
        const Order& order,
        const Signature& sig,
        bool is_neg_risk,
        bool register_position,
        const std::string& asset = "",
        const std::string& question = "",
        double end_date_ts = 0.0,
        const std::string& strategy = "MANUAL",
        const std::string& original_order_id = ""
    );

    bool simulate_paper_order(const Order& order, const Signature& sig, const std::string& asset = "", const std::string& question = "", double end_date_ts = 0.0, const std::string& strategy = "MANUAL", const std::string& original_order_id = "", bool is_neg_risk = false, const std::string& direction = "");

    LegFillResult execute_dh_leg_buy(const std::string& token_id, double price, double size_shares, bool is_neg_risk);
    LegFillResult execute_unwind_sell(const std::string& token_id, double price, double size_shares, bool is_neg_risk);

    std::optional<boost::json::object> fetch_book_object(const std::string& token_id);
    BookAskInfo parse_book_asks(const boost::json::object& book) const;
    BookAskInfo fetch_book_ask_info(const std::string& token_id);

    EIP712Signer& pick_signer(bool is_neg_risk) const;

    std::string generate_salt() const;
    std::string compute_hmac_signature(const std::string& timestamp, const std::string& method, const std::string& path, const std::string& body);
    std::string authenticated_http_get(const std::string& target);
    struct PolledFill {
        bool ok = false;
        std::string status;
        double size_shares = 0.0;
        double price = 0.0;
    };
    PolledFill poll_order_fill(const std::string& order_id, double fallback_price, double requested_shares);
    std::string extract_order_id(const boost::json::object& obj) const;
    double parse_matched_size(const boost::json::value& raw) const;
    std::string base64_encode(const unsigned char* input, int length);
    std::vector<unsigned char> base64_decode(const std::string& input);
    int calc_decode_length(const std::string& b64input);
};

} // namespace exec
} // namespace trading
