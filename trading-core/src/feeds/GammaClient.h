#pragma once
#include <string>
#include <vector>
#include <optional>
#include <mutex>
#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>
#include "../signals/Signal.h"

namespace trading {

class GammaClient {
public:
    GammaClient(boost::asio::io_context& ioc, boost::asio::ssl::context& ctx);
    std::vector<MarketInfo> fetch_updown_markets(const std::string& asset, int window_minutes = 5);

    // REST price fallback — mirrors Python's get_market_price() REST path.
    // Hits GET clob.polymarket.com/price?token_id=...&side=BUY.
    // Returns nullopt on any error. BLOCKING — must run on gamma_ioc, never feed_ioc.
    std::optional<double> fetch_token_price(const std::string& token_id, const std::string& side = "BUY");
    std::optional<double> fetch_binance_price(const std::string& symbol);

private:
    std::string http_get(const std::string& host, const std::string& target);
    std::optional<MarketInfo> probe_slug(const std::string& asset, long long ts, int window_minutes);

    boost::asio::io_context& ioc_;
    boost::asio::ssl::context& ctx_;
    std::mutex http_mutex_;
};

} // namespace trading
