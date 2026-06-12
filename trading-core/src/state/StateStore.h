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
    void set_strategy(std::string s) { strategy_ = std::move(s); }
    void set_dh_config(double sum_target, double min_discount) {
        dh_sum_target_ = sum_target;
        dh_min_discount_ = min_discount;
    }
    void set_dh_timing(double cooldown_seconds, double min_seconds_remaining) {
        dh_cooldown_seconds_ = cooldown_seconds;
        dh_min_seconds_remaining_ = min_seconds_remaining;
    }
    double get_dh_sum_target() const { return dh_sum_target_; }
    double get_dh_min_discount() const { return dh_min_discount_; }
    double get_dh_cooldown_seconds() const { return dh_cooldown_seconds_; }
    double get_dh_min_seconds_remaining() const { return dh_min_seconds_remaining_; }
    void set_dh_window_enabled(bool enable_5m, bool enable_15m) {
        dh_enable_5m_ = enable_5m;
        dh_enable_15m_ = enable_15m;
    }
    bool dh_enable_5m() const { return dh_enable_5m_; }
    bool dh_enable_15m() const { return dh_enable_15m_; }
    void set_dh_asset_enabled(int window_minutes, const std::string& asset, bool enabled);
    bool dh_asset_enabled(int window_minutes, const std::string& asset) const;
    void set_binance_feed_enabled(bool enabled) { binance_feed_enabled_ = enabled; }

    struct TokenFeeParams {
        double rate = 0.0;
        double exponent = 0.0;
        bool from_api = false;
    };
    void set_token_fee_params(const std::string& token_id, double rate, double exponent);
    TokenFeeParams get_token_fee_params(std::string_view token_id) const;
    double compute_dh_entry_fee_per_share(
        double yes_price, double no_price,
        const std::string& yes_token_id, const std::string& no_token_id) const;

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
    std::string strategy_ = "dump_hedge";
    double dh_sum_target_ = 0.95;
    double dh_min_discount_ = 0.02;
    double dh_cooldown_seconds_ = 30.0;
    double dh_min_seconds_remaining_ = 60.0;
    bool dh_enable_5m_ = true;
    bool dh_enable_15m_ = true;
    bool dh_5m_btc_ = true;
    bool dh_5m_eth_ = true;
    bool dh_5m_sol_ = true;
    bool dh_15m_btc_ = true;
    bool dh_15m_eth_ = true;
    bool binance_feed_enabled_ = true;
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
    std::unordered_map<std::string, TokenFeeParams> token_fee_params_;

    mutable std::shared_mutex market_mutex_;
    std::vector<MarketInfo> markets_;

    mutable std::shared_mutex log_mutex_;
    std::deque<std::string> telemetry_log_;
    std::deque<std::string> signal_log_;
    static constexpr size_t MAX_LOG_LINES = 100;
};

} // namespace trading
