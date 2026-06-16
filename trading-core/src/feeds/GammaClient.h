#pragma once
#include <string>
#include <vector>
#include <optional>
#include <mutex>
#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>
#include "../signals/Signal.h"

namespace trading {

class StateStore;

struct SettlementOutcome {
    double yes_payout = 0.0;
    double no_payout = 0.0;
    bool resolved = false;
};

class GammaClient {
public:
    GammaClient(boost::asio::io_context& ioc, boost::asio::ssl::context& ctx);
    std::vector<MarketInfo> fetch_updown_markets(const std::string& asset, int window_minutes = 5);

    // REST price fallback — mirrors Python's get_market_price() REST path.
    // Hits GET clob.polymarket.com/price?token_id=...&side=BUY.
    // Returns nullopt on any error. BLOCKING — must run on gamma_ioc, never feed_ioc.
    std::optional<double> fetch_token_price(const std::string& token_id, const std::string& side = "BUY");
    std::optional<double> fetch_binance_price(const std::string& symbol);

    // Official resolution payouts (0/1) from Gamma or CLOB after market closes.
    std::optional<SettlementOutcome> fetch_settlement_outcomes(const std::string& condition_id);

    // Cache Polymarket V2 fee curve (fd.r / fd.e) per token from /clob-markets/{condition_id}.
    bool fetch_and_cache_market_fees(const std::string& condition_id, StateStore& store);

private:
    std::string http_get(const std::string& host, const std::string& target);
    std::optional<MarketInfo> probe_slug(const std::string& asset, long long ts, int window_minutes);

    boost::asio::io_context& ioc_;
    boost::asio::ssl::context& ctx_;
    std::mutex http_mutex_;
};

} // namespace trading
