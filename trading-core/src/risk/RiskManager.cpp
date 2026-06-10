#include "RiskManager.h"
#include <spdlog/spdlog.h>
#include <numeric>
#include <cmath>

namespace risk {

double RiskManager::now() {
    auto duration = std::chrono::system_clock::now().time_since_epoch();
    return std::chrono::duration<double>(duration).count();
}

double RiskManager::next_midnight() {
    auto now_t = std::chrono::system_clock::now();
    time_t t_now = std::chrono::system_clock::to_time_t(now_t);
    tm* utc_tm = gmtime(&t_now);
    
    utc_tm->tm_hour = 0;
    utc_tm->tm_min = 0;
    utc_tm->tm_sec = 0;
    
#ifdef _WIN32
    time_t midnight = _mkgmtime(utc_tm);
#else
    time_t midnight = timegm(utc_tm);
#endif
    
    midnight += 86400; // Next day
    return static_cast<double>(midnight);
}

RiskManager::RiskManager(
    double starting_balance,
    double max_position_fraction,
    double daily_loss_limit,
    double total_drawdown_kill,
    int max_concurrent_positions,
    bool circuit_breaker_enabled,
    int circuit_breaker_min_losses,
    int circuit_breaker_window,
    double circuit_breaker_loss_pct,
    double circuit_breaker_pause_seconds,
    double min_order_size
) : max_position_fraction_(max_position_fraction),
    daily_loss_limit_(daily_loss_limit),
    total_drawdown_kill_(total_drawdown_kill),
    max_concurrent_positions_(max_concurrent_positions),
    circuit_breaker_enabled_(circuit_breaker_enabled),
    circuit_breaker_min_losses_(circuit_breaker_min_losses),
    circuit_breaker_loss_pct_(circuit_breaker_loss_pct),
    circuit_breaker_pause_seconds_(circuit_breaker_pause_seconds),
    min_order_size_(min_order_size),
    starting_balance_(starting_balance),
    current_balance_(starting_balance),
    peak_balance_(starting_balance),
    daily_starting_balance_(starting_balance),
    daily_reset_time_(next_midnight()),
    status_(TradingStatus::ACTIVE),
    kill_reason_(std::nullopt),
    circuit_breaker_window_(circuit_breaker_window)
{
    spdlog::info(
        "RiskManager initialized | Balance: ${:.2f} | "
        "Max position: {:.0f}% | Daily limit: -{:.0f}% | Kill switch: -{:.0f}%",
        starting_balance_,
        max_position_fraction_ * 100.0,
        daily_loss_limit_ * 100.0,
        total_drawdown_kill_ * 100.0
    );
}

TradingStatus RiskManager::get_status() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return status_;
}

bool RiskManager::is_trading_allowed() {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    check_daily_reset();
    check_circuit_breaker_resume();
    return status_ == TradingStatus::ACTIVE;
}

double RiskManager::get_current_balance() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return current_balance_;
}

double RiskManager::get_total_equity() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    double equity = current_balance_;
    for (const auto& [id, p] : open_positions_) {
        (void)id;
        equity += p.cost_usdc;
    }
    for (const auto& [id, p] : open_dh_positions_) {
        (void)id;
        equity += p.combined_cost_usdc;
    }
    return equity;
}

std::optional<std::string> RiskManager::get_status_reason() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return kill_reason_;
}

double RiskManager::get_daily_starting_balance() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return daily_starting_balance_;
}

double RiskManager::get_peak_balance() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return peak_balance_;
}

double RiskManager::get_starting_balance() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return starting_balance_;
}

int RiskManager::get_open_position_count() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return open_positions_.size() + open_dh_positions_.size();
}

double RiskManager::get_win_rate() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    int closed = closed_positions_.size() + closed_dh_positions_.size();
    if (closed == 0) return 0.0;
    return static_cast<double>(winning_trades_) / closed;
}

double RiskManager::get_min_order_size() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return min_order_size_;
}

double RiskManager::get_la_pnl() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return la_pnl_;
}

double RiskManager::get_dh_pnl() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return dh_pnl_;
}

int RiskManager::get_total_trades() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return total_trades_;
}

int RiskManager::get_total_dh_trades() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return total_dh_trades_;
}

int RiskManager::get_winning_trades() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return winning_trades_;
}

const std::unordered_map<std::string, int>& RiskManager::get_asset_trades() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return asset_trades_;
}

const std::unordered_map<std::string, int>& RiskManager::get_asset_wins() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return asset_wins_;
}

const std::unordered_map<std::string, double>& RiskManager::get_asset_pnl() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return asset_pnl_;
}

std::unordered_map<std::string, Position> RiskManager::get_open_positions() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return open_positions_;
}

std::unordered_map<std::string, DumpHedgePosition> RiskManager::get_open_dh_positions() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return open_dh_positions_;
}

std::vector<Position> RiskManager::get_closed_positions() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return closed_positions_;
}

std::vector<DumpHedgePosition> RiskManager::get_closed_dh_positions() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return closed_dh_positions_;
}

void RiskManager::update_peak_price(const std::string& order_id, double peak_price) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    auto it = open_positions_.find(order_id);
    if (it != open_positions_.end()) {
        it->second.peak_price = peak_price;
    }
}

bool RiskManager::is_trading_allowed_no_lock() {
    check_daily_reset();
    check_circuit_breaker_resume();
    return status_ == TradingStatus::ACTIVE;
}

std::pair<bool, std::string> RiskManager::can_open_position(double position_size_usdc) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (!is_trading_allowed()) {
        return {false, "Trading halted: " + kill_reason_.value_or("N/A")};
    }

    if (get_open_position_count() >= max_concurrent_positions_) {
        return {false, "Max concurrent positions reached (" + std::to_string(max_concurrent_positions_) + ")"};
    }

    double max_allowed = current_balance_ * max_position_fraction_;
    if (position_size_usdc > max_allowed) {
        return {false, "Position size exceeds max allowed (" + std::to_string(max_position_fraction_ * 100.0) + "% of balance)"};
    }

    if (position_size_usdc > current_balance_) {
        return {false, "Insufficient balance"};
    }

    if (position_size_usdc < min_order_size_) {
        return {false, "Position size $" + std::to_string(position_size_usdc) + " below minimum $" + std::to_string(min_order_size_)};
    }

    return {true, "OK"};
}

void RiskManager::register_trade_open(const Position& position) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    open_positions_[position.order_id] = position;
    double entry_fee = position.cost_usdc * fee_rate_;
    current_balance_ -= (position.cost_usdc + entry_fee);

    spdlog::info("Position OPENED | {} | ${:.2f} USDC (+ ${:.2f} fee) | Balance: ${:.2f}",
                 position.order_id, position.cost_usdc, entry_fee, current_balance_);
}

std::optional<Position> RiskManager::register_trade_close(
    const std::string& order_id,
    double exit_price,
    std::optional<double> exit_timestamp,
    std::optional<double> actual_proceeds_usdc
) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    auto it = open_positions_.find(order_id);
    if (it == open_positions_.end()) {
        spdlog::warn("register_trade_close: order_id {} not found", order_id);
        return std::nullopt;
    }

    Position pos = it->second;
    open_positions_.erase(it);

    if (exit_price < 0.0 || exit_price > 1.0) {
        spdlog::error("Invalid exit_price {:.3f} for {} | Expected: 0.000-1.000", exit_price, order_id);
        open_positions_[order_id] = pos;
        return std::nullopt;
    }

    pos.closed_at = exit_timestamp.value_or(now());
    pos.exit_price = exit_price;

    double pnl = 0.0;
    if (actual_proceeds_usdc.has_value()) {
        pnl = actual_proceeds_usdc.value() - pos.cost_usdc - (pos.cost_usdc * fee_rate_);
        current_balance_ += actual_proceeds_usdc.value();
    } else {
        double gross = exit_price * pos.size_shares;
        double exit_fee = gross * fee_rate_;
        double net = gross - exit_fee;
        double entry_fee = pos.cost_usdc * fee_rate_;
        pnl = net - pos.cost_usdc - entry_fee;
        current_balance_ += net;
    }

    pos.pnl_usdc = pnl;
    total_pnl_ += pnl;
    la_pnl_ += pnl;
    total_trades_++;
    
    bool won = pnl > 0.0;
    if (won) winning_trades_++;

    record_asset_close(pos.asset, pnl, won);

    if (current_balance_ > peak_balance_) {
        peak_balance_ = current_balance_;
    }

    closed_positions_.push_back(pos);
    if (closed_positions_.size() > 1000) {
        closed_positions_.erase(closed_positions_.begin());
    }

    spdlog::info("Position CLOSED | {} | PnL: ${:+.2f} | Balance: ${:.2f} | Win rate: {:.1f}%",
                 order_id, pnl, current_balance_, get_win_rate() * 100.0);

    check_risk_thresholds();
    
    recent_la_pnls_.push_back(pnl);
    if(recent_la_pnls_.size() > static_cast<size_t>(circuit_breaker_window_)) {
        recent_la_pnls_.pop_front();
    }
    
    check_circuit_breaker();

    return pos;
}

std::pair<bool, std::string> RiskManager::can_open_dh_position(double combined_cost_usdc) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (!is_trading_allowed()) {
        return {false, "Trading halted: " + kill_reason_.value_or("N/A")};
    }

    if (get_open_position_count() >= max_concurrent_positions_) {
        return {false, "Max concurrent positions reached (" + std::to_string(max_concurrent_positions_) + ")"};
    }

    double max_allowed = current_balance_ * max_position_fraction_;
    if (combined_cost_usdc > max_allowed) {
        return {false, "DH cost exceeds max allowed"};
    }

    if (combined_cost_usdc > current_balance_) {
        return {false, "Insufficient balance"};
    }

    return {true, "OK"};
}

void RiskManager::register_dh_open(const DumpHedgePosition& position) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    open_dh_positions_[position.dh_id] = position;
    double entry_fee = position.combined_cost_usdc * fee_rate_;
    current_balance_ -= (position.combined_cost_usdc + entry_fee);

    spdlog::info("DH Position OPENED | {} | ${:.2f} USDC (+ ${:.2f} fee) | Locked: ${:.2f} | Balance: ${:.2f}",
                 position.dh_id, position.combined_cost_usdc, entry_fee, position.locked_profit_usdc, current_balance_);
}

std::optional<DumpHedgePosition> RiskManager::register_dh_close(
    const std::string& dh_id,
    double yes_exit_price,
    double no_exit_price,
    const std::string& exit_reason,
    std::optional<double> exit_timestamp,
    std::optional<double> actual_proceeds_usdc
) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    auto it = open_dh_positions_.find(dh_id);
    if (it == open_dh_positions_.end()) {
        spdlog::warn("register_dh_close: dh_id {} not found", dh_id);
        return std::nullopt;
    }

    DumpHedgePosition pos = it->second;
    open_dh_positions_.erase(it);

    pos.closed_at = exit_timestamp.value_or(now());
    pos.yes_exit_price = yes_exit_price;
    pos.no_exit_price = no_exit_price;
    pos.exit_reason = exit_reason;

    double pnl = 0.0;
    if (actual_proceeds_usdc.has_value()) {
        pnl = actual_proceeds_usdc.value() - pos.combined_cost_usdc - (pos.combined_cost_usdc * fee_rate_);
        current_balance_ += actual_proceeds_usdc.value();
    } else {
        double gross = (yes_exit_price + no_exit_price) * pos.size_shares;
        double exit_fee = gross * fee_rate_;
        double net = gross - exit_fee;
        double entry_fee = pos.combined_cost_usdc * fee_rate_;
        pnl = net - pos.combined_cost_usdc - entry_fee;
        current_balance_ += net;
    }

    pos.pnl_usdc = pnl;
    total_pnl_ += pnl;
    dh_pnl_ += pnl;
    total_dh_trades_++;

    bool won = pnl > 0.0;
    if (won) winning_trades_++;

    record_asset_close(pos.asset, pnl, won);

    if (current_balance_ > peak_balance_) {
        peak_balance_ = current_balance_;
    }

    closed_dh_positions_.push_back(pos);
    if (closed_dh_positions_.size() > 1000) {
        closed_dh_positions_.erase(closed_dh_positions_.begin());
    }

    spdlog::info("DH Position CLOSED | {} | PnL: ${:+.2f} | Reason: {} | Balance: ${:.2f}",
                 dh_id, pnl, exit_reason, current_balance_);

    check_risk_thresholds();
    
    recent_dh_pnls_.push_back(pnl);
    if(recent_dh_pnls_.size() > static_cast<size_t>(circuit_breaker_window_)) {
        recent_dh_pnls_.pop_front();
    }
    
    check_circuit_breaker();

    return pos;
}

void RiskManager::update_balance(double new_balance) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    double old = current_balance_;
    current_balance_ = new_balance;
    if (new_balance > peak_balance_) {
        peak_balance_ = new_balance;
    }
    spdlog::debug("Balance updated: ${:.2f} -> ${:.2f} (Δ${:+.2f})", old, new_balance, new_balance - old);
    check_risk_thresholds();
}

void RiskManager::set_daily_starting_balance(double balance) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    daily_starting_balance_ = balance;
    spdlog::debug("Daily starting balance set to ${:.2f}", balance);
}

void RiskManager::set_live_starting_balance(double balance) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    starting_balance_ = balance;
    current_balance_ = balance;
    peak_balance_ = balance;
    daily_starting_balance_ = balance;

    if (status_ == TradingStatus::DAILY_HALT || status_ == TradingStatus::KILLED) {
        status_ = TradingStatus::ACTIVE;
        kill_reason_ = std::nullopt;
    }

    spdlog::info("Live baseline balances reset to ${:.2f}", balance);
}

void RiskManager::pause(const std::string& reason) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (status_ == TradingStatus::ACTIVE) {
        status_ = TradingStatus::PAUSED;
        kill_reason_ = reason;
        spdlog::warn("Trading PAUSED: {}", reason);
    }
}

bool RiskManager::resume() {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (status_ == TradingStatus::KILLED) {
        spdlog::error("Cannot resume: kill switch has been triggered.");
        return false;
    }
    if (status_ == TradingStatus::PAUSED) {
        status_ = TradingStatus::ACTIVE;
        kill_reason_ = std::nullopt;
        spdlog::info("Trading RESUMED.");
    }
    return true;
}

bool RiskManager::reset_kill_switch(bool confirm) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (!confirm) {
        spdlog::error("reset_kill_switch requires confirm=true.");
        return false;
    }
    if (status_ == TradingStatus::KILLED) {
        status_ = TradingStatus::ACTIVE;
        kill_reason_ = std::nullopt;
        daily_starting_balance_ = current_balance_;
        spdlog::warn("KILL SWITCH RESET manually. Trading resumed. Balance: ${:.2f}", current_balance_);
    }
    return true;
}

void RiskManager::check_risk_thresholds() {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (status_ == TradingStatus::KILLED) return;

    const double equity = [&]() {
        double e = current_balance_;
        for (const auto& [id, p] : open_positions_) {
            (void)id;
            e += p.cost_usdc;
        }
        for (const auto& [id, p] : open_dh_positions_) {
            (void)id;
            e += p.combined_cost_usdc;
        }
        return e;
    }();

    if (equity > peak_balance_) {
        peak_balance_ = equity;
    }

    if (peak_balance_ > 0) {
        double drawdown_pct = (peak_balance_ - equity) / peak_balance_;
        if (drawdown_pct >= total_drawdown_kill_) {
            trigger_kill_switch(
                "Total drawdown " + std::to_string(drawdown_pct * 100.0) + "% exceeded threshold. " +
                "Peak: $" + std::to_string(peak_balance_) + " -> Equity: $" + std::to_string(equity)
            );
            return;
        }
    }

    if (daily_starting_balance_ > 0) {
        double daily_loss_pct = (daily_starting_balance_ - equity) / daily_starting_balance_;
        if (daily_loss_pct >= daily_loss_limit_) {
            trigger_daily_halt(
                "Daily loss " + std::to_string(daily_loss_pct * 100.0) + "% exceeded limit. " +
                "Daily start: $" + std::to_string(daily_starting_balance_) + " -> Equity: $" + std::to_string(equity)
            );
        }
    }
}

void RiskManager::check_circuit_breaker() {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (!circuit_breaker_enabled_) return;
    if (status_ != TradingStatus::ACTIVE) return;

    double threshold = current_balance_ * circuit_breaker_loss_pct_;

    auto evaluate_cb = [&](const std::deque<double>& pnls, const std::string& strategy) -> std::optional<std::string> {
        if (pnls.size() < static_cast<size_t>(circuit_breaker_min_losses_)) return std::nullopt;

        std::vector<double> losses;
        for (double p : pnls) {
            if (p < 0.0) losses.push_back(p);
        }

        if (losses.size() < static_cast<size_t>(circuit_breaker_min_losses_)) return std::nullopt;

        double cumulative_loss = 0.0;
        for (double l : losses) cumulative_loss += std::abs(l);

        if (cumulative_loss >= threshold) {
            double resume_at = now() + circuit_breaker_pause_seconds_;
            circuit_breaker_resume_at_ = resume_at;
            status_ = TradingStatus::PAUSED;
            kill_reason_ = "Circuit breaker (" + strategy + "): " + std::to_string(losses.size()) + " losses in last " +
                           std::to_string(pnls.size()) + " trades, cumulative loss $" + std::to_string(cumulative_loss) +
                           " > " + std::to_string(circuit_breaker_loss_pct_ * 100.0) + "% of balance. Pausing.";
                           
            return fmt::format("CIRCUIT BREAKER triggered [{}] - trading paused for {}s. Resumes at unix {}.", strategy, circuit_breaker_pause_seconds_, resume_at);
        }
        return std::nullopt;
    };

    if (auto msg = evaluate_cb(recent_la_pnls_, "LA")) {
        spdlog::warn("{}", *msg);
        return;
    }
    if (auto msg = evaluate_cb(recent_dh_pnls_, "DH")) {
        spdlog::warn("{}", *msg);
        return;
    }
}

void RiskManager::check_circuit_breaker_resume() {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (status_ == TradingStatus::PAUSED && circuit_breaker_resume_at_ > 0.0 && now() >= circuit_breaker_resume_at_) {
        circuit_breaker_resume_at_ = 0.0;
        status_ = TradingStatus::ACTIVE;
        kill_reason_ = std::nullopt;
        recent_la_pnls_.clear();
        recent_dh_pnls_.clear();
        spdlog::info("Circuit breaker pause expired - trading RESUMED.");
    }
}

void RiskManager::trigger_kill_switch(const std::string& reason) {
    status_ = TradingStatus::KILLED;
    kill_reason_ = reason;
    spdlog::critical("KILL SWITCH TRIGGERED - ALL TRADING HALTED");
    spdlog::critical("Reason: {}", reason);
    spdlog::critical("Call reset_kill_switch(confirm=true) to resume.");
}

void RiskManager::trigger_daily_halt(const std::string& reason) {
    if (status_ != TradingStatus::DAILY_HALT) {
        status_ = TradingStatus::DAILY_HALT;
        kill_reason_ = reason;
        spdlog::warn("DAILY HALT TRIGGERED - Trading paused until midnight UTC.");
        spdlog::warn("Reason: {}", reason);
    }
}

void RiskManager::check_daily_reset() {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (now() >= daily_reset_time_) {
        if (status_ == TradingStatus::DAILY_HALT) {
            status_ = TradingStatus::ACTIVE;
            kill_reason_ = std::nullopt;
            spdlog::info("Daily halt reset at midnight UTC. Trading resumed. "
                         "New daily starting balance: ${:.2f}", current_balance_);
        }
        daily_starting_balance_ = current_balance_;
        daily_reset_time_ = next_midnight();
    }
}

void RiskManager::record_asset_close(const std::string& asset, double pnl, bool won) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    if (asset.empty()) return;
    asset_trades_[asset]++;
    if (won) asset_wins_[asset]++;
    asset_pnl_[asset] += pnl;
}

} // namespace risk
