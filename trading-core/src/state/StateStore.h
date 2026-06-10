#pragma once
#include <string>
#include <string_view>
#include <unordered_map>
#include <shared_mutex>
#include <optional>
#include <deque>
#include <vector>
#include <atomic>
#include "../signals/Signal.h"

#include "../risk/RiskManager.h"

namespace trading {

struct PriceTick {
    double price;
    double timestamp_ms;
    double volume;
    double received_at;
};

struct TokenPrice {
    double price;
    std::string side;
    double ts;
};

class StateStore {
public:
    void set_risk_manager(risk::RiskManager* rm) { risk_manager_ = rm; }
    void set_paper_mode(bool pm) { paper_mode_ = pm; }
    bool is_paper_mode() const { return paper_mode_; }
    void set_fee_rate(double rate) { fee_rate_ = rate; }

    void update_btc_price(const PriceTick& tick);
    std::optional<PriceTick> get_latest_btc_price() const;
    // Note: use get_price_at("btc", seconds_ago) for historical BTC lookups.

    void update_eth_price(const PriceTick& tick);
    std::optional<PriceTick> get_latest_eth_price() const;

    void update_sol_price(const PriceTick& tick);
    std::optional<PriceTick> get_latest_sol_price() const;

    void update_token_price(std::string_view token_id, const TokenPrice& price);
    void update_token_bid(std::string_view token_id, const TokenPrice& price);
    std::optional<TokenPrice> get_token_bid(std::string_view token_id) const;
    std::optional<TokenPrice> get_token_price(std::string_view token_id) const;

    void update_markets(const std::vector<MarketInfo>& markets);
    std::string get_dashboard_json() const;
    PriceTick get_latest_price(const std::string& asset) const;
    std::optional<double> get_price_at(const std::string& asset, double seconds_ago) const;

    // Telemetry & signal log
    void push_telemetry(const std::string& line);
    void push_signal(const std::string& line);

private:
    risk::RiskManager* risk_manager_ = nullptr;
    bool paper_mode_ = true;
    double fee_rate_ = 0.018;
    mutable std::shared_mutex btc_mutex_;
    PriceTick latest_btc_tick_{};
    std::deque<PriceTick> btc_history_;
    uint64_t btc_tick_count_ = 0;

    mutable std::shared_mutex eth_mutex_;
    PriceTick latest_eth_tick_{};
    std::deque<PriceTick> eth_history_;
    uint64_t eth_tick_count_ = 0;

    mutable std::shared_mutex sol_mutex_;
    PriceTick latest_sol_tick_{};
    std::deque<PriceTick> sol_history_;
    uint64_t sol_tick_count_ = 0;

    mutable std::shared_mutex token_mutex_;
    std::unordered_map<std::string, TokenPrice> token_prices_;
    std::unordered_map<std::string, TokenPrice> token_bids_;

    mutable std::shared_mutex market_mutex_;
    std::vector<MarketInfo> markets_;

    mutable std::shared_mutex log_mutex_;
    std::deque<std::string> telemetry_log_;
    std::deque<std::string> signal_log_;
    static constexpr size_t MAX_LOG_LINES = 30;
};

} // namespace trading
