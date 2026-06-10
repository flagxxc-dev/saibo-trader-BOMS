#include "PolymarketFeed.h"
#include <spdlog/spdlog.h>
#include <boost/json.hpp>
#include <chrono>

namespace trading {

PolymarketFeed::PolymarketFeed(net::io_context& ioc, ssl::context& ctx, StateStore& store)
    : resolver_(net::make_strand(ioc)),
      ioc_(ioc),
      ctx_(ctx),
      ws_(std::in_place, net::make_strand(ioc), ctx),
      timer_(net::make_strand(ioc)),
      ping_timer_(net::make_strand(ioc)),
      store_(store) {}

PolymarketFeed::~PolymarketFeed() = default;

void PolymarketFeed::start() {
    running_ = true;
    resolve();
}

void PolymarketFeed::stop() {
    running_ = false;
    connected_ = false;
    timer_.cancel();
    ping_timer_.cancel();
    if (ws_ && ws_->is_open()) {
        beast::error_code ec;
        ws_->close(websocket::close_code::normal, ec);
    }
}

void PolymarketFeed::subscribe(const std::vector<std::string>& token_ids) {
    if (!token_ids.empty()) {
        subscribed_tokens_ = token_ids;
    }
    if (connected_) {
        const size_t chunk_size = 200;
        for (size_t i = 0; i < subscribed_tokens_.size(); i += chunk_size) {
            size_t end = std::min(i + chunk_size, subscribed_tokens_.size());
            boost::json::object msg;
            msg["type"] = "market";
            boost::json::array assets;
            for (size_t j = i; j < end; ++j)
                assets.push_back(subscribed_tokens_[j].c_str());
            msg["assets_ids"] = std::move(assets);
            std::string json = boost::json::serialize(msg);
            if (ws_) {
                ws_->async_write(net::buffer(json), [](beast::error_code ec, std::size_t) {
                    if (ec) spdlog::warn("PolymarketFeed: Subscription failed: {}", ec.message());
                });
            }
        }
        spdlog::info("PolymarketFeed: Subscribed to {} tokens", subscribed_tokens_.size());
    }
}

void PolymarketFeed::resolve() {
    resolver_.async_resolve(host_, port_, beast::bind_front_handler(&PolymarketFeed::on_resolve, shared_from_this()));
}

void PolymarketFeed::on_resolve(beast::error_code ec, tcp::resolver::results_type results) {
    if (ec) return reconnect();
    beast::get_lowest_layer(*ws_).async_connect(results, beast::bind_front_handler(&PolymarketFeed::on_connect, shared_from_this()));
}

void PolymarketFeed::on_connect(beast::error_code ec, tcp::resolver::results_type::endpoint_type ep) {
    if (ec) return reconnect();
    SSL_set_tlsext_host_name(ws_->next_layer().native_handle(), host_.c_str());
    ws_->next_layer().async_handshake(ssl::stream_base::client, beast::bind_front_handler(&PolymarketFeed::on_ssl_handshake, shared_from_this()));
}

void PolymarketFeed::on_ssl_handshake(beast::error_code ec) {
    if (ec) return reconnect();
    ws_->set_option(websocket::stream_base::decorator([](websocket::request_type& req) {
        req.set(http::field::user_agent, "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36");
        req.set(http::field::origin, "https://polymarket.com");
    }));
    ws_->async_handshake(host_, path_, beast::bind_front_handler(&PolymarketFeed::on_handshake, shared_from_this()));
}

void PolymarketFeed::on_handshake(beast::error_code ec) {
    if (ec) return reconnect();
    connected_ = true;
    spdlog::info("PolymarketFeed: WebSocket connected");
    if (!subscribed_tokens_.empty()) subscribe({});
    send_ping();
    do_read();
}

void PolymarketFeed::send_ping() {
    if (!running_ || !connected_) return;
    ping_timer_.expires_after(std::chrono::seconds(5));
    ping_timer_.async_wait([this](beast::error_code ec) {
        if (!ec && running_ && connected_ && ws_ && ws_->is_open()) {
            ws_->async_write(net::buffer("{\"type\":\"ping\"}"), [this](beast::error_code write_ec, std::size_t) {
                if (!write_ec) send_ping();
            });
        }
    });
}

void PolymarketFeed::do_read() {
    if (!running_) return;
    ws_->async_read(buffer_, beast::bind_front_handler(&PolymarketFeed::on_read, shared_from_this()));
}

void PolymarketFeed::on_read(beast::error_code ec, std::size_t) {
    if (ec) return reconnect();
    process_message(beast::buffers_to_string(buffer_.data()));
    buffer_.consume(buffer_.size());
    do_read();
}

void PolymarketFeed::process_message(std::string_view msg) {
    // Skip non-JSON messages (ping/pong heartbeats, empty frames)
    if (msg.empty() || (msg[0] != '{' && msg[0] != '[')) {
        return;
    }
    try {
        boost::json::value jv = boost::json::parse(msg);

        auto process_item = [&](const boost::json::object& item) {
            std::string et = "";
            if (item.contains("event_type")) et = std::string(item.at("event_type").as_string());
            else if (item.contains("type")) et = std::string(item.at("type").as_string());

            double now_ts = std::chrono::duration<double>(
                std::chrono::system_clock::now().time_since_epoch()).count();

            if (et == "price_change") {
                // price_change: side="BUY" = maker bid (we sell to), side="SELL" = maker ask (we buy from)
                std::string token_id = item.contains("asset_id") ?
                    std::string(item.at("asset_id").as_string()) : "";
                if (token_id.empty()) return;

                std::string side = item.contains("side") ?
                    std::string(item.at("side").as_string()) : "SELL";
                double price = 0;
                if (item.contains("price"))
                    price = std::stod(std::string(item.at("price").as_string()));
                if (price <= 0 || price > 1.0) return;

                TokenPrice tp;
                tp.ts = now_ts;
                tp.price = price;

                spdlog::debug("PM tick: token={}.. price={:.3f} side={}", token_id.substr(0,12), price, side);
                if (side == "SELL") {
                    // Maker SELL = Ask = price we BUY at
                    tp.side = "BUY";
                    store_.update_token_price(token_id, tp);
                    if (tick_callback_) tick_callback_(token_id);
                } else {
                    // Maker BUY = Bid — store separately for reference
                    tp.side = "SELL";
                    store_.update_token_bid(token_id, tp);
                }

            } else if (et == "book") {
                std::string token_id = item.contains("asset_id") ?
                    std::string(item.at("asset_id").as_string()) : "";
                if (token_id.empty()) return;

                // Best ask = lowest ask = price we BUY at
                if (item.contains("asks") && !item.at("asks").as_array().empty()) {
                    double best_ask = 1.0;
                    for (const auto& a : item.at("asks").as_array()) {
                        double p = std::stod(std::string(a.as_object().at("price").as_string()));
                        if (p < best_ask) best_ask = p;
                    }
                    TokenPrice tp;
                    tp.price = best_ask;
                    tp.side = "BUY";
                    tp.ts = now_ts;
                    store_.update_token_price(token_id, tp);
                    if (tick_callback_) tick_callback_(token_id);
                }

                // Best bid = highest bid
                if (item.contains("bids") && !item.at("bids").as_array().empty()) {
                    double best_bid = 0.0;
                    for (const auto& b : item.at("bids").as_array()) {
                        double p = std::stod(std::string(b.as_object().at("price").as_string()));
                        if (p > best_bid) best_bid = p;
                    }
                    TokenPrice tp;
                    tp.price = best_bid;
                    tp.side = "SELL";
                    tp.ts = now_ts;
                    store_.update_token_bid(token_id, tp);
                }
            }
        };

        if (jv.is_array()) for (auto& v : jv.as_array()) process_item(v.as_object());
        else if (jv.is_object()) process_item(jv.as_object());
    } catch (const std::exception& e) {
        spdlog::error("PolymarketFeed process_message error: {}", e.what());
    }
}

void PolymarketFeed::reconnect() {
    if (!running_) return;
    connected_ = false;
    spdlog::warn("PolymarketFeed: Disconnected, reconnecting in 2s...");
    timer_.expires_after(std::chrono::seconds(2));
    timer_.async_wait([this](beast::error_code ec) {
        if (!ec && running_) {
            ws_.emplace(net::make_strand(ioc_), ctx_);
            resolve();
        }
    });
}

} // namespace trading
