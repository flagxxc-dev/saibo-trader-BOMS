#pragma once

#include <string>

namespace model {

class FairValueModel {
public:
    /**
     * Compute fair probability using a time-aware sigmoid model.
     * P(UP) = sigmoid( (price_now - price_to_beat) / scale(t) )
     * scale(t) = base_scale * sqrt(t_frac) + min_scale
     */
    static double compute_fair_value_5m(
        double price_now,
        double price_to_beat,
        double seconds_remaining,
        const std::string& direction,
        double base_scale,
        double min_scale,
        double window_seconds = 300.0
    );
};

} // namespace model
