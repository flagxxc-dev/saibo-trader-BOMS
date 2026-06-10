#pragma once
#include <string>

namespace trading {

struct MarketInfo {
    std::string condition_id;
    std::string question;
    std::string asset;
    std::string yes_token_id;
    std::string no_token_id;
    double strike;
    double end_date_ts; // UNIX timestamp in seconds
    std::string end_date_iso; // ISO-8601 string for display
    double volume = 0.0; // 24h volume in USD for sorting
    double yes_price = 0.5;
    double no_price = 0.5;
    double liquidity = 0.0;
    // Neg-risk markets (all Polymarket 5m/15m Up-Down markets) require a
    // different EIP-712 verifying contract. Must be detected from Gamma API
    // and threaded through to the signer — wrong contract → order_version_mismatch.
    bool is_neg_risk = false;
};

struct DumpHedgeSignal {
    MarketInfo market;
    std::string asset;
    std::string yes_token_id;
    std::string no_token_id;
    double yes_price;
    double no_price;
    double combined_price;
    double discount;
    double discount_pct;
    double seconds_remaining;
    double timestamp;
};

struct LatencyArbSignal {
    MarketInfo market;
    std::string asset;
    std::string token_id;
    std::string side; // "BUY" or "SELL"
    double polymarket_price;
    double binance_price;
    double fair_value;
    double edge;
    double seconds_remaining;
    double timestamp;
};

} // namespace trading
