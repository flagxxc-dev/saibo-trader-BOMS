#include "KellySizer.h"
#include <cmath>
#include <sstream>
#include <iomanip>
#include <iostream>
#include <algorithm>

namespace trading {

std::string KellyResult::to_string() const {
    std::stringstream ss;
    ss << std::fixed << std::setprecision(4);
    ss << "Kelly: raw=" << kelly_fraction << " | ";
    ss << "fractional=" << fractional_kelly << " | ";
    ss << std::setprecision(2) << "size=$" << position_size_usdc << " USDC | ";
    ss << std::setprecision(4) << "edge=" << edge << " | ";
    ss << (capped ? "CAPPED" : "uncapped");
    return ss.str();
}

KellySizer::KellySizer(
    double kelly_fraction,
    double max_position_fraction,
    double min_position_usdc,
    double fixed_bet_usdc,
    bool adaptive_kelly_enabled,
    double adaptive_kelly_floor
) : kelly_fraction_(kelly_fraction),
    max_position_fraction_(max_position_fraction),
    min_position_usdc_(min_position_usdc),
    fixed_bet_usdc_(fixed_bet_usdc),
    adaptive_kelly_enabled_(adaptive_kelly_enabled),
    adaptive_kelly_floor_(adaptive_kelly_floor) 
{}

double KellySizer::effective_kelly_fraction(std::optional<double> win_rate) const {
    if (!adaptive_kelly_enabled_ || !win_rate.has_value()) {
        return kelly_fraction_;
    }

    double rate = win_rate.value();
    double floor = kelly_fraction_ * adaptive_kelly_floor_;
    double fraction;

    if (rate < 0.45) {
        fraction = floor;
    } else if (rate < 0.50) {
        double t = (rate - 0.45) / 0.05;
        fraction = floor + t * (kelly_fraction_ - floor);
    } else if (rate < 0.55) {
        fraction = kelly_fraction_;
    } else {
        fraction = std::min(kelly_fraction_ * 1.25, 1.0);
    }

    return fraction;
}

std::optional<KellyResult> KellySizer::calculate(
    double bankroll,
    double win_probability,
    double current_price,
    std::optional<double> historical_win_rate
) const {
    if (win_probability <= 0.0 || win_probability >= 1.0) return std::nullopt;
    if (current_price <= 0.0 || current_price >= 1.0) return std::nullopt;
    if (bankroll <= 0.0) return std::nullopt;

    if (fixed_bet_usdc_ > 0.0) {
        double max_allowed = bankroll * max_position_fraction_;
        double position_size = std::min(fixed_bet_usdc_, max_allowed);
        bool capped = position_size < fixed_bet_usdc_;

        if (position_size < min_position_usdc_) return std::nullopt;

        double b = (1.0 - current_price) / current_price;
        double p = win_probability;
        double q = 1.0 - p;
        double raw_kelly = (p * b - q) / b;
        double fractional = position_size / bankroll;

        return KellyResult{
            raw_kelly,
            fractional,
            std::round(position_size * 100.0) / 100.0,
            bankroll,
            p,
            current_price,
            p - current_price,
            b,
            capped
        };
    }

    double b = (1.0 - current_price) / current_price;
    double p = win_probability;
    double q = 1.0 - p;
    double raw_kelly = (p * b - q) / b;

    if (raw_kelly <= 0.0) return std::nullopt;

    double eff_fraction = effective_kelly_fraction(historical_win_rate);
    double fractional = raw_kelly * eff_fraction;

    bool capped = false;
    if (fractional > max_position_fraction_) {
        fractional = max_position_fraction_;
        capped = true;
    }

    double position_size = bankroll * fractional;
    if (position_size < min_position_usdc_) return std::nullopt;

    return KellyResult{
        raw_kelly,
        fractional,
        std::round(position_size * 100.0) / 100.0,
        bankroll,
        p,
        current_price,
        p - current_price,
        b,
        capped
    };
}

double KellySizer::expected_value(double win_probability, double current_price) const {
    if (current_price <= 0.0) return 0.0;
    return (win_probability / current_price) - 1.0;
}

} // namespace trading
