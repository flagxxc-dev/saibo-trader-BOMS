#include "model/FairValueModel.h"
#include "risk/RiskManager.h"
#include <iostream>
#include <cassert>
#include <iomanip>

void test_fair_value_model() {
    std::cout << "--- Testing FairValueModel ---" << std::endl;
    // P(UP) = sigmoid( (price_now - price_to_beat) / scale(t) )
    // scale(t) = base_scale * sqrt(t_frac) + min_scale
    // Example: BTC 5-minute market
    double price_now = 83500.0;
    double price_to_beat = 83450.0;
    double seconds_remaining = 60.0;
    
    // btc scale = 150.0 base, 20.0 min
    double fair_value_up = model::FairValueModel::compute_fair_value_5m(
        price_now, price_to_beat, seconds_remaining, "UP", 150.0, 20.0
    );
    
    std::cout << "Price Now: " << price_now << " | PTB: " << price_to_beat << " | Sec Remaining: " << seconds_remaining << std::endl;
    std::cout << "Fair Value UP: " << std::fixed << std::setprecision(4) << fair_value_up << std::endl;

    double fair_value_down = model::FairValueModel::compute_fair_value_5m(
        price_now, price_to_beat, seconds_remaining, "DOWN", 150.0, 20.0
    );
    std::cout << "Fair Value DOWN: " << std::fixed << std::setprecision(4) << fair_value_down << std::endl;
    
    // Verify sum is 1.0
    assert(std::abs(fair_value_up + fair_value_down - 1.0) < 0.0001);
}

void test_risk_manager() {
    std::cout << "\n--- Testing RiskManager ---" << std::endl;
    
    risk::RiskManager rm(1000.0, 0.08, 0.20, 0.40, 3);
    
    std::cout << "Initial Balance: $" << rm.get_current_balance() << std::endl;
    
    // 1. Open trade
    risk::Position pos1{.order_id="order1", .token_id="t1", .market_question="Q1", .side="BUY", .entry_price=0.50, .size_shares=100, .cost_usdc=50.0, .opened_at=0.0};
    rm.register_trade_open(pos1);
    std::cout << "After pos1 open balance: $" << rm.get_current_balance() << std::endl;
    
    // 2. Win trade (sell at 0.75, +$25 profit)
    rm.register_trade_close("order1", 0.75);
    std::cout << "After pos1 close balance: $" << rm.get_current_balance() << std::endl;
    
    // 3. Lose heavily to trigger circuit breaker (3 losses)
    for (int i = 2; i <= 4; ++i) {
        std::string oid = "order" + std::to_string(i);
        risk::Position pos{.order_id=oid, .token_id="t2", .market_question="Q2", .side="BUY", .entry_price=0.50, .size_shares=100, .cost_usdc=50.0, .opened_at=0.0};
        rm.register_trade_open(pos);
        rm.register_trade_close(oid, 0.0); // complete loss (-$50)
    }
    
    std::cout << "After 3 losses balance: $" << rm.get_current_balance() << std::endl;
    std::cout << "Trading Status (0=ACTIVE, 1=DAILY_HALT, 2=KILLED, 3=PAUSED): " 
              << static_cast<int>(rm.get_status()) << std::endl;
              
    // 4. Force total drawdown to trigger kill switch
    rm.update_balance(500.0); // 50% drawdown from peak (~1025)
    std::cout << "After forcing drawdown balance: $" << rm.get_current_balance() << std::endl;
    std::cout << "Trading Status (0=ACTIVE, 1=DAILY_HALT, 2=KILLED, 3=PAUSED): " 
              << static_cast<int>(rm.get_status()) << std::endl;
}

int main() {
    test_fair_value_model();
    test_risk_manager();
    return 0;
}
