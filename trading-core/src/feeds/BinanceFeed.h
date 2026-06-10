#pragma once
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/beast/ssl.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/steady_timer.hpp>
#include <string>
#include <memory>
#include <optional>
#include <deque>
#include <functional>
#include "../state/StateStore.h"

namespace trading {

namespace beast = boost::beast;
namespace websocket = beast::websocket;
namespace net = boost::asio;
namespace ssl = boost::asio::ssl;
using tcp = boost::asio::ip::tcp;

class BinanceFeed : public std::enable_shared_from_this<BinanceFeed> {
public:
    explicit BinanceFeed(net::io_context& ioc, ssl::context& ctx, StateStore& store, std::string symbol);
    ~BinanceFeed();

    void start();
    void stop();
    double get_price_at(double seconds_ago);

    using TickCallback = std::function<void(const std::string&, double)>;
    void set_tick_callback(TickCallback cb) { tick_callback_ = std::move(cb); }

private:
    void resolve();
    void on_resolve(beast::error_code ec, tcp::resolver::results_type results);
    void on_connect(beast::error_code ec, tcp::resolver::results_type::endpoint_type ep);
    void on_ssl_handshake(beast::error_code ec);
    void on_handshake(beast::error_code ec);
    void do_read();
    void on_read(beast::error_code ec, std::size_t bytes_transferred);
    void process_message(std::string_view msg);
    void reconnect();

    tcp::resolver resolver_;
    net::io_context& ioc_;
    ssl::context& ctx_;
    std::optional<websocket::stream<beast::ssl_stream<beast::tcp_stream>>> ws_;
    net::steady_timer timer_;
    beast::flat_buffer buffer_;
    
    StateStore& store_;
    std::string symbol_;
    std::string host_ = "stream.binance.com";
    // Port 443 — 9443 is often blocked in Docker/network; WS works on 443 too.
    std::string port_ = "443";
    std::string path_;
    
    std::deque<PriceTick> history_;
    const size_t max_history_ = 5400; // ~15 mins at 6 ticks/sec
    
    TickCallback tick_callback_;
    bool running_ = false;
};

} // namespace trading
