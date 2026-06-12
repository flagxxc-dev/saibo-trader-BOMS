#include "RiskManager.h"
#include <spdlog/spdlog.h>
#include <numeric>
#include <cmath>
#include <boost/json.hpp>

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
    // Use persisted trade counters — closed_* arrays may be truncated on paper-state export.
    int closed = total_trades_ + total_dh_trades_;
    if (closed == 0) return 0.0;
    double rate = static_cast<double>(winning_trades_) / closed;
    return std::min(rate, 1.0);
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

double RiskManager::get_max_position_fraction() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return max_position_fraction_;
}

double RiskManager::get_daily_loss_limit() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return daily_loss_limit_;
}

double RiskManager::get_total_drawdown_kill() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return total_drawdown_kill_;
}

int RiskManager::get_max_concurrent_positions() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    return max_concurrent_positions_;
}

void RiskManager::set_max_position_fraction(double v) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    max_position_fraction_ = v;
    spdlog::info("Risk config updated | max_position_fraction={:.2f}", v);
}

void RiskManager::set_daily_loss_limit(double v) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    daily_loss_limit_ = v;
    spdlog::info("Risk config updated | daily_loss_limit={:.2f}", v);
}

void RiskManager::set_total_drawdown_kill(double v) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    total_drawdown_kill_ = v;
    spdlog::info("Risk config updated | total_drawdown_kill={:.2f}", v);
}

void RiskManager::set_max_concurrent_positions(int v) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    max_concurrent_positions_ = v;
    spdlog::info("Risk config updated | max_concurrent_positions={}", v);
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

int RiskManager::close_legacy_la_positions() {
    struct LegacyClose { std::string id; double exit_price; double proceeds; };
    std::vector<LegacyClose> to_close;
    {
        std::lock_guard<std::recursive_mutex> lock(mtx_);
        for (const auto& [id, p] : open_positions_) {
            if (p.strategy == "LA") {
                to_close.push_back({id, p.entry_price, p.cost_usdc * (1.0 + fee_rate_)});
            }
        }
    }
    int closed = 0;
    for (const auto& lc : to_close) {
        if (register_trade_close(lc.id, lc.exit_price, std::nullopt, lc.proceeds).has_value()) {
            ++closed;
            spdlog::info("Legacy LA position closed | {} | proceeds ${:.2f}", lc.id, lc.proceeds);
        }
    }
    return closed;
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

namespace {

boost::json::object position_to_json(const Position& p) {
    boost::json::object o;
    o["order_id"] = p.order_id;
    o["token_id"] = p.token_id;
    o["market_question"] = p.market_question;
    o["side"] = p.side;
    o["entry_price"] = p.entry_price;
    o["size_shares"] = p.size_shares;
    o["cost_usdc"] = p.cost_usdc;
    o["opened_at"] = p.opened_at;
    o["end_date_ts"] = p.end_date_ts;
    o["asset"] = p.asset;
    o["direction"] = p.direction;
    o["strategy"] = p.strategy;
    o["condition_id"] = p.condition_id;
    o["paper_mode"] = p.paper_mode;
    o["peak_price"] = p.peak_price;
    o["is_neg_risk"] = p.is_neg_risk;
    if (p.closed_at) o["closed_at"] = *p.closed_at;
    if (p.exit_price) o["exit_price"] = *p.exit_price;
    if (p.pnl_usdc) o["pnl_usdc"] = *p.pnl_usdc;
    return o;
}

bool position_from_json(const boost::json::object& o, Position& p) {
    try {
        p.order_id = std::string(o.at("order_id").as_string());
        p.token_id = std::string(o.at("token_id").as_string());
        p.market_question = o.contains("market_question") ? std::string(o.at("market_question").as_string()) : "";
        p.side = o.contains("side") ? std::string(o.at("side").as_string()) : "BUY";
        p.entry_price = o.at("entry_price").as_double();
        p.size_shares = o.at("size_shares").as_double();
        p.cost_usdc = o.at("cost_usdc").as_double();
        p.opened_at = o.at("opened_at").as_double();
        p.end_date_ts = o.contains("end_date_ts") ? o.at("end_date_ts").as_double() : 0.0;
        p.asset = o.contains("asset") ? std::string(o.at("asset").as_string()) : "";
        p.direction = o.contains("direction") ? std::string(o.at("direction").as_string()) : "";
        p.strategy = o.contains("strategy") ? std::string(o.at("strategy").as_string()) : "LA";
        p.condition_id = o.contains("condition_id") ? std::string(o.at("condition_id").as_string()) : "";
        p.paper_mode = !o.contains("paper_mode") || o.at("paper_mode").as_bool();
        p.peak_price = o.contains("peak_price") ? o.at("peak_price").as_double() : 0.0;
        p.is_neg_risk = o.contains("is_neg_risk") && o.at("is_neg_risk").as_bool();
        p.closed_at = std::nullopt;
        p.exit_price = std::nullopt;
        p.pnl_usdc = std::nullopt;
        if (o.contains("closed_at")) p.closed_at = o.at("closed_at").as_double();
        if (o.contains("exit_price")) p.exit_price = o.at("exit_price").as_double();
        if (o.contains("pnl_usdc")) p.pnl_usdc = o.at("pnl_usdc").as_double();
        return true;
    } catch (...) {
        return false;
    }
}

boost::json::object dh_position_to_json(const DumpHedgePosition& p) {
    boost::json::object o;
    o["dh_id"] = p.dh_id;
    o["yes_order_id"] = p.yes_order_id;
    o["no_order_id"] = p.no_order_id;
    o["yes_token_id"] = p.yes_token_id;
    o["no_token_id"] = p.no_token_id;
    o["market_question"] = p.market_question;
    o["asset"] = p.asset;
    o["yes_entry_price"] = p.yes_entry_price;
    o["no_entry_price"] = p.no_entry_price;
    o["combined_entry_price"] = p.combined_entry_price;
    o["size_shares"] = p.size_shares;
    o["combined_cost_usdc"] = p.combined_cost_usdc;
    o["locked_profit_usdc"] = p.locked_profit_usdc;
    o["opened_at"] = p.opened_at;
    o["end_date_ts"] = p.end_date_ts;
    o["paper_mode"] = p.paper_mode;
    o["strategy"] = p.strategy;
    o["is_neg_risk"] = p.is_neg_risk;
    o["window_minutes"] = p.window_minutes;
    if (!p.condition_id.empty()) o["condition_id"] = p.condition_id;
    o["exit_reason"] = p.exit_reason;
    if (p.closed_at) o["closed_at"] = *p.closed_at;
    if (p.yes_exit_price) o["yes_exit_price"] = *p.yes_exit_price;
    if (p.no_exit_price) o["no_exit_price"] = *p.no_exit_price;
    if (p.pnl_usdc) o["pnl_usdc"] = *p.pnl_usdc;
    return o;
}

bool dh_position_from_json(const boost::json::object& o, DumpHedgePosition& p) {
    try {
        p.dh_id = std::string(o.at("dh_id").as_string());
        p.yes_order_id = o.contains("yes_order_id") ? std::string(o.at("yes_order_id").as_string()) : "";
        p.no_order_id = o.contains("no_order_id") ? std::string(o.at("no_order_id").as_string()) : "";
        p.yes_token_id = o.contains("yes_token_id") ? std::string(o.at("yes_token_id").as_string()) : "";
        p.no_token_id = o.contains("no_token_id") ? std::string(o.at("no_token_id").as_string()) : "";
        p.market_question = o.contains("market_question") ? std::string(o.at("market_question").as_string()) : "";
        p.asset = o.contains("asset") ? std::string(o.at("asset").as_string()) : "";
        p.yes_entry_price = o.at("yes_entry_price").as_double();
        p.no_entry_price = o.at("no_entry_price").as_double();
        p.combined_entry_price = o.at("combined_entry_price").as_double();
        p.size_shares = o.at("size_shares").as_double();
        p.combined_cost_usdc = o.at("combined_cost_usdc").as_double();
        p.locked_profit_usdc = o.contains("locked_profit_usdc") ? o.at("locked_profit_usdc").as_double() : 0.0;
        p.opened_at = o.at("opened_at").as_double();
        p.end_date_ts = o.contains("end_date_ts") ? o.at("end_date_ts").as_double() : 0.0;
        p.paper_mode = !o.contains("paper_mode") || o.at("paper_mode").as_bool();
        p.strategy = o.contains("strategy") ? std::string(o.at("strategy").as_string()) : "DH";
        p.is_neg_risk = o.contains("is_neg_risk") && o.at("is_neg_risk").as_bool();
        p.window_minutes = o.contains("window_minutes") ? static_cast<int>(o.at("window_minutes").as_int64()) : 5;
        p.condition_id = o.contains("condition_id") ? std::string(o.at("condition_id").as_string()) : "";
        p.exit_reason = o.contains("exit_reason") ? std::string(o.at("exit_reason").as_string()) : "";
        p.closed_at = std::nullopt;
        p.yes_exit_price = std::nullopt;
        p.no_exit_price = std::nullopt;
        p.pnl_usdc = std::nullopt;
        if (o.contains("closed_at")) p.closed_at = o.at("closed_at").as_double();
        if (o.contains("yes_exit_price")) p.yes_exit_price = o.at("yes_exit_price").as_double();
        if (o.contains("no_exit_price")) p.no_exit_price = o.at("no_exit_price").as_double();
        if (o.contains("pnl_usdc")) p.pnl_usdc = o.at("pnl_usdc").as_double();
        return true;
    } catch (...) {
        return false;
    }
}

boost::json::object string_int_map_to_json(const std::unordered_map<std::string, int>& m) {
    boost::json::object o;
    for (const auto& [k, v] : m) o[k] = v;
    return o;
}

boost::json::object string_double_map_to_json(const std::unordered_map<std::string, double>& m) {
    boost::json::object o;
    for (const auto& [k, v] : m) o[k] = v;
    return o;
}

void json_to_string_int_map(const boost::json::object& o, std::unordered_map<std::string, int>& m) {
    m.clear();
    for (const auto& kv : o) {
        if (kv.value().is_int64()) m[std::string(kv.key())] = static_cast<int>(kv.value().as_int64());
    }
}

void json_to_string_double_map(const boost::json::object& o, std::unordered_map<std::string, double>& m) {
    m.clear();
    for (const auto& kv : o) {
        if (kv.value().is_double()) m[std::string(kv.key())] = kv.value().as_double();
        else if (kv.value().is_int64()) m[std::string(kv.key())] = static_cast<double>(kv.value().as_int64());
    }
}

} // namespace

boost::json::object RiskManager::export_paper_state() const {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    boost::json::object root;
    root["version"] = 1;
    root["saved_at"] = now();
    root["starting_balance"] = starting_balance_;
    root["current_balance"] = current_balance_;
    root["peak_balance"] = peak_balance_;
    root["daily_starting_balance"] = daily_starting_balance_;
    root["daily_reset_time"] = daily_reset_time_;
    root["status"] = static_cast<int>(status_);
    if (kill_reason_) root["kill_reason"] = *kill_reason_;
    root["total_trades"] = total_trades_;
    root["winning_trades"] = winning_trades_;
    root["total_dh_trades"] = total_dh_trades_;
    root["total_pnl"] = total_pnl_;
    root["la_pnl"] = la_pnl_;
    root["dh_pnl"] = dh_pnl_;
    root["circuit_breaker_resume_at"] = circuit_breaker_resume_at_;

    boost::json::object open_la;
    for (const auto& [id, p] : open_positions_) open_la[id] = position_to_json(p);
    root["open_positions"] = std::move(open_la);

    boost::json::object open_dh;
    for (const auto& [id, p] : open_dh_positions_) open_dh[id] = dh_position_to_json(p);
    root["open_dh_positions"] = std::move(open_dh);

    boost::json::array closed_la;
    size_t la_start = closed_positions_.size() > 200 ? closed_positions_.size() - 200 : 0;
    for (size_t i = la_start; i < closed_positions_.size(); ++i) {
        closed_la.push_back(position_to_json(closed_positions_[i]));
    }
    root["closed_positions"] = std::move(closed_la);

    boost::json::array closed_dh;
    size_t dh_start = closed_dh_positions_.size() > 200 ? closed_dh_positions_.size() - 200 : 0;
    for (size_t i = dh_start; i < closed_dh_positions_.size(); ++i) {
        closed_dh.push_back(dh_position_to_json(closed_dh_positions_[i]));
    }
    root["closed_dh_positions"] = std::move(closed_dh);

    root["asset_trades"] = string_int_map_to_json(asset_trades_);
    root["asset_wins"] = string_int_map_to_json(asset_wins_);
    root["asset_pnl"] = string_double_map_to_json(asset_pnl_);

    boost::json::array la_pnls;
    for (double v : recent_la_pnls_) la_pnls.push_back(v);
    root["recent_la_pnls"] = std::move(la_pnls);

    boost::json::array dh_pnls;
    for (double v : recent_dh_pnls_) dh_pnls.push_back(v);
    root["recent_dh_pnls"] = std::move(dh_pnls);

    return root;
}

bool RiskManager::import_paper_state(const boost::json::object& doc) {
    std::lock_guard<std::recursive_mutex> lock(mtx_);
    try {
        if (!doc.contains("version") || doc.at("version").as_int64() != 1) return false;

        starting_balance_ = doc.at("starting_balance").as_double();
        current_balance_ = doc.at("current_balance").as_double();
        peak_balance_ = doc.contains("peak_balance") ? doc.at("peak_balance").as_double() : current_balance_;
        daily_starting_balance_ = doc.contains("daily_starting_balance")
            ? doc.at("daily_starting_balance").as_double() : current_balance_;
        daily_reset_time_ = doc.contains("daily_reset_time")
            ? doc.at("daily_reset_time").as_double() : next_midnight();

        status_ = static_cast<TradingStatus>(doc.contains("status") ? static_cast<int>(doc.at("status").as_int64()) : 0);
        kill_reason_ = std::nullopt;
        if (doc.contains("kill_reason") && doc.at("kill_reason").is_string()) {
            kill_reason_ = std::string(doc.at("kill_reason").as_string());
        }

        total_trades_ = doc.contains("total_trades") ? static_cast<int>(doc.at("total_trades").as_int64()) : 0;
        winning_trades_ = doc.contains("winning_trades") ? static_cast<int>(doc.at("winning_trades").as_int64()) : 0;
        total_dh_trades_ = doc.contains("total_dh_trades") ? static_cast<int>(doc.at("total_dh_trades").as_int64()) : 0;
        total_pnl_ = doc.contains("total_pnl") ? doc.at("total_pnl").as_double() : 0.0;
        la_pnl_ = doc.contains("la_pnl") ? doc.at("la_pnl").as_double() : 0.0;
        dh_pnl_ = doc.contains("dh_pnl") ? doc.at("dh_pnl").as_double() : 0.0;
        circuit_breaker_resume_at_ = doc.contains("circuit_breaker_resume_at")
            ? doc.at("circuit_breaker_resume_at").as_double() : 0.0;

        open_positions_.clear();
        if (doc.contains("open_positions") && doc.at("open_positions").is_object()) {
            for (const auto& kv : doc.at("open_positions").as_object()) {
                Position p;
                if (position_from_json(kv.value().as_object(), p)) {
                    open_positions_[p.order_id] = p;
                }
            }
        }

        open_dh_positions_.clear();
        if (doc.contains("open_dh_positions") && doc.at("open_dh_positions").is_object()) {
            for (const auto& kv : doc.at("open_dh_positions").as_object()) {
                DumpHedgePosition p;
                if (dh_position_from_json(kv.value().as_object(), p)) {
                    open_dh_positions_[p.dh_id] = p;
                }
            }
        }

        closed_positions_.clear();
        if (doc.contains("closed_positions") && doc.at("closed_positions").is_array()) {
            for (const auto& v : doc.at("closed_positions").as_array()) {
                Position p;
                if (position_from_json(v.as_object(), p)) closed_positions_.push_back(p);
            }
        }

        closed_dh_positions_.clear();
        if (doc.contains("closed_dh_positions") && doc.at("closed_dh_positions").is_array()) {
            for (const auto& v : doc.at("closed_dh_positions").as_array()) {
                DumpHedgePosition p;
                if (dh_position_from_json(v.as_object(), p)) closed_dh_positions_.push_back(p);
            }
        }

        if (doc.contains("asset_trades") && doc.at("asset_trades").is_object()) {
            json_to_string_int_map(doc.at("asset_trades").as_object(), asset_trades_);
        }
        if (doc.contains("asset_wins") && doc.at("asset_wins").is_object()) {
            json_to_string_int_map(doc.at("asset_wins").as_object(), asset_wins_);
        }
        if (doc.contains("asset_pnl") && doc.at("asset_pnl").is_object()) {
            json_to_string_double_map(doc.at("asset_pnl").as_object(), asset_pnl_);
        }

        recent_la_pnls_.clear();
        if (doc.contains("recent_la_pnls") && doc.at("recent_la_pnls").is_array()) {
            for (const auto& v : doc.at("recent_la_pnls").as_array()) {
                if (v.is_double()) recent_la_pnls_.push_back(v.as_double());
            }
        }

        recent_dh_pnls_.clear();
        if (doc.contains("recent_dh_pnls") && doc.at("recent_dh_pnls").is_array()) {
            for (const auto& v : doc.at("recent_dh_pnls").as_array()) {
                if (v.is_double()) recent_dh_pnls_.push_back(v.as_double());
            }
        }

        return true;
    } catch (const std::exception& e) {
        spdlog::warn("import_paper_state failed: {}", e.what());
        return false;
    }
}

} // namespace risk
