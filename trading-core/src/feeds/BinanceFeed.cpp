#include "BinanceFeed.h"
#include <spdlog/spdlog.h>
#include <boost/json.hpp>
#include <chrono>
#include <algorithm>

namespace trading {

BinanceFeed::BinanceFeed(net::io_context& ioc, ssl::context& ctx, StateStore& store, std::string symbol)
    : resolver_(net::make_strand(ioc)),
      ioc_(ioc),
      ctx_(ctx),
      ws_(std::in_place, net::make_strand(ioc), ctx),
      timer_(net::make_strand(ioc)),
      store_(store),
      symbol_(std::move(symbol)) {
    
    std::string lower_sym = symbol_;
    std::transform(lower_sym.begin(), lower_sym.end(), lower_sym.begin(), ::tolower);
    path_ = "/ws/" + lower_sym + "@trade";
}

BinanceFeed::~BinanceFeed() = default;

void BinanceFeed::start() {
    running_ = true;
    resolve();
}

void BinanceFeed::stop() {
    running_ = false;
    timer_.cancel();
    if (ws_ && ws_->is_open()) {
        beast::error_code ec;
        ws_->close(websocket::close_code::normal, ec);
    }
}

double BinanceFeed::get_price_at(double seconds_ago) {
    if (history_.empty()) return 0.0;
    
    auto now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    double target_ts = now - seconds_ago;
    
    for (auto it = history_.rbegin(); it != history_.rend(); ++it) {
        if (it->received_at <= target_ts) {
            return it->price;
        }
    }
    return history_.front().price;
}

void BinanceFeed::resolve() {
    resolver_.async_resolve(host_, port_, beast::bind_front_handler(&BinanceFeed::on_resolve, shared_from_this()));
}

void BinanceFeed::on_resolve(beast::error_code ec, tcp::resolver::results_type results) {
    if (ec) { spdlog::warn("BinanceFeed [{}] resolve failed: {}", symbol_, ec.message()); return reconnect(); }
    beast::get_lowest_layer(*ws_).async_connect(results, beast::bind_front_handler(&BinanceFeed::on_connect, shared_from_this()));
}

void BinanceFeed::on_connect(beast::error_code ec, tcp::resolver::results_type::endpoint_type ep) {
    if (ec) { spdlog::warn("BinanceFeed [{}] connect failed: {}", symbol_, ec.message()); return reconnect(); }
    SSL_set_tlsext_host_name(ws_->next_layer().native_handle(), host_.c_str());
    ws_->next_layer().async_handshake(ssl::stream_base::client, beast::bind_front_handler(&BinanceFeed::on_ssl_handshake, shared_from_this()));
}

void BinanceFeed::on_ssl_handshake(beast::error_code ec) {
    if (ec) return reconnect();
    ws_->async_handshake(host_, path_, beast::bind_front_handler(&BinanceFeed::on_handshake, shared_from_this()));
}

void BinanceFeed::on_handshake(beast::error_code ec) {
    if (ec) return reconnect();
    do_read();
}

void BinanceFeed::do_read() {
    if (!running_) return;
    ws_->async_read(buffer_, beast::bind_front_handler(&BinanceFeed::on_read, shared_from_this()));
}

void BinanceFeed::on_read(beast::error_code ec, std::size_t) {
    if (ec) { spdlog::warn("BinanceFeed [{}] read failed: {}", symbol_, ec.message()); return reconnect(); }
    process_message(beast::buffers_to_string(buffer_.data()));
    buffer_.consume(buffer_.size());
    do_read();
}

void BinanceFeed::process_message(std::string_view msg) {
    try {
        auto jv = boost::json::parse(msg);
        auto const& obj = jv.as_object();
        if (obj.contains("e") && obj.at("e").as_string() == "trade") {
            PriceTick tick;
            tick.price = std::stod(std::string(obj.at("p").as_string()));
            tick.timestamp_ms = obj.at("T").as_int64();
            tick.received_at = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
            // "q" = trade quantity (base asset), used for volume-weighted logic downstream
            if (obj.contains("q")) {
                tick.volume = std::stod(std::string(obj.at("q").as_string()));
            }

            history_.push_back(tick);
            if (history_.size() > max_history_) history_.pop_front();

            if (symbol_.find("eth") != std::string::npos) store_.update_eth_price(tick);
            else if (symbol_.find("sol") != std::string::npos) store_.update_sol_price(tick);
            else store_.update_btc_price(tick);

            if (tick_callback_) tick_callback_(symbol_, tick.price);
        }
    } catch (const std::exception& e) {
        spdlog::error("BinanceFeed [{}] process_message error: {}", symbol_, e.what());
    }
}

void BinanceFeed::reconnect() {
    if (!running_) return;
    timer_.expires_after(std::chrono::seconds(2));
    timer_.async_wait([this](beast::error_code ec) {
        if (!ec && running_) {
            ws_.emplace(net::make_strand(ioc_), ctx_);
            resolve();
        }
    });
}

} // namespace trading
