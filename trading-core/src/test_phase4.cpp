#include "state/StateStore.h"
#include "signals/LatencyArbDetector.h"
#include "signals/DumpHedgeDetector.h"
#include <spdlog/spdlog.h>
#include <vector>

using namespace trading;

int main() {
    spdlog::set_level(spdlog::level::info);
    spdlog::info("Starting Phase 4 Test (detectors)");

    StateStore state_store;

    MarketInfo btc_market{
        .condition_id = "cond_1",
        .question = "Bitcoin Up or Down - 5 Minutes?",
        .asset = "btc",
        .yes_token_id = "token_yes_1",
        .no_token_id = "token_no_1",
        .strike = 65000.0,
        .end_date_ts = 10300.0
    };

    std::vector<MarketInfo> markets = {btc_market};
    LatencyArbDetector la(state_store, markets, 0.04, 60.0, 0.0, 2.7, "btc");
    DumpHedgeDetector dh(state_store, markets, 0.95, 0.02, 60.0, 0.0);

    double t = 10000.0 * 1000.0;

    spdlog::info("--- TEST 1: No signals ---");
    state_store.update_btc_price({65000.0, t, 1.0, 10000.0});
    state_store.update_token_price("token_yes_1", {0.50, "BUY", t});
    state_store.update_token_price("token_no_1", {0.50, "BUY", t});
    if (!la.evaluate(t) && !dh.evaluate(t)) {
        spdlog::info("PASS: no LA/DH signals at fair market");
    }

    spdlog::info("--- TEST 2: Dump hedge ---");
    state_store.update_token_price("token_yes_1", {0.45, "BUY", t + 10000});
    state_store.update_token_price("token_no_1", {0.45, "BUY", t + 10000});
    if (dh.evaluate(t + 10000)) {
        spdlog::info("PASS: DH signal on combined 0.90");
    }

    spdlog::info("Phase 4 Test Completed!");
    return 0;
}
