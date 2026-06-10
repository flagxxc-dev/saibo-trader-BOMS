#pragma once
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/beast/ssl.hpp>
#include <boost/asio/strand.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/steady_timer.hpp>

#include <string>
#include <memory>
#include <vector>
#include <optional>
#include <functional>
#include "../state/StateStore.h"

namespace trading {

namespace beast = boost::beast;
namespace http = beast::http;
namespace websocket = beast::websocket;
namespace net = boost::asio;
namespace ssl = boost::asio::ssl;
using tcp = boost::asio::ip::tcp;

class PolymarketFeed : public std::enable_shared_from_this<PolymarketFeed> {
public:
    explicit PolymarketFeed(net::io_context& ioc, ssl::context& ctx, StateStore& store);
    ~PolymarketFeed();

    void start();
    void stop();
    void subscribe(const std::vector<std::string>& token_ids);
    void set_tick_callback(std::function<void(const std::string&)> cb) { tick_callback_ = std::move(cb); }

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
    void send_ping();

    tcp::resolver resolver_;
    net::io_context& ioc_;
    ssl::context& ctx_;
    std::optional<websocket::stream<beast::ssl_stream<beast::tcp_stream>>> ws_;
    net::steady_timer timer_;
    net::steady_timer ping_timer_;
    beast::flat_buffer buffer_;
    
    StateStore& store_;
    std::string host_ = "ws-subscriptions-clob.polymarket.com";
    std::string port_ = "443";
    std::string path_ = "/ws/market";
    
    std::vector<std::string> subscribed_tokens_;
    
    std::function<void(const std::string&)> tick_callback_;
    bool running_ = false;
    bool connected_ = false;
};

} // namespace trading
