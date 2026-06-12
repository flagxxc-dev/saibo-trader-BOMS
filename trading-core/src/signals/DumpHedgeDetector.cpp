#include "DumpHedgeDetector.h"
#include <spdlog/spdlog.h>
#include <fmt/core.h>
#include <algorithm>
#include <cmath>

namespace trading {

namespace {

constexpr double kDebugLogIntervalSec = 30.0;

struct DhNearMiss {
    bool valid = false;
    std::string asset;
    int window_minutes = 0;
    double yes_price = 0.0;
    double no_price = 0.0;
    double combined = 0.0;
    double entry_fees = 0.0;
    double discount = 0.0;
    double seconds_remaining = 0.0;
    std::string reason;
    double score = -1e9;
};

std::string reject_label(const std::string& code) {
    if (code == "window_off") return "window_off";
    if (code == "asset_off") return "asset_off";
    if (code == "cooldown") return "cooldown";
    if (code == "min_time") return "min_time";
    if (code == "no_price") return "no_price";
    if (code == "bad_side") return "bad_side";
    if (code == "sum_high") return "sum_high";
    if (code == "disc_low") return "disc_low";
    return code;
}

void consider_near_miss(DhNearMiss& best, DhNearMiss candidate) {
    if (!candidate.valid) return;
    if (!best.valid || candidate.score > best.score) {
        best = std::move(candidate);
    }
}

} // namespace

static std::string dh_market_key(const MarketInfo& market) {
    return market.asset + "-" + std::to_string(market.window_minutes) + "m";
}

DumpHedgeDetector::DumpHedgeDetector(StateStore& state_store,
                                     std::vector<MarketInfo> active_markets,
                                     double sum_target,
                                     double min_discount,
                                     double min_seconds_remaining,
                                     double cooldown_seconds)
    : state_store_(state_store),
      active_markets_(std::move(active_markets)),
      sum_target_(sum_target),
      min_discount_(min_discount),
      min_seconds_remaining_(min_seconds_remaining),
      cooldown_seconds_(cooldown_seconds)
{
    std::string msg = fmt::format("DumpHedgeDetector initialized | Markets: {} | SumTarget: {:.2f} | MinDiscount: {:.2f}",
                                  active_markets_.size(), sum_target_, min_discount_);
    spdlog::log(spdlog::level::info, msg);
}

std::optional<DumpHedgeSignal> DumpHedgeDetector::evaluate(double current_time_ms) {
    evaluations_++;
    std::optional<DumpHedgeSignal> best_signal;
    DhNearMiss near_miss;

    for (const auto& market : active_markets_) {
        const std::string market_key = dh_market_key(market);
        if (last_signal_time_.contains(market_key)) {
            if ((current_time_ms - last_signal_time_.at(market_key)) / 1000.0 < cooldown_seconds_) {
                continue;
            }
        }

        if (market.window_minutes == 5 && !state_store_.dh_enable_5m()) {
            consider_near_miss(near_miss, DhNearMiss{
                .valid = true, .asset = market.asset, .window_minutes = market.window_minutes,
                .reason = "window_off", .score = -1000.0});
            continue;
        }
        if (market.window_minutes == 15 && !state_store_.dh_enable_15m()) {
            consider_near_miss(near_miss, DhNearMiss{
                .valid = true, .asset = market.asset, .window_minutes = market.window_minutes,
                .reason = "window_off", .score = -1000.0});
            continue;
        }
        if (!state_store_.dh_asset_enabled(market.window_minutes, market.asset)) {
            consider_near_miss(near_miss, DhNearMiss{
                .valid = true, .asset = market.asset, .window_minutes = market.window_minutes,
                .reason = "asset_off", .score = -900.0});
            continue;
        }

        double seconds_remaining = market.end_date_ts - (current_time_ms / 1000.0);
        if (seconds_remaining < min_seconds_remaining_) {
            consider_near_miss(near_miss, DhNearMiss{
                .valid = true, .asset = market.asset, .window_minutes = market.window_minutes,
                .seconds_remaining = seconds_remaining, .reason = "min_time", .score = -800.0});
            continue;
        }

        auto yes_price_opt = state_store_.get_token_price(market.yes_token_id);
        auto no_price_opt = state_store_.get_token_price(market.no_token_id);

        if (!yes_price_opt || !no_price_opt) {
            consider_near_miss(near_miss, DhNearMiss{
                .valid = true, .asset = market.asset, .window_minutes = market.window_minutes,
                .seconds_remaining = seconds_remaining, .reason = "no_price", .score = -700.0});
            continue;
        }

        if (yes_price_opt->side != "BUY" || no_price_opt->side != "BUY") {
            consider_near_miss(near_miss, DhNearMiss{
                .valid = true, .asset = market.asset, .window_minutes = market.window_minutes,
                .yes_price = yes_price_opt->price, .no_price = no_price_opt->price,
                .seconds_remaining = seconds_remaining, .reason = "bad_side", .score = -600.0});
            continue;
        }

        double yes_price = yes_price_opt->price;
        double no_price = no_price_opt->price;

        if (yes_price <= 0 || no_price <= 0) continue;

        double combined = yes_price + no_price;
        double entry_fees = state_store_.compute_dh_entry_fee_per_share(
            yes_price, no_price, market.yes_token_id, market.no_token_id);
        double discount = 1.0 - combined - entry_fees;

        DhNearMiss priced{
            .valid = true,
            .asset = market.asset,
            .window_minutes = market.window_minutes,
            .yes_price = yes_price,
            .no_price = no_price,
            .combined = combined,
            .entry_fees = entry_fees,
            .discount = discount,
            .seconds_remaining = seconds_remaining,
        };

        if (combined > sum_target_) {
            priced.reason = "sum_high";
            priced.score = discount - (combined - sum_target_);
            consider_near_miss(near_miss, priced);
            continue;
        }
        if (discount < min_discount_) {
            priced.reason = "disc_low";
            priced.score = discount;
            consider_near_miss(near_miss, priced);
            continue;
        }

        DumpHedgeSignal signal{
            .market = market,
            .asset = market.asset,
            .yes_token_id = market.yes_token_id,
            .no_token_id = market.no_token_id,
            .yes_price = yes_price,
            .no_price = no_price,
            .combined_price = combined,
            .discount = discount,
            .discount_pct = combined > 0 ? (discount / combined) : 0.0,
            .seconds_remaining = seconds_remaining,
            .timestamp = current_time_ms
        };

        if (!best_signal || signal.discount > best_signal->discount) {
            best_signal = signal;
        }
    }

    const double now_sec = current_time_ms / 1000.0;
    if (!best_signal && near_miss.valid && (now_sec - last_near_miss_log_sec_) >= kDebugLogIntervalSec) {
        last_near_miss_log_sec_ = now_sec;
        std::string detail;
        if (near_miss.reason == "sum_high") {
            detail = fmt::format("sum {:.4f} > target {:.4f} (over {:.4f})",
                                 near_miss.combined, sum_target_, near_miss.combined - sum_target_);
        } else if (near_miss.reason == "disc_low") {
            detail = fmt::format("disc {:.4f} < min {:.4f} (short {:.4f})",
                                 near_miss.discount, min_discount_, min_discount_ - near_miss.discount);
        } else if (near_miss.reason == "min_time") {
            detail = fmt::format("{:.0f}s left < min {:.0f}s", near_miss.seconds_remaining, min_seconds_remaining_);
        } else {
            detail = reject_label(near_miss.reason);
        }

        std::string msg;
        if (near_miss.combined > 0.0) {
            msg = fmt::format(
                "[DH DEBUG] near-miss | {} {}m | YES {:.4f} NO {:.4f} | sum {:.4f} fee {:.4f}/sh disc {:.4f} ({:.2f}%) | {:.0f}s left | {} | {}",
                near_miss.asset, near_miss.window_minutes,
                near_miss.yes_price, near_miss.no_price,
                near_miss.combined, near_miss.entry_fees, near_miss.discount,
                near_miss.discount / near_miss.combined * 100.0,
                near_miss.seconds_remaining, reject_label(near_miss.reason), detail);
        } else {
            msg = fmt::format(
                "[DH DEBUG] near-miss | {} {}m | {:.0f}s left | {} | {}",
                near_miss.asset, near_miss.window_minutes,
                near_miss.seconds_remaining, reject_label(near_miss.reason), detail);
        }
        spdlog::info(msg);
        state_store_.push_telemetry(msg);
    }

    if (best_signal) {
        last_signal_time_[dh_market_key(best_signal->market)] = current_time_ms;
        signals_generated_++;
        std::string msg = fmt::format("DUMP-HEDGE DETECTED [#{}] | {} {}m | YES: {:.3f} NO: {:.3f} | Sum: {:.3f} | Locked: {:.3f}/share",
                                      signals_generated_, best_signal->asset, best_signal->market.window_minutes,
                                      best_signal->yes_price, best_signal->no_price,
                                      best_signal->combined_price, best_signal->discount);
        spdlog::log(spdlog::level::info, msg);
    }

    return best_signal;
}

void DumpHedgeDetector::reset_cooldown(const std::string& asset, double current_time_ms) {
    last_signal_time_[asset] = current_time_ms;
}

} // namespace trading
