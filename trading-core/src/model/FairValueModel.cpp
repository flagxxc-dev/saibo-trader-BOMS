#include "FairValueModel.h"
#include <cmath>
#include <algorithm>

namespace model {

double FairValueModel::compute_fair_value_5m(
    double price_now,
    double price_to_beat,
    double seconds_remaining,
    const std::string& direction,
    double base_scale,
    double min_scale,
    double window_seconds
) {
    double t_frac = std::max(0.01, seconds_remaining / window_seconds);
    double scale = base_scale * std::sqrt(t_frac) + min_scale;

    double distance = (price_now - price_to_beat) / scale;
    double p_up = 1.0 / (1.0 + std::exp(-distance));

    double fair_value = (direction == "UP") ? p_up : (1.0 - p_up);
    return std::max(0.01, std::min(0.99, fair_value));
}

} // namespace model
