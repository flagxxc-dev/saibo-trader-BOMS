"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  BINANCE WEBSOCKET FEED                                                      ║
║  Streams real-time BTC/USDT trade data from Binance with sub-50ms latency.  ║
║  Auto-reconnects on failure and maintains a rolling price history.          ║
╚══════════════════════════════════════════════════════════════════════════════╝

Fix log (v3 — handshake timeout resilience):

  Root cause of the recurring "timed out during opening handshake":
    Binance load-balancers occasionally accept the TCP SYN and TLS handshake
    but stall before sending the HTTP 101 Switching Protocols response.
    This is an intermittent server-side issue, not a network block.
    It typically resolves on the very next connection attempt.

  Fixes applied:
    1. PARALLEL PROBE on startup — all 4 endpoints are raced simultaneously;
       whichever responds first wins. Eliminates the sequential 12s-per-endpoint
       wait and finds the fastest server immediately.

    2. STICKY ENDPOINT — once a working endpoint is found, the bot stays on it.
       It only rotates to the next endpoint after 3 consecutive failures on the
       current one, preventing unnecessary churn.

    3. EXPONENTIAL BACKOFF — reconnect delay grows: 2s → 4s → 8s → 16s (max),
       then resets to 2s after a successful connection. Prevents hammering a
       stalled server while recovering quickly from transient failures.

    4. STALE-TICK WATCHDOG — a background coroutine checks that new ticks
       arrive at least every 30 seconds. If no tick arrives, the connection is
       considered dead and forcibly closed to trigger a reconnect.

    5. OPEN_TIMEOUT raised to 20s — gives the server more time to respond
       while still failing fast enough to cycle to the next endpoint.

    6. REST bootstrap uses data-api.binance.vision (geo-unrestricted) first.
"""

import asyncio
import json
import ssl
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

import requests
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PriceTick:
    """A single BTC price tick from Binance."""
    price: float
    timestamp_ms: float  # Unix timestamp in milliseconds (Binance trade time)
    volume: float        # Trade volume in BTC
    received_at: float = field(default_factory=time.time)  # Local receive time

    @property
    def timestamp_s(self) -> float:
        """Return Binance trade timestamp in seconds."""
        return self.timestamp_ms / 1000.0


class BinanceWebSocketFeed:
    """
    Maintains a persistent WebSocket connection to Binance's real-time
    trade stream for any symbol (BTCUSDT, ETHUSDT, SOLUSDT, …).

    All 4 endpoints are probed in parallel on startup; the fastest one
    is used. On disconnect, the bot retries the same endpoint up to 3 times
    before rotating to the next one.
    """

    HISTORY_SIZE = 5400  # ~1080s at 5 ticks/sec — covers a full 15-minute window + buffer

    # Timeouts (seconds)
    OPEN_TIMEOUT_S: int = 20      # Raised from 15 → 20 for slow server responses
    PING_INTERVAL_S: int = 20
    PING_TIMEOUT_S: int = 15
    CLOSE_TIMEOUT_S: int = 5
    PROBE_TIMEOUT_S: int = 10     # Parallel probe: how long to wait for fastest endpoint
    STALE_TICK_TIMEOUT_S: int = 30  # Watchdog: reconnect if no tick for this long

    # Reconnect backoff
    BACKOFF_BASE_S: float = 2.0
    BACKOFF_MAX_S: float = 16.0
    FAILURES_BEFORE_ROTATE: int = 3  # Consecutive failures before trying next endpoint

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        reconnect_delay: float = 2.0,
        on_tick: Optional[Callable[[PriceTick], None]] = None,
    ) -> None:
        self.symbol = symbol.upper()
        _sym = self.symbol.lower()
        self.STREAM_URLS: List[str] = [
            f"wss://data-stream.binance.com:9443/ws/{_sym}@trade",
            f"wss://data-stream.binance.com:443/ws/{_sym}@trade",
            f"wss://stream.binance.com:9443/ws/{_sym}@trade",
            f"wss://stream.binance.com:443/ws/{_sym}@trade",
        ]
        self.REST_URLS: List[str] = [
            f"https://data-api.binance.vision/api/v3/ticker/price?symbol={self.symbol}",
            f"https://api.binance.com/api/v3/ticker/price?symbol={self.symbol}",
            f"https://api1.binance.com/api/v3/ticker/price?symbol={self.symbol}",
        ]
        self.reconnect_delay = reconnect_delay
        self.on_tick = on_tick

        self._latest_tick: Optional[PriceTick] = None
        self._history: Deque[PriceTick] = deque(maxlen=self.HISTORY_SIZE)
        self._running = False
        self._connected = False
        self._total_ticks = 0
        self._connection_attempts = 0

        # Endpoint management
        self._current_url_index = 0
        self._consecutive_failures = 0
        self._current_backoff = self.BACKOFF_BASE_S

        # Watchdog state
        self._last_tick_time: float = 0.0
        self._ws_handle: Optional[websockets.WebSocketClientProtocol] = None

        self._ssl_context = ssl.create_default_context()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def latest_price(self) -> Optional[float]:
        return self._latest_tick.price if self._latest_tick else None

    @property
    def latest_tick(self) -> Optional[PriceTick]:
        return self._latest_tick

    @property
    def latest_lag_ms(self) -> Optional[float]:
        """Return the estimated network transit lag in milliseconds."""
        tick = self._latest_tick
        if not tick:
            return None
        return max(0.0, (tick.received_at * 1000.0) - tick.timestamp_ms)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def tick_count(self) -> int:
        return self._total_ticks

    def get_price_at(self, seconds_ago: float) -> Optional[float]:
        """Return BTC price approximately `seconds_ago` seconds in the past."""
        if not self._history:
            return None
        cutoff = time.time() - seconds_ago
        for tick in reversed(self._history):
            if tick.timestamp_s <= cutoff:
                return tick.price
        return self._history[0].price if self._history else None

    def get_price_change(self, seconds_ago: float = 3.0) -> Optional[float]:
        """Return absolute BTC price change over the last `seconds_ago` seconds."""
        current = self.latest_price
        past = self.get_price_at(seconds_ago)
        if current is None or past is None:
            return None
        return current - past

    def get_price_direction(
        self, seconds_ago: float = 3.0, min_move: float = 50.0
    ) -> Optional[str]:
        """Return 'UP', 'DOWN', or None based on price movement."""
        change = self.get_price_change(seconds_ago)
        if change is None:
            return None
        if change >= min_move:
            return "UP"
        if change <= -min_move:
            return "DOWN"
        return None

    async def start(self) -> None:
        """
        Start the feed. Performs REST bootstrap, then parallel endpoint probe,
        then enters the persistent reconnect loop.
        """
        self._running = True

        # Step 1: REST bootstrap — populate price immediately
        await self._bootstrap_price_from_rest()

        # Step 2: Parallel probe — find the fastest working endpoint
        best_index = await self._probe_fastest_endpoint()
        if best_index is not None:
            self._current_url_index = best_index
            logger.info(
                "Parallel probe selected endpoint [%d]: %s",
                best_index,
                self.STREAM_URLS[best_index],
            )
        else:
            logger.warning(
                "Parallel probe found no responding endpoint — "
                "will attempt all endpoints sequentially."
            )

        # Step 3: Start stale-tick watchdog
        watchdog_task = asyncio.create_task(self._stale_tick_watchdog())

        # Step 4: Persistent WebSocket loop
        logger.info("BinanceWebSocketFeed entering reconnect loop...")
        try:
            while self._running:
                try:
                    await self._connect_and_stream()
                    # Clean disconnect (stop() was called)
                    if not self._running:
                        break
                    # Unexpected clean close — treat as failure
                    raise ConnectionClosed(None, None)

                except Exception as exc:
                    if not self._running:
                        break

                    self._connected = False
                    self._consecutive_failures += 1
                    err_name = type(exc).__name__
                    err_msg = str(exc)[:80]

                    # Rotate endpoint after N consecutive failures
                    if self._consecutive_failures >= self.FAILURES_BEFORE_ROTATE:
                        old_idx = self._current_url_index
                        self._current_url_index = (
                            self._current_url_index + 1
                        ) % len(self.STREAM_URLS)
                        self._consecutive_failures = 0
                        self._current_backoff = self.BACKOFF_BASE_S  # Reset backoff on rotate
                        logger.warning(
                            "WebSocket [%s] failed %d times (%s: %s). "
                            "Rotating to endpoint [%d]: %s — reconnecting in %.1fs...",
                            self.STREAM_URLS[old_idx].split("/")[2],
                            self.FAILURES_BEFORE_ROTATE,
                            err_name, err_msg,
                            self._current_url_index,
                            self.STREAM_URLS[self._current_url_index],
                            self._current_backoff,
                        )
                    else:
                        logger.warning(
                            "WebSocket disconnected (%s: %s) — "
                            "retry #%d on same endpoint in %.1fs...",
                            err_name, err_msg,
                            self._consecutive_failures,
                            self._current_backoff,
                        )

                    await asyncio.sleep(self._current_backoff)

                    # Exponential backoff: 2 → 4 → 8 → 16 → 16 → ...
                    self._current_backoff = min(
                        self._current_backoff * 2, self.BACKOFF_MAX_S
                    )
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Watchdog task raised unexpected exception during cleanup: %s", exc)

        logger.info("BinanceWebSocketFeed stopped.")

    def stop(self) -> None:
        """Signal the feed to stop."""
        self._running = False
        logger.info("BinanceWebSocketFeed stop requested.")

    def get_stats(self) -> dict:
        return {
            "connected": self._connected,
            "total_ticks": self._total_ticks,
            "connection_attempts": self._connection_attempts,
            "latest_price": self.latest_price,
            "history_size": len(self._history),
            "current_endpoint": self.STREAM_URLS[self._current_url_index],
            "consecutive_failures": self._consecutive_failures,
            "current_backoff_s": self._current_backoff,
            "seconds_since_last_tick": (
                round(time.time() - self._last_tick_time, 1)
                if self._last_tick_time > 0 else None
            ),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Methods
    # ─────────────────────────────────────────────────────────────────────────

    async def _bootstrap_price_from_rest(self) -> None:
        """Fetch BTC price from REST API and inject as a synthetic tick."""
        loop = asyncio.get_running_loop()
        for url in self.REST_URLS:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda u=url: requests.get(u, timeout=8),
                )
                data = response.json()
                price = float(data["price"])
                tick = PriceTick(
                    price=price,
                    timestamp_ms=time.time() * 1000,
                    volume=0.0,
                )
                self._latest_tick = tick
                self._history.append(tick)
                self._last_tick_time = time.time()
                logger.info(
                    "REST bootstrap ✓ — BTC: $%.2f (source: %s)",
                    price, url.split("/")[2],
                )
                return
            except Exception as exc:
                logger.warning("REST bootstrap failed (%s): %s", url.split("/")[2], exc)

        logger.warning(
            "All REST bootstrap endpoints failed. "
            "Waiting for first WebSocket tick."
        )

    async def _probe_fastest_endpoint(self) -> Optional[int]:
        """
        Race all endpoints in parallel. Return the index of the first one
        that successfully completes a WebSocket handshake and receives a tick.

        This eliminates the sequential 12s-per-endpoint wait on startup.
        """
        logger.info(
            "Probing %d endpoints in parallel (timeout: %ds)...",
            len(self.STREAM_URLS),
            self.PROBE_TIMEOUT_S,
        )

        async def probe_one(index: int, url: str) -> Optional[int]:
            try:
                async with websockets.connect(
                    url,
                    ssl=self._ssl_context,
                    open_timeout=self.PROBE_TIMEOUT_S,
                    ping_interval=None,
                    close_timeout=2,
                ) as ws:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                    return index
            except Exception:
                return None

        tasks = [
            asyncio.create_task(probe_one(i, url))
            for i, url in enumerate(self.STREAM_URLS)
        ]

        winner = None
        try:
            # Wait for the first successful probe
            for coro in asyncio.as_completed(tasks):
                result = await coro
                if result is not None:
                    winner = result
                    break
        finally:
            # Cancel all remaining probe tasks
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if winner is not None:
            logger.info(
                "Fastest endpoint: [%d] %s",
                winner, self.STREAM_URLS[winner],
            )
        return winner

    async def _connect_and_stream(self) -> None:
        """Connect to the current endpoint and stream messages."""
        url = self.STREAM_URLS[self._current_url_index]
        self._connection_attempts += 1

        logger.info(
            "Connecting (attempt #%d) → %s",
            self._connection_attempts, url,
        )

        async with websockets.connect(
            url,
            ssl=self._ssl_context,
            open_timeout=self.OPEN_TIMEOUT_S,
            ping_interval=self.PING_INTERVAL_S,
            ping_timeout=self.PING_TIMEOUT_S,
            close_timeout=self.CLOSE_TIMEOUT_S,
            max_size=2 ** 20,
            compression=None,
        ) as ws:
            self._ws_handle = ws
            self._connected = True
            self._consecutive_failures = 0
            self._current_backoff = self.BACKOFF_BASE_S  # Reset backoff on success

            logger.info(
                "Connected ✓ — streaming %s via %s",
                self.symbol, url.split("/")[2],
            )

            async for raw_message in ws:
                if not self._running:
                    break
                self._process_message(raw_message)

        self._ws_handle = None

    def _process_message(self, raw: str) -> None:
        """Parse a Binance trade message and update state."""
        try:
            data = json.loads(raw)
            if data.get("e") != "trade":
                return

            tick = PriceTick(
                price=float(data["p"]),
                timestamp_ms=float(data["T"]),
                volume=float(data["q"]),
            )
            self._latest_tick = tick
            self._history.append(tick)
            self._total_ticks += 1
            self._last_tick_time = time.time()

            if self._total_ticks % 500 == 0:
                logger.debug(
                    "BTC tick #%d: $%.2f (%.4f BTC)",
                    self._total_ticks, tick.price, tick.volume,
                )

            if self.on_tick:
                self.on_tick(tick)

        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Parse error: %s | Raw: %.80s", exc, raw)

    async def _stale_tick_watchdog(self) -> None:
        """
        Background coroutine that monitors tick freshness.

        If no tick arrives for STALE_TICK_TIMEOUT_S seconds while the feed
        is supposed to be connected, the WebSocket connection is forcibly
        closed to trigger a reconnect. This catches zombie connections where
        the TCP socket is open but Binance has stopped sending data.
        """
        logger.debug("Stale-tick watchdog started (threshold: %ds)", self.STALE_TICK_TIMEOUT_S)
        while self._running:
            await asyncio.sleep(10)  # Check every 10 seconds
            if not self._connected or self._last_tick_time == 0:
                continue
            stale_seconds = time.time() - self._last_tick_time
            if stale_seconds > self.STALE_TICK_TIMEOUT_S:
                logger.warning(
                    "Stale tick watchdog triggered — no tick for %.0fs "
                    "(threshold: %ds). Forcing reconnect...",
                    stale_seconds, self.STALE_TICK_TIMEOUT_S,
                )
                if self._ws_handle is not None:
                    try:
                        await self._ws_handle.close()
                    except Exception:
                        pass
