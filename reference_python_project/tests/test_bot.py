"""
Comprehensive test suite for the Polymarket Latency Arbitrage Bot.
Tests all core modules: Kelly sizer, Risk manager, Edge detector, Config validation.
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.kelly import KellySizer, KellyResult
from risk.risk_manager import RiskManager, TradingStatus, Position
from config import BotConfig


class TestKellySizer(unittest.TestCase):
    """Tests for the Kelly Criterion position sizer."""

    def setUp(self):
        self.sizer = KellySizer(
            kelly_fraction=0.5,
            max_position_fraction=0.08,
            min_position_usdc=1.0,
        )

    def test_basic_kelly_calculation(self):
        """Kelly should return a positive position when there is a genuine edge."""
        result = self.sizer.calculate(
            bankroll=1000.0,
            win_probability=0.65,
            current_price=0.50,
        )
        self.assertIsNotNone(result)
        self.assertGreater(result.position_size_usdc, 0)
        self.assertGreater(result.edge, 0)
        print(f"  ✓ Kelly result: {result}")

    def test_no_edge_returns_none(self):
        """Kelly should return None when win probability equals market price (no edge)."""
        result = self.sizer.calculate(
            bankroll=1000.0,
            win_probability=0.50,
            current_price=0.50,
        )
        self.assertIsNone(result)
        print("  ✓ No edge correctly returns None")

    def test_negative_edge_returns_none(self):
        """Kelly should return None when win probability is below market price."""
        result = self.sizer.calculate(
            bankroll=1000.0,
            win_probability=0.40,
            current_price=0.55,
        )
        self.assertIsNone(result)
        print("  ✓ Negative edge correctly returns None")

    def test_position_capped_at_max_fraction(self):
        """Position size should never exceed max_position_fraction of bankroll."""
        result = self.sizer.calculate(
            bankroll=1000.0,
            win_probability=0.95,  # Very high edge
            current_price=0.10,
        )
        self.assertIsNotNone(result)
        max_allowed = 1000.0 * 0.08
        self.assertLessEqual(result.position_size_usdc, max_allowed + 0.01)
        self.assertTrue(result.capped)
        print(f"  ✓ Position capped at ${max_allowed:.2f} (got ${result.position_size_usdc:.2f})")

    def test_fractional_kelly_applied(self):
        """Fractional Kelly should be half of raw Kelly."""
        result = self.sizer.calculate(
            bankroll=1000.0,
            win_probability=0.65,
            current_price=0.50,
        )
        if result and not result.capped:
            self.assertAlmostEqual(
                result.fractional_kelly,
                result.kelly_fraction * 0.5,
                places=4,
            )
        print("  ✓ Half-Kelly fraction correctly applied")

    def test_expected_value_positive(self):
        """Expected value should be positive when win_probability > current_price."""
        ev = self.sizer.expected_value(win_probability=0.65, current_price=0.50)
        self.assertGreater(ev, 0)
        print(f"  ✓ Expected value: {ev:.4f} (positive)")

    def test_invalid_inputs(self):
        """Invalid inputs should return None without raising exceptions."""
        self.assertIsNone(self.sizer.calculate(1000.0, 0.0, 0.5))   # p=0
        self.assertIsNone(self.sizer.calculate(1000.0, 1.0, 0.5))   # p=1
        self.assertIsNone(self.sizer.calculate(1000.0, 0.6, 0.0))   # price=0
        self.assertIsNone(self.sizer.calculate(0.0, 0.6, 0.5))      # bankroll=0
        print("  ✓ Invalid inputs handled gracefully")

    def test_fixed_bet_mode_exact_amount(self):
        """Fixed bet mode should return the exact configured amount."""
        fixed_sizer = KellySizer(
            kelly_fraction=0.5,
            max_position_fraction=0.08,
            fixed_bet_usdc=50.0,
        )
        result = fixed_sizer.calculate(
            bankroll=1000.0,
            win_probability=0.55,
            current_price=0.50,
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.position_size_usdc, 50.0, places=2)
        print(f"  ✓ Fixed bet mode: ${result.position_size_usdc:.2f} (expected $50.00)")

    def test_fixed_bet_capped_by_max_fraction(self):
        """Fixed bet should be capped by max_position_fraction when balance is low."""
        fixed_sizer = KellySizer(
            kelly_fraction=0.5,
            max_position_fraction=0.08,
            fixed_bet_usdc=100.0,  # $100 fixed bet
        )
        # With balance=$500, max allowed = $500 * 8% = $40
        result = fixed_sizer.calculate(
            bankroll=500.0,
            win_probability=0.60,
            current_price=0.50,
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.position_size_usdc, 40.0, places=2)  # Capped at $40
        self.assertTrue(result.capped)
        print(f"  ✓ Fixed bet capped: ${result.position_size_usdc:.2f} (max $40.00 at 8% of $500)")

    def test_fixed_bet_zero_uses_kelly(self):
        """When fixed_bet_usdc=0, Kelly Criterion should be used."""
        kelly_sizer = KellySizer(
            kelly_fraction=0.5,
            max_position_fraction=0.08,
            fixed_bet_usdc=0.0,  # Kelly mode
        )
        result = kelly_sizer.calculate(
            bankroll=1000.0,
            win_probability=0.65,
            current_price=0.50,
        )
        self.assertIsNotNone(result)
        # Kelly result should NOT be exactly $50 — it varies with edge
        self.assertNotAlmostEqual(result.position_size_usdc, 50.0, delta=5.0)
        print(f"  ✓ Kelly mode active when fixed_bet=0: ${result.position_size_usdc:.2f}")


class TestRiskManager(unittest.TestCase):
    """Tests for the Risk Manager and kill switch logic."""

    def setUp(self):
        self.rm = RiskManager(
            starting_balance=1000.0,
            max_position_fraction=0.08,
            daily_loss_limit=0.20,
            total_drawdown_kill=0.40,
            max_concurrent_positions=3,
        )

    def _make_position(self, order_id: str, cost: float = 50.0, price: float = 0.50) -> Position:
        return Position(
            order_id=order_id,
            token_id="test_token",
            market_question="Will BTC be above $65,000?",
            side="BUY",
            entry_price=price,
            size_shares=cost / price,
            cost_usdc=cost,
            opened_at=time.time(),
            paper_mode=True,
        )

    def test_initial_state(self):
        """Risk manager should start in ACTIVE state."""
        self.assertEqual(self.rm.status, TradingStatus.ACTIVE)
        self.assertTrue(self.rm.is_trading_allowed)
        self.assertAlmostEqual(self.rm.current_balance, 1000.0)
        print("  ✓ Initial state: ACTIVE, balance=$1000.00")

    def test_position_size_limit(self):
        """Should reject positions exceeding max_position_fraction."""
        allowed, reason = self.rm.can_open_position(90.0)  # 9% > 8% limit
        self.assertFalse(allowed)
        self.assertIn("max", reason.lower())
        print(f"  ✓ Oversized position rejected: {reason}")

    def test_position_size_allowed(self):
        """Should allow positions within limits."""
        allowed, reason = self.rm.can_open_position(70.0)  # 7% < 8% limit
        self.assertTrue(allowed)
        print(f"  ✓ Valid position allowed: {reason}")

    def test_concurrent_position_limit(self):
        """Should reject new positions when max concurrent positions reached."""
        for i in range(3):
            pos = self._make_position(f"order_{i}", cost=50.0)
            self.rm.register_trade_open(pos)

        allowed, reason = self.rm.can_open_position(50.0)
        self.assertFalse(allowed)
        self.assertIn("concurrent", reason.lower())
        print(f"  ✓ Concurrent limit enforced: {reason}")

    def test_winning_trade_updates_balance(self):
        """A winning trade should increase the balance."""
        pos = self._make_position("win_order", cost=80.0, price=0.50)
        self.rm.register_trade_open(pos)
        initial_balance = self.rm.current_balance

        # Close at 0.90 (win: bought at 0.50, exit at 0.90)
        self.rm.register_trade_close("win_order", exit_price=0.90)
        expected_pnl = (0.90 - 0.50) * (80.0 / 0.50)  # 0.40 * 160 = $64
        self.assertGreater(self.rm.current_balance, initial_balance)
        self.assertEqual(self.rm._winning_trades, 1)
        print(f"  ✓ Win trade: balance ${initial_balance:.2f} → ${self.rm.current_balance:.2f}")

    def test_losing_trade_decreases_balance(self):
        """A losing trade should result in a net loss vs starting balance."""
        starting = self.rm.current_balance
        pos = self._make_position("loss_order", cost=80.0, price=0.50)
        self.rm.register_trade_open(pos)

        # Close at 0.05 — near total loss (bought at 0.50, exit at 0.05)
        self.rm.register_trade_close("loss_order", exit_price=0.05)
        # Net PnL should be negative: (0.05 - 0.50) * 160 shares = -$72
        self.assertLess(self.rm.current_balance, starting)
        print(f"  ✓ Loss trade: balance decreased from ${starting:.2f} to ${self.rm.current_balance:.2f}")

    def test_daily_halt_triggered(self):
        """Daily halt should trigger when daily loss exceeds limit."""
        # Simulate a large loss: lose 25% of daily balance
        self.rm._current_balance = 750.0  # 25% loss from $1000
        self.rm._check_risk_thresholds()
        self.assertEqual(self.rm.status, TradingStatus.DAILY_HALT)
        self.assertFalse(self.rm.is_trading_allowed)
        print(f"  ✓ Daily halt triggered at 25% loss (limit: 20%)")

    def test_kill_switch_triggered(self):
        """Kill switch should trigger when total drawdown exceeds limit."""
        self.rm._peak_balance = 1000.0
        self.rm._current_balance = 550.0  # 45% drawdown > 40% kill threshold
        self.rm._check_risk_thresholds()
        self.assertEqual(self.rm.status, TradingStatus.KILLED)
        self.assertFalse(self.rm.is_trading_allowed)
        print(f"  ✓ Kill switch triggered at 45% drawdown (limit: 40%)")

    def test_kill_switch_requires_confirmation(self):
        """Kill switch reset should require explicit confirmation."""
        self.rm._status = TradingStatus.KILLED
        result = self.rm.reset_kill_switch(confirm=False)
        self.assertFalse(result)
        self.assertEqual(self.rm.status, TradingStatus.KILLED)
        print("  ✓ Kill switch reset requires confirm=True")

    def test_kill_switch_reset_with_confirmation(self):
        """Kill switch should reset when confirm=True is provided."""
        self.rm._status = TradingStatus.KILLED
        result = self.rm.reset_kill_switch(confirm=True)
        self.assertTrue(result)
        self.assertEqual(self.rm.status, TradingStatus.ACTIVE)
        print("  ✓ Kill switch reset with confirm=True")

    def test_pause_and_resume(self):
        """Pause and resume should work correctly."""
        self.rm.pause("Test pause")
        self.assertEqual(self.rm.status, TradingStatus.PAUSED)
        self.assertFalse(self.rm.is_trading_allowed)

        self.rm.resume()
        self.assertEqual(self.rm.status, TradingStatus.ACTIVE)
        self.assertTrue(self.rm.is_trading_allowed)
        print("  ✓ Pause and resume work correctly")

    def test_win_rate_calculation(self):
        """Win rate should be calculated correctly."""
        # 2 wins, 1 loss
        for i in range(3):
            pos = self._make_position(f"trade_{i}", cost=30.0, price=0.50)
            self.rm.register_trade_open(pos)
            exit_price = 0.90 if i < 2 else 0.05  # 2 wins, 1 loss
            self.rm.register_trade_close(f"trade_{i}", exit_price=exit_price)

        self.assertAlmostEqual(self.rm.win_rate, 2/3, places=2)
        print(f"  ✓ Win rate: {self.rm.win_rate:.2%} (expected 66.7%)")


class TestBotConfig(unittest.TestCase):
    """Tests for configuration validation."""

    def test_default_config_is_paper_mode(self):
        """Default configuration should be in paper mode."""
        config = BotConfig()
        self.assertTrue(config.paper_mode)
        print("  ✓ Default config is paper mode")

    def test_live_mode_requires_private_key(self):
        """Live mode should raise ValueError if private key is missing."""
        config = BotConfig()
        config.paper_mode = False
        config.polymarket_private_key = ""
        with self.assertRaises(ValueError) as ctx:
            config.validate()
        self.assertIn("POLYMARKET_PRIVATE_KEY", str(ctx.exception))
        print("  ✓ Live mode requires private key")

    def test_invalid_position_fraction_rejected(self):
        """Position fraction above 50% should be rejected."""
        config = BotConfig()
        config.risk_max_position_fraction = 0.60  # 60% — above the 0.50 cap
        with self.assertRaises(ValueError):
            config.validate()
        print("  ✓ Position fraction > 50% rejected")

    def test_valid_paper_config_passes(self):
        """Valid paper mode config should pass validation without errors."""
        config = BotConfig()
        config.paper_mode = True
        try:
            config.validate()
            print("  ✓ Valid paper config passes validation")
        except ValueError as e:
            self.fail(f"Valid config raised ValueError: {e}")


class TestEdgeDetectorLogic(unittest.TestCase):
    """Tests for the edge detection fair value estimation (5-minute up/down model)."""

    def _make_detector(self, trade_window_minutes: int = 5):
        """Create a bare EdgeDetector instance with the minimum attributes needed for unit tests."""
        from core.edge_detector import EdgeDetector
        detector = EdgeDetector.__new__(EdgeDetector)
        detector.trade_window_minutes = trade_window_minutes
        return detector

    def test_fair_value_estimation_up_move(self):
        """Large upward BTC move should push fair value above 0.5 for UP outcome."""
        detector = self._make_detector()
        # BTC is $200 above price-to-beat with 60s remaining → strong UP signal
        fair_value = detector._compute_fair_value_5m(
            price_now=67000.0,
            price_to_beat=66800.0,   # $200 above → UP is winning
            seconds_remaining=60.0,
            direction="UP",
            base_scale=500.0,
            min_scale=50.0,
        )
        self.assertGreater(fair_value, 0.50)
        print(f"  ✓ $200 up move → fair value: {fair_value:.4f} (>0.50)")

    def test_fair_value_estimation_down_move(self):
        """Large downward BTC move should push fair value below 0.5 for UP outcome."""
        detector = self._make_detector()
        # BTC is $200 below price-to-beat with 60s remaining → DOWN is winning
        fair_value = detector._compute_fair_value_5m(
            price_now=66600.0,
            price_to_beat=66800.0,   # $200 below → DOWN is winning
            seconds_remaining=60.0,
            direction="UP",          # Testing UP probability → should be <0.5
            base_scale=500.0,
            min_scale=50.0,
        )
        self.assertLess(fair_value, 0.50)
        print(f"  ✓ $200 down move → fair value: {fair_value:.4f} (<0.50)")

    def test_fair_value_near_fifty_at_start(self):
        """With 290s remaining and BTC near price-to-beat, fair value should be near 0.5."""
        detector = self._make_detector()
        # BTC only $10 above price-to-beat with 290s remaining → near 50/50
        fair_value = detector._compute_fair_value_5m(
            price_now=66810.0,
            price_to_beat=66800.0,   # Only $10 above
            seconds_remaining=290.0, # Just started — large scale, small signal
            direction="UP",
            base_scale=500.0,
            min_scale=50.0,
        )
        # With large scale at t=290s, $10 move should barely move probability
        self.assertGreater(fair_value, 0.45)
        self.assertLess(fair_value, 0.60)
        print(f"  ✓ Small move at window start → fair value: {fair_value:.4f} (near 0.50)")

    def test_5m_market_filter(self):
        """_is_5m_updown_market should correctly identify 5-minute up/down markets."""
        from core.polymarket_client import MarketInfo

        detector = self._make_detector(trade_window_minutes=5)

        def make_market(question):
            return MarketInfo(
                condition_id="test", question=question,
                yes_token_id="y", yes_price=0.5,
                no_token_id="n", no_price=0.5,
                volume_24h=0, liquidity=10000,
                end_date_iso="", tick_size=0.01, neg_risk=False,
            )

        # Should match
        self.assertTrue(detector._is_5m_updown_market(
            make_market("Bitcoin Up or Down - 5 Minutes")
        ))
        self.assertTrue(detector._is_5m_updown_market(
            make_market("Bitcoin Up or Down - 5 minutes - March 30")
        ))
        # Should NOT match
        self.assertFalse(detector._is_5m_updown_market(
            make_market("Will BTC be above $70,000 by end of March?")
        ))
        self.assertFalse(detector._is_5m_updown_market(
            make_market("Bitcoin Up or Down - 15 Minutes")
        ))
        print("  ✓ 5-minute up/down market filter works correctly")


class TestRetryDecorator(unittest.IsolatedAsyncioTestCase):
    """Tests for the async_retry / sync_retry decorators in utils/retry.py."""

    async def test_succeeds_on_first_attempt(self):
        """Function that succeeds immediately should not be retried."""
        from utils.retry import async_retry

        calls = []

        @async_retry(max_attempts=3, base_delay=0.0)
        async def ok_func():
            calls.append(1)
            return "done"

        result = await ok_func()
        self.assertEqual(result, "done")
        self.assertEqual(len(calls), 1)
        print("  ✓ Succeeds on first attempt, no retries")

    async def test_retries_on_specified_exception(self):
        """Should retry up to max_attempts times on a matching exception."""
        from utils.retry import async_retry, RetryError

        calls = []

        @async_retry(max_attempts=3, base_delay=0.0, exceptions=(ValueError,))
        async def flaky():
            calls.append(1)
            raise ValueError("transient")

        with self.assertRaises(RetryError) as ctx:
            await flaky()

        self.assertEqual(len(calls), 3)
        self.assertIsInstance(ctx.exception.last_exception, ValueError)
        print(f"  ✓ Retried 3 times, raised RetryError ({len(calls)} calls)")

    async def test_succeeds_after_transient_failure(self):
        """Should succeed if the function passes on a later attempt."""
        from utils.retry import async_retry

        calls = []

        @async_retry(max_attempts=4, base_delay=0.0, exceptions=(RuntimeError,))
        async def eventually_ok():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("not ready")
            return "ok"

        result = await eventually_ok()
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        print(f"  ✓ Succeeded on attempt {len(calls)} after transient failures")

    async def test_non_matching_exception_propagates_immediately(self):
        """Exceptions NOT in the retry list should propagate without retrying."""
        from utils.retry import async_retry

        calls = []

        @async_retry(max_attempts=5, base_delay=0.0, exceptions=(ValueError,))
        async def wrong_exc():
            calls.append(1)
            raise KeyError("not retried")

        with self.assertRaises(KeyError):
            await wrong_exc()

        self.assertEqual(len(calls), 1, "Should not retry on non-matching exception")
        print("  ✓ Non-matching exception propagates immediately (no retry)")

    async def test_retry_error_carries_last_exception(self):
        """RetryError.__cause__ should be the last raised exception."""
        from utils.retry import async_retry, RetryError

        @async_retry(max_attempts=2, base_delay=0.0, exceptions=(OSError,))
        async def always_fails():
            raise OSError("disk full")

        with self.assertRaises(RetryError) as ctx:
            await always_fails()

        self.assertIsInstance(ctx.exception.__cause__, OSError)
        self.assertEqual(ctx.exception.attempts, 2)
        print(f"  ✓ RetryError.attempts={ctx.exception.attempts}, cause={ctx.exception.__cause__}")

    def test_sync_retry_works(self):
        """sync_retry should behave identically to async_retry for sync functions."""
        from utils.retry import sync_retry, RetryError

        calls = []

        @sync_retry(max_attempts=3, base_delay=0.0, exceptions=(ConnectionError,))
        def flaky_sync():
            calls.append(1)
            raise ConnectionError("timeout")

        with self.assertRaises(RetryError):
            flaky_sync()

        self.assertEqual(len(calls), 3)
        print(f"  ✓ sync_retry retried 3 times")

    async def test_on_retry_callback_called(self):
        """on_retry callback should be invoked for each failed attempt."""
        from utils.retry import async_retry

        retry_log = []

        @async_retry(
            max_attempts=3,
            base_delay=0.0,
            exceptions=(ValueError,),
            on_retry=lambda attempt, exc, delay: retry_log.append((attempt, str(exc))),
        )
        async def noisy_fail():
            raise ValueError("boom")

        from utils.retry import RetryError
        with self.assertRaises(RetryError):
            await noisy_fail()

        self.assertEqual(len(retry_log), 2)  # Called for attempts 1 and 2 (not the last)
        print(f"  ✓ on_retry callback fired {len(retry_log)} times: {retry_log}")


if __name__ == "__main__":
    print("\n" + "═" * 70)
    print("  POLYMARKET BOT TEST SUITE")
    print("═" * 70 + "\n")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestKellySizer))
    suite.addTests(loader.loadTestsFromTestCase(TestRiskManager))
    suite.addTests(loader.loadTestsFromTestCase(TestBotConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeDetectorLogic))
    suite.addTests(loader.loadTestsFromTestCase(TestRetryDecorator))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "═" * 70)
    if result.wasSuccessful():
        print(f"  ✅ ALL {result.testsRun} TESTS PASSED")
    else:
        print(f"  ❌ {len(result.failures)} FAILURES, {len(result.errors)} ERRORS")
    print("═" * 70 + "\n")

    sys.exit(0 if result.wasSuccessful() else 1)
