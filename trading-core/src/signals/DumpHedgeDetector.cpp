#include "DumpHedgeDetector.h"
#include <spdlog/spdlog.h>
#include <fmt/core.h>

namespace trading {

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

    for (const auto& market : active_markets_) {
        const std::string market_key = dh_market_key(market);
        if (last_signal_time_.contains(market_key)) {
            if ((current_time_ms - last_signal_time_.at(market_key)) / 1000.0 < cooldown_seconds_) {
                continue;
            }
        }

        if (market.window_minutes == 5 && !state_store_.dh_enable_5m()) continue;
        if (market.window_minutes == 15 && !state_store_.dh_enable_15m()) continue;
        if (!state_store_.dh_asset_enabled(market.window_minutes, market.asset)) continue;

        // Check time remaining
        double seconds_remaining = market.end_date_ts - (current_time_ms / 1000.0);
        if (seconds_remaining < min_seconds_remaining_) {
            continue;
        }

        auto yes_price_opt = state_store_.get_token_price(market.yes_token_id);
        auto no_price_opt = state_store_.get_token_price(market.no_token_id);

        if (!yes_price_opt || !no_price_opt) {
            continue;
        }
        
        // Ensure both prices represent ASKs (which we can BUY from)
        if (yes_price_opt->side != "BUY" || no_price_opt->side != "BUY") {
            continue;
        }

        double yes_price = yes_price_opt->price;
        double no_price = no_price_opt->price;

        if (yes_price <= 0 || no_price <= 0) continue;

        double combined = yes_price + no_price;
        double entry_fees = state_store_.compute_dh_entry_fee_per_share(
            yes_price, no_price, market.yes_token_id, market.no_token_id);
        double discount = 1.0 - combined - entry_fees;

        if (combined > sum_target_) continue;
        if (discount < min_discount_) continue;

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
