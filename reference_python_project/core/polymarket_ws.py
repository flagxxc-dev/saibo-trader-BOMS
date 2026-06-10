"""
core/polymarket_ws.py
─────────────────────────────────────────────────────────────────────────────
Polymarket CLOB WebSocket Feed

Subscribes to the official Polymarket CLOB WebSocket for real-time
`price_change` events on active btc-updown-5m markets.

Official endpoint: wss://ws-subscriptions-clob.polymarket.com/ws
Protocol:
  - Subscribe: {"type": "subscribe", "channel": "market", "markets": [conditionId]}
  - Events: book | price_change | trade
  - Ping every 5 seconds (no rate limit per Polymarket docs)

This replaces REST polling of /price endpoint, reducing latency from
~200-500ms (REST) to ~10-50ms (WebSocket push).

Architecture:
  PolymarketWSFeed
    ├── _connect()           — WebSocket handshake + subscribe
    ├── _ping_loop()         — 5s keepalive pings
    ├── _message_loop()      — parse price_change events
    ├── subscribe(condition_id) — add market to subscription
    └── get_price(token_id) — latest price for a token

Usage:
    feed = PolymarketWSFeed()
    await feed.start()
    feed.subscribe("0xcondition...")
    price = feed.get_price("token_id_123")
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Dict, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

import logging
from utils.logger import get_logger

_root_logger = get_logger(__name__)

# Dedicated WebSocket logger that writes all WS traffic (including DEBUG) to a separate file
ws_file_logger = logging.getLogger("pm_ws_file")
ws_file_logger.setLevel(logging.DEBUG)
ws_file_logger.propagate = False
if not ws_file_logger.handlers:
    _fh = logging.FileHandler("polymarket_ws.log")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    ws_file_logger.addHandler(_fh)

class WSLoggerWrapper:
    def debug(self, msg, *args, **kwargs):
        ws_file_logger.debug(msg, *args, **kwargs)
        
    def info(self, msg, *args, **kwargs):
        _root_logger.info(msg, *args, **kwargs)
        ws_file_logger.info(msg, *args, **kwargs)
        
    def warning(self, msg, *args, **kwargs):
        _root_logger.warning(msg, *args, **kwargs)
        ws_file_logger.warning(msg, *args, **kwargs)
        
    def error(self, msg, *args, **kwargs):
        _root_logger.error(msg, *args, **kwargs)
        ws_file_logger.error(msg, *args, **kwargs)

logger = WSLoggerWrapper()


class PolymarketWSFeed:
    """
    Real-time Polymarket price feed via CLOB WebSocket.

    Maintains a live price cache for all subscribed markets.
    Automatically reconnects on disconnect with exponential backoff.
    """

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    PING_INTERVAL = 5.0          # seconds — required by Polymarket docs
    RECONNECT_BASE_DELAY = 2.0   # seconds
    RECONNECT_MAX_DELAY = 30.0   # seconds
    STALE_PRICE_TTL = 60.0       # seconds before price is considered stale

    def __init__(
        self,
        on_price_change: Optional[Callable] = None,
        proxy_url: str = "",
    ) -> None:
        """
        Args:
            on_price_change: Optional callback(token_id, price, side, timestamp)
                             called on every price_change event.
            proxy_url: Optional SOCKS5/SOCKS4/HTTP proxy URL, e.g.
                       "socks5://user:pass@host:1080". Leave empty for direct.
        """
        self._on_price_change = on_price_change
        self._proxy_url: str = proxy_url

        # Price cache: token_id → {"price": float, "side": str, "ts": float}
        self._prices: Dict[str, Dict] = {}

        # Subscribed condition IDs
        self._subscribed: Set[str] = set()
        
        # Mapping: condition_id → [yes_token_id, no_token_id]
        self._condition_to_tokens: Dict[str, list] = {}

        # Internal state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_delay = self.RECONNECT_BASE_DELAY
        self._connect_attempts = 0
        self._last_message_ts: float = 0.0
        self._latest_lag_ms: Optional[float] = None
        self._total_updates: int = 0

        # Tasks
        self._feed_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._cache_evict_task: Optional[asyncio.Task] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket feed in the background."""
        if self._running:
            return
        self._running = True
        self._feed_task = asyncio.create_task(self._run_forever(), name="pm_ws_feed")
        self._cache_evict_task = asyncio.create_task(self._evict_stale_prices(), name="pm_ws_cache_evict")
        logger.info("PolymarketWSFeed started.")

    async def stop(self) -> None:
        """Gracefully stop the WebSocket feed."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._feed_task:
            self._feed_task.cancel()
        if self._ping_task:
            self._ping_task.cancel()
        if self._cache_evict_task:
            self._cache_evict_task.cancel()
        logger.info("PolymarketWSFeed stopped.")

    def subscribe(self, condition_id: str, yes_token_id: str, no_token_id: str) -> None:
        """
        Add a condition ID to the subscription set.

        If already connected, sends the subscribe message immediately.
        Otherwise, it will be sent on next connect.
        """
        if condition_id in self._subscribed:
            return
        self._subscribed.add(condition_id)
        self._condition_to_tokens[condition_id] = [yes_token_id, no_token_id]
        logger.info("PM WS: Queued subscription for condition %s", condition_id[:16])

        # If already connected, subscribe immediately
        if self._ws and not self._ws.closed:
            asyncio.create_task(self._send_subscribe([yes_token_id, no_token_id]))

    def unsubscribe(self, condition_id: str) -> None:
        """Remove a condition ID from the subscription set."""
        self._subscribed.discard(condition_id)
        self._condition_to_tokens.pop(condition_id, None)

    def get_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        Get the latest cached price for a token.

        Args:
            token_id: CLOB token ID (from clobTokenIds field).
            side: "BUY" or "SELL" — which side of the book to read.

        Returns:
            Latest price as float (0.0–1.0), or None if not available/stale.
        """
        key = token_id if side.upper() == "BUY" else f"{token_id}_bid"
        entry = self._prices.get(key)
        if not entry:
            return None
        # Check staleness
        age = time.time() - entry["ts"]
        if age > self.STALE_PRICE_TTL:
            logger.debug("PM WS: Stale price for token %s (age: %.0fs)", token_id[:16], age)
            return None
        return entry["price"]

    def get_stats(self) -> Dict:
        """Return feed statistics for heartbeat logging."""
        return {
            "subscribed_markets": len(self._subscribed),
            "cached_tokens": len(self._prices),
            "total_updates": self._total_updates,
            "last_message_age": round(time.time() - self._last_message_ts, 1) if self._last_message_ts else None,
            "connected": self._ws is not None and not self._ws.closed,
        }

    @property
    def latest_lag_ms(self) -> Optional[float]:
        """Return the estimated network transit lag in milliseconds for the latest message."""
        return self._latest_lag_ms

    @property
    def is_connected(self) -> bool:
        """True if the WebSocket is currently connected."""
        return self._ws is not None and not self._ws.closed

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: Connection Loop
    # ─────────────────────────────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        """Main reconnect loop — runs until stop() is called."""
        _hard_reject_count = 0
        _HARD_REJECT_BACKOFF = 3   # long backoff after this many consecutive 403/451
        _HARD_REJECT_SLEEP   = 300 # seconds — retry after 5 min instead of giving up
        while self._running:
            try:
                await self._connect_and_run()
                # Clean exit — stop() was called
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break

                exc_name = type(exc).__name__
                exc_str  = str(exc)

                # Classify the error precisely.
                # Only HTTP 403 (forbidden) and 451 (legal block) are genuine
                # geo/auth rejections.  5xx, 404, and generic InvalidStatus are
                # transient server or routing errors — retry with normal backoff.
                http_status = 0
                if exc_name in ("InvalidStatus", "InvalidStatusCode"):
                    # websockets attaches the HTTP response to the exception
                    resp = getattr(exc, "response", None)
                    if resp is not None:
                        http_status = getattr(resp, "status_code", 0) or getattr(resp, "status", 0)
                    if not http_status:
                        for code in (403, 451, 404, 500, 503):
                            if str(code) in exc_str:
                                http_status = code
                                break

                is_hard_reject = http_status in (403, 451)

                if is_hard_reject:
                    _hard_reject_count += 1
                    logger.warning(
                        "PM WS: HTTP %d — connection rejected (attempt %d/%d). "
                        "Will retry in %ds.",
                        http_status, _hard_reject_count,
                        _HARD_REJECT_BACKOFF, _HARD_REJECT_SLEEP,
                    )
                    if _hard_reject_count >= _HARD_REJECT_BACKOFF:
                        logger.warning(
                            "PM WS: %d consecutive rejections — backing off %ds "
                            "then retrying. REST fallback active in the meantime.",
                            _hard_reject_count, _HARD_REJECT_SLEEP,
                        )
                        _hard_reject_count = 0
                        self._reconnect_delay = self.RECONNECT_BASE_DELAY
                        await asyncio.sleep(_HARD_REJECT_SLEEP)
                    else:
                        await asyncio.sleep(30.0)
                    continue

                # Transient error — reset hard-reject counter, use exponential backoff
                _hard_reject_count = 0
                logger.warning(
                    "PM WS: Disconnected (%s%s). Reconnecting in %.0fs...",
                    exc_name,
                    f" HTTP {http_status}" if http_status else "",
                    self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.RECONNECT_MAX_DELAY
                )

    async def _connect_and_run(self) -> None:
        """Establish WebSocket connection, subscribe, and process messages."""
        self._connect_attempts += 1
        # Only log at INFO for first 3 attempts; after that demote to DEBUG
        # to avoid filling the dashboard with repeated geo-block retries.
        _log = logger.info if self._connect_attempts <= 3 else logger.debug
        _log(
            "PM WS: Connecting (attempt #%d) → %s",
            self._connect_attempts, self.WS_URL,
        )

        _ws_extra: dict = {}
        if self._proxy_url:
            from python_socks.async_.asyncio import Proxy as _SocksProxy  # type: ignore
            import ssl as _ssl
            _proxy = _SocksProxy.from_url(self._proxy_url)
            _sock = await _proxy.connect(
                dest_host="ws-subscriptions-clob.polymarket.com",
                dest_port=443,
            )
            _ssl_ctx = _ssl.create_default_context()
            _ws_extra["sock"] = _sock
            _ws_extra["ssl"] = _ssl_ctx
            _ws_extra["server_hostname"] = "ws-subscriptions-clob.polymarket.com"

        async with websockets.connect(
            self.WS_URL,
            open_timeout=15,
            ping_interval=None,   # We handle pings manually
            ping_timeout=None,
            max_size=2**20,       # 1MB max message size
            extra_headers=[
                ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"),
                ("Origin", "https://polymarket.com"),
            ],
            **_ws_extra,
        ) as ws:
            self._ws = ws
            self._reconnect_delay = self.RECONNECT_BASE_DELAY  # Reset on success
            logger.info("PM WS: Connected ✓ — %s", self.WS_URL)

            # Subscribe to all queued token IDs
            if self._subscribed:
                all_tokens = []
                for cid in self._subscribed:
                    all_tokens.extend(self._condition_to_tokens.get(cid, []))
                if all_tokens:
                    await self._send_subscribe(all_tokens)

            # Start ping loop
            self._ping_task = asyncio.create_task(
                self._ping_loop(ws), name="pm_ws_ping"
            )

            try:
                await self._message_loop(ws)
            finally:
                self._ping_task.cancel()
                self._ws = None

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """
        Send a JSON ping message every 5 seconds as required by Polymarket.
        Polymarket expects {"type": "ping"} text frames, not WebSocket
        protocol-level ping frames — ws.ping() would go unanswered and
        cause the library to close the connection on timeout.
        """
        while True:
            await asyncio.sleep(self.PING_INTERVAL)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except ConnectionClosed:
                break
            except Exception:
                break

    async def _message_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Receive and process WebSocket messages."""
        async for raw in ws:
            if not self._running:
                break
            self._last_message_ts = time.time()
            try:
                self._handle_message(raw)
            except Exception as exc:
                logger.debug("PM WS: Error handling message: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: Message Handling
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_message(self, raw: str) -> None:
        """Parse and dispatch a raw WebSocket message."""
        try:
            messages = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Polymarket sends either a single object or an array of objects
        if isinstance(messages, dict):
            messages = [messages]

        for msg in messages:
            event_type = msg.get("event_type") or msg.get("type", "")

            if event_type == "price_change":
                self._handle_price_change(msg)
            elif event_type == "book":
                self._handle_book_snapshot(msg)
            elif event_type == "trade":
                self._handle_trade(msg)
            elif event_type in ("last_trade_price",):
                pass  # Ignore last trade price events
            # Ignore unknown event types silently

    def _handle_price_change(self, msg: dict) -> None:
        """
        Handle a price_change event.

        Message format (from Polymarket docs):
        {
            "event_type": "price_change",
            "asset_id": "token_id",
            "market": "condition_id",
            "price": "0.47",
            "side": "BUY",
            "timestamp": "1234567890000"
        }
        """
        token_id = msg.get("asset_id") or msg.get("token_id", "")
        price_str = msg.get("price", "")
        side = msg.get("side", "BUY")
        ts_str = msg.get("timestamp", "")

        if not token_id or not price_str:
            return

        try:
            price = float(price_str)
            ts = float(ts_str) / 1000.0 if ts_str else time.time()
        except (ValueError, TypeError):
            return

        if not (0.0 <= price <= 1.0):
            logger.debug("PM WS: Ignoring out-of-range price %.4f for token %s", price, token_id[:16])
            return

        # In Polymarket WS `price_change`:
        # side="BUY" means Maker BUY = Bid (we SELL to this).
        # side="SELL" means Maker SELL = Ask (we BUY from this).
        pm_side = side.upper()
        if pm_side == "BUY":
            key = f"{token_id}_bid"
            client_side = "SELL"
        else:
            key = token_id
            client_side = "BUY"

        # Update cache
        self._prices[key] = {"price": price, "side": client_side, "ts": ts}
        self._total_updates += 1
        
        # Update MS lag if timestamp was provided by Polymarket
        if ts_str:
            # Polymarket timestamps are execution times. Local time minus execution time is the lag.
            self._latest_lag_ms = max(0.0, (time.time() - ts) * 1000.0)

        logger.debug(
            "PM WS price_change: token=%s | price=%.4f | side=%s",
            token_id[:16], price, side,
        )

        # Fire callback if registered
        if self._on_price_change:
            try:
                self._on_price_change(token_id, price, side, ts)
            except Exception as exc:
                logger.debug("PM WS: Callback error: %s", exc)

    def _handle_book_snapshot(self, msg: dict) -> None:
        """
        Handle a full orderbook snapshot (book event).

        Extract best bid and ask prices and cache them.
        """
        token_id = msg.get("asset_id") or msg.get("market", "")
        if not token_id:
            return

        bids = msg.get("bids", [])
        asks = msg.get("asks", [])

        # Best bid = highest bid price
        if bids:
            try:
                best_bid = max(float(b["price"]) for b in bids)
                self._prices[f"{token_id}_bid"] = {
                    "price": best_bid, "side": "SELL", "ts": time.time()
                }
            except (KeyError, ValueError):
                pass

        # Best ask = lowest ask price
        if asks:
            try:
                best_ask = min(float(a["price"]) for a in asks)
                self._prices[token_id] = {
                    "price": best_ask, "side": "BUY", "ts": time.time()
                }
                self._total_updates += 1
            except (KeyError, ValueError):
                pass

    def _handle_trade(self, msg: dict) -> None:
        """Log trade executions for monitoring."""
        token_id = msg.get("asset_id", "")[:16]
        price = msg.get("price", "?")
        size = msg.get("size", "?")
        logger.debug("PM WS trade: token=%s | price=%s | size=%s", token_id, price, size)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: Subscription
    # ─────────────────────────────────────────────────────────────────────────

    async def _evict_stale_prices(self) -> None:
        """
        Background task (every 60s):
          1. Evict price cache entries older than STALE_PRICE_TTL.
          2. Subscription heartbeat — re-subscribe any condition IDs whose prices
             have gone stale while the WS is still reported as connected (zombie
             connection detection).
        """
        while self._running:
            await asyncio.sleep(60)
            now = time.time()

            # ── Evict stale entries ──────────────────────────────────────────
            stale = [tid for tid, e in self._prices.items() if now - e["ts"] > self.STALE_PRICE_TTL]
            for tid in stale:
                del self._prices[tid]
            if stale:
                logger.debug("PM WS: Evicted %d stale price cache entries.", len(stale))

            # ── Subscription heartbeat ───────────────────────────────────────
            # If connected but no messages in 2× STALE_PRICE_TTL, the connection
            # may be a zombie. Force close so _run_forever reconnects and re-subscribes.
            if (
                self._ws is not None
                and not self._ws.closed
                and self._last_message_ts > 0
                and (now - self._last_message_ts) > self.STALE_PRICE_TTL * 2
                and self._subscribed
            ):
                logger.warning(
                    "PM WS: No messages for %.0fs (threshold %.0fs) — "
                    "zombie connection suspected, forcing reconnect.",
                    now - self._last_message_ts,
                    self.STALE_PRICE_TTL * 2,
                )
                try:
                    await self._ws.close()
                except Exception:
                    pass

    async def _send_subscribe(self, token_ids: list) -> None:
        """Send a subscribe message for the given token IDs."""
        if not self._ws or self._ws.closed:
            logger.debug(
                "PM WS: _send_subscribe skipped (WS not connected) — "
                "will replay on next reconnect: %s",
                ", ".join(c[:12] for c in token_ids[:3]),
            )
            return
        msg = {
            "type": "market",
            "assets_ids": token_ids,
        }
        try:
            await self._ws.send(json.dumps(msg))
            logger.info(
                "PM WS: Subscribed to %d token(s): %s...",
                len(token_ids),
                ", ".join(c[:12] for c in token_ids[:3]),
            )
        except Exception as exc:
            logger.warning("PM WS: Failed to send subscribe: %s", exc)
