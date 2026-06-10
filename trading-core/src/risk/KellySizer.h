#pragma once
#include <optional>
#include <string>

namespace trading {

struct KellyResult {
    double kelly_fraction;
    double fractional_kelly;
    double position_size_usdc;
    double bankroll;
    double win_probability;
    double current_price;
    double edge;
    double net_odds;
    bool capped = false;

    std::string to_string() const;
};

class KellySizer {
public:
    KellySizer(
        double kelly_fraction = 0.5,
        double max_position_fraction = 0.08,
        double min_position_usdc = 1.0,
        double fixed_bet_usdc = 0.0,
        bool adaptive_kelly_enabled = false,
        double adaptive_kelly_floor = 0.1
    );

    std::optional<KellyResult> calculate(
        double bankroll,
        double win_probability,
        double current_price,
        std::optional<double> historical_win_rate = std::nullopt
    ) const;

    double expected_value(double win_probability, double current_price) const;

private:
    double effective_kelly_fraction(std::optional<double> win_rate) const;

    double kelly_fraction_;
    double max_position_fraction_;
    double min_position_usdc_;
    double fixed_bet_usdc_;
    bool adaptive_kelly_enabled_;
    double adaptive_kelly_floor_;
};

} // namespace trading
