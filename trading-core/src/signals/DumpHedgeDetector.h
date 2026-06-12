#pragma once
#include "Signal.h"
#include "../state/StateStore.h"
#include <vector>
#include <string>
#include <optional>
#include <unordered_map>

namespace trading {

class DumpHedgeDetector {
public:
    DumpHedgeDetector(StateStore& state_store, 
                      std::vector<MarketInfo> active_markets,
                      double sum_target = 0.95,
                      double min_discount = 0.03,
                      double min_seconds_remaining = 60.0,
                      double cooldown_seconds = 30.0);

    std::optional<DumpHedgeSignal> evaluate(double current_time_ms);
    void set_active_markets(std::vector<MarketInfo> markets) { active_markets_ = std::move(markets); }
    void reset_cooldown(const std::string& asset, double current_time_ms);
    
    void set_sum_target(double val) { sum_target_ = val; }
    void set_min_discount(double val) { min_discount_ = val; }
    void set_fee_rate(double val) { fee_rate_ = val; }
    void set_cooldown_seconds(double val) { cooldown_seconds_ = val; }
    void set_min_seconds_remaining(double val) { min_seconds_remaining_ = val; }

private:
    StateStore& state_store_;
    std::vector<MarketInfo> active_markets_;
    double sum_target_;
    double min_discount_;
    double min_seconds_remaining_;
    double cooldown_seconds_;
    double fee_rate_ = 0.018;

    std::unordered_map<std::string, double> last_signal_time_;
    double last_near_miss_log_sec_ = 0.0;
    int evaluations_ = 0;
    int signals_generated_ = 0;
};

} // namespace trading
