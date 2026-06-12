#pragma once

#include <string>
#include <unordered_map>
#include <vector>
#include <deque>
#include <optional>
#include <chrono>
#include <memory>
#include <mutex>
#include <boost/json.hpp>

namespace risk {

enum class TradingStatus {
    ACTIVE,
    DAILY_HALT,
    KILLED,
    PAUSED
};

struct Position {
    std::string order_id;
    std::string token_id;
    std::string market_question;
    std::string side;
    double entry_price;
    double size_shares;
    double cost_usdc;
    double opened_at;
    double end_date_ts = 0.0;
    std::string asset = "";
    std::string direction = "";
    std::string strategy = "MANUAL";
    std::string condition_id = "";
    std::optional<double> closed_at;
    std::optional<double> exit_price;
    std::optional<double> pnl_usdc;
    bool paper_mode = true;
    double peak_price = 0.0;
    bool is_neg_risk = false; // true for Polymarket Up/Down (neg-risk) markets
};

struct DumpHedgePosition {
    std::string dh_id;
    std::string yes_order_id;
    std::string no_order_id;
    std::string yes_token_id;
    std::string no_token_id;
    std::string market_question;
    std::string asset;
    double yes_entry_price;
    double no_entry_price;
    double combined_entry_price;
    double size_shares;
    double combined_cost_usdc;
    double locked_profit_usdc;
    double opened_at;
    double end_date_ts = 0.0;
    bool paper_mode = true;
    std::string strategy = "DH";
    std::optional<double> closed_at;
    std::optional<double> yes_exit_price;
    std::optional<double> no_exit_price;
    std::optional<double> pnl_usdc;
    std::string exit_reason;
    bool is_neg_risk = false; // true for Polymarket Up/Down (neg-risk) markets
    int window_minutes = 5;   // 5 or 15 — Polymarket up/down series
    std::string condition_id; // 0x hex — for on-chain redeem after resolution
};

class RiskManager {
public:
    RiskManager(
        double starting_balance,
        double max_position_fraction = 0.08,
        double daily_loss_limit = 0.20,
        double total_drawdown_kill = 0.40,
        int max_concurrent_positions = 3,
        bool circuit_breaker_enabled = true,
        int circuit_breaker_min_losses = 3,
        int circuit_breaker_window = 5,
        double circuit_breaker_loss_pct = 0.02,
        double circuit_breaker_pause_seconds = 300.0,
        double min_order_size = 5.0
    );

    TradingStatus get_status() const;
    bool is_trading_allowed();
    double get_current_balance() const;
    double get_total_equity() const;
    std::optional<std::string> get_status_reason() const;
    double get_daily_starting_balance() const;
    double get_peak_balance() const;
    double get_starting_balance() const;
    int get_open_position_count() const;
    double get_win_rate() const;
    double get_min_order_size() const;
    
    double get_la_pnl() const;
    double get_dh_pnl() const;
    int get_total_trades() const;
    int get_total_dh_trades() const;
    int get_winning_trades() const;

    const std::unordered_map<std::string, int>& get_asset_trades() const;
    const std::unordered_map<std::string, int>& get_asset_wins() const;
    const std::unordered_map<std::string, double>& get_asset_pnl() const;

    std::pair<bool, std::string> can_open_position(double position_size_usdc);
    void register_trade_open(const Position& position);
    std::optional<Position> register_trade_close(
        const std::string& order_id,
        double exit_price,
        std::optional<double> exit_timestamp = std::nullopt,
        std::optional<double> actual_proceeds_usdc = std::nullopt
    );

    std::pair<bool, std::string> can_open_dh_position(double combined_cost_usdc);
    void register_dh_open(const DumpHedgePosition& position);
    std::optional<DumpHedgePosition> register_dh_close(
        const std::string& dh_id,
        double yes_exit_price,
        double no_exit_price,
        const std::string& exit_reason = "",
        std::optional<double> exit_timestamp = std::nullopt,
        std::optional<double> actual_proceeds_usdc = std::nullopt
    );

    std::unordered_map<std::string, Position> get_open_positions() const;
    std::unordered_map<std::string, DumpHedgePosition> get_open_dh_positions() const;
    std::vector<Position> get_closed_positions() const;
    std::vector<DumpHedgePosition> get_closed_dh_positions() const;

    void update_peak_price(const std::string& order_id, double peak_price);

    void update_balance(double new_balance);
    void set_daily_starting_balance(double balance);
    void set_live_starting_balance(double balance);

    void set_fee_rate(double rate) { fee_rate_ = rate; }
    double get_fee_rate() const { return fee_rate_; }

    double get_max_position_fraction() const;
    double get_daily_loss_limit() const;
    double get_total_drawdown_kill() const;
    int get_max_concurrent_positions() const;

    void set_max_position_fraction(double v);
    void set_daily_loss_limit(double v);
    void set_total_drawdown_kill(double v);
    void set_max_concurrent_positions(int v);

    void pause(const std::string& reason = "Manual pause");
    bool resume();
    bool reset_kill_switch(bool confirm = false);

    // Flat-close any open LA positions left from older sessions (strategy removed).
    int close_legacy_la_positions();

    // Paper mode persistence (JSON snapshot)
    boost::json::object export_paper_state() const;
    bool import_paper_state(const boost::json::object& doc);

private:
    void check_risk_thresholds();
    void check_circuit_breaker();
    void check_circuit_breaker_resume();
    void check_daily_reset();
    void trigger_kill_switch(const std::string& reason);
    void trigger_daily_halt(const std::string& reason);
    static double next_midnight();
    void record_asset_close(const std::string& asset, double pnl, bool won);
    static double now();
    bool is_trading_allowed_no_lock();

    double max_position_fraction_;
    double daily_loss_limit_;
    double total_drawdown_kill_;
    int max_concurrent_positions_;
    bool circuit_breaker_enabled_;
    int circuit_breaker_min_losses_;
    double circuit_breaker_loss_pct_;
    double circuit_breaker_pause_seconds_;
    double min_order_size_;

    double starting_balance_;
    double current_balance_;
    double peak_balance_;
    double daily_starting_balance_;
    double daily_reset_time_;

    TradingStatus status_;
    std::optional<std::string> kill_reason_;

    std::unordered_map<std::string, Position> open_positions_;
    std::vector<Position> closed_positions_;

    std::unordered_map<std::string, DumpHedgePosition> open_dh_positions_;
    std::vector<DumpHedgePosition> closed_dh_positions_;

    int total_trades_ = 0;
    int winning_trades_ = 0;
    double total_pnl_ = 0.0;
    int total_dh_trades_ = 0;
    double la_pnl_ = 0.0;
    double dh_pnl_ = 0.0;

    std::unordered_map<std::string, int> asset_trades_;
    std::unordered_map<std::string, int> asset_wins_;
    std::unordered_map<std::string, double> asset_pnl_;

    std::deque<double> recent_la_pnls_;
    std::deque<double> recent_dh_pnls_;
    int circuit_breaker_window_;
    double circuit_breaker_resume_at_ = 0.0;
    double fee_rate_ = 0.018;
    mutable std::recursive_mutex mtx_;
};

} // namespace risk
