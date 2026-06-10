"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  POLYMARKET CLOB CLIENT WRAPPER                                           ║
║  Wraps the official py-clob-client with retry logic, market caching,      ║
║  and a clean interface for the arbitrage bot.                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


import asyncio
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from utils.logger import get_logger
from utils.retry import async_retry


logger = get_logger(__name__)


@dataclass
class MarketInfo:
    """Snapshot of a Polymarket market relevant to BTC price bets."""
    condition_id: str
    question: str
    # YES token
    yes_token_id: str
    yes_price: float
    # NO token
    no_token_id: str
    no_price: float
    # Market metadata
    volume_24h: float
    liquidity: float
    end_date_iso: str
    tick_size: float
    neg_risk: bool
    # Timestamp of this snapshot
    fetched_at: float = 0.0

    @property
    def is_near_fifty_fifty(self, tolerance: float = 0.10) -> bool:
        """Return True if the market is near 50/50 (within tolerance)."""
        return abs(self.yes_price - 0.50) <= tolerance

    @property
    def implied_btc_direction(self) -> Optional[str]:
        """
        Infer the market's implied BTC direction from its question text.
        Returns 'UP' if the market resolves YES on a price increase,
        'DOWN' if it resolves YES on a price decrease, or None if unclear.
        """
        q = self.question.lower()
        if any(k in q for k in ["above", "higher", "over", "exceed", "up"]):
            return "UP"
        if any(k in q for k in ["below", "lower", "under", "drop", "down"]):
            return "DOWN"
        return None


@dataclass
class OrderResult:
    """Result of an order submission."""
    order_id: str
    status: str
    token_id: str
    side: str
    price: float
    size: float
    paper_mode: bool
    timestamp: float = 0.0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.status in ("matched", "live", "paper_filled") and self.error is None


class PolymarketClient:
    """
    High-level wrapper around the Polymarket CLOB API.

    Handles authentication, market discovery (finding active BTC short-duration
    markets), order book polling, and order execution. Supports both live and
    paper-trading modes.

    Authentication levels:
      - Level 0 (no auth): Read-only market data
      - Level 1 (private key): Order signing
      - Level 2 (API key): Order submission
    """

    GAMMA_API_URL = "https://gamma-api.polymarket.com"
    CLOB_API_URL = "https://clob.polymarket.com"

    def __init__(
        self,
        host: str,
        chain_id: int,
        private_key: str,
        funder: str,
        signature_type: int = 1,
        paper_mode: bool = True,
        trade_window_minutes: int = 5,
        paper_slippage_pct: float = 0.0,
        proxy_url: str = "",
    ) -> None:
        self.host = host
        self.chain_id = chain_id
        self.private_key = private_key
        self.funder = funder
        self.signature_type = signature_type
        self.paper_mode = paper_mode
        self.trade_window_minutes: int = trade_window_minutes
        self.paper_slippage_pct: float = paper_slippage_pct
        self._proxy_url: str = proxy_url

        self._client = None
        # Per-asset market cache: asset → {conditionId: MarketInfo}
        self._market_cache_by_asset: Dict[str, Dict[str, MarketInfo]] = {}
        self._last_cache_update_by_asset: Dict[str, float] = {}
        self._cache_ttl: float = 30.0  # Refresh market cache every 30 seconds
        self._order_count: int = 0
        # Consecutive empty discovery counter per asset — for operator alerting
        self._consecutive_empty_by_asset: Dict[str, int] = {}
        _proxy_kwargs = {"proxy": proxy_url} if proxy_url else {}
        self._http = httpx.AsyncClient(timeout=8.0, follow_redirects=True, **_proxy_kwargs)

        if not paper_mode:
            self._initialize_live_client()
        else:
            logger.info(
                "PolymarketClient initialized in PAPER MODE — no real orders will be placed."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Window helpers
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def _window_seconds(self) -> int:
        """Window duration in seconds: 300 (5-min) or 900 (15-min)."""
        return self.trade_window_minutes * 60

    @property
    def _slug_suffix(self) -> str:
        """Slug fragment that identifies the window series: '5m' or '15m'."""
        return f"{self.trade_window_minutes}m"

    @property
    def _gamma_tag_slug(self) -> str:
        """Gamma API tag slug for the window series: '5M' or '15M'."""
        return f"{self.trade_window_minutes}M"

    # ─────────────────────────────────────────────────────────────────────────
    # HTTP helpers
    # ─────────────────────────────────────────────────────────────────────────

    @async_retry(
        max_attempts=3,
        base_delay=0.5,
        max_delay=8.0,
        exceptions=(httpx.TransportError, httpx.TimeoutException),
    )
    async def _get(self, url: str, **kwargs) -> httpx.Response:
        """GET with automatic retry on network/timeout errors (3 attempts, exponential backoff)."""
        return await self._http.request("GET", url, **kwargs)

    # ─────────────────────────────────────────────────────────────────────────
    # Initialization
    # ─────────────────────────────────────────────────────────────────────────

    def _initialize_live_client(self) -> None:
        """Initialize the py-clob-client for live trading."""
        try:
            from py_clob_client.client import ClobClient

            logger.info("Initializing Polymarket CLOB client (LIVE MODE)...")

            self._client = ClobClient(
                self.host,
                key=self.private_key,
                chain_id=self.chain_id,
                signature_type=self.signature_type,
                funder=self.funder,
            )
            self._client.set_api_creds(self._client.create_or_derive_api_creds())

            logger.info(
                "Polymarket CLOB client authenticated. Funder: %s",
                self.funder[:10] + "..." if self.funder else "N/A",
            )
        except ImportError:
            logger.error("py-clob-client is not installed. Run: pip install py-clob-client")
            raise
        except Exception as exc:
            logger.error("Failed to initialize Polymarket client: %s", exc)
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # Market Discovery
    # ─────────────────────────────────────────────────────────────────────────

    async def get_active_markets(
        self,
        asset: str = "btc",
        min_liquidity: float = 1_000.0,
        force_refresh: bool = False,
    ) -> List[MarketInfo]:
        """
        Fetch active 5-minute up/down markets for any supported asset.

        Args:
            asset: "btc", "eth", or "sol" (lowercase).
            min_liquidity: Minimum USDC liquidity required.
            force_refresh: Bypass cache and fetch fresh data.

        Returns:
            List of MarketInfo for active 5-minute up/down markets.
        """
        asset = asset.lower()
        now = time.time()
        cache = self._market_cache_by_asset.get(asset, {})
        last_update = self._last_cache_update_by_asset.get(asset, 0.0)
        if (
            not force_refresh
            and cache
            and (now - last_update) < self._cache_ttl
        ):
            return list(cache.values())

        try:
            markets = await self._fetch_updown_markets(asset, min_liquidity)
            self._market_cache_by_asset[asset] = {m.condition_id: m for m in markets}
            self._last_cache_update_by_asset[asset] = now
            if markets:
                self._consecutive_empty_by_asset[asset] = 0
                logger.debug(
                    "[%s] Active %dm markets: %d | Best: '%s' | UP: %.2f | DOWN: %.2f | Liq: $%.0f",
                    asset.upper(), self.trade_window_minutes, len(markets),
                    markets[0].question[:55],
                    markets[0].yes_price,
                    markets[0].no_price,
                    markets[0].liquidity,
                )
            else:
                logger.warning(
                    "[%s] No active %dm up/down markets found (between windows?).",
                    asset.upper(), self.trade_window_minutes,
                )
                self._consecutive_empty_by_asset[asset] = (
                    self._consecutive_empty_by_asset.get(asset, 0) + 1
                )
            return markets
        except Exception as exc:
            logger.error("[%s] Failed to fetch %dm markets: %s", asset.upper(), self.trade_window_minutes, exc)
            return list(cache.values())  # Return stale cache on error

    async def _fetch_updown_markets(self, asset: str, min_liquidity: float) -> List[MarketInfo]:
        """
        Discover active up/down markets for the given asset and window size.

        Slug format: {asset}-updown-{Nm}-{unix_ts} aligned to N-minute UTC boundaries.
          5-min:  btc-updown-5m-1774926600  (300-second boundaries)
          15-min: btc-updown-15m-1774926400 (900-second boundaries)

        Primary: Direct slug probe (proven reliable).
        Fallback: Gamma Events tag search.
        """
        now_ts = int(time.time())

        # Strategy A: Direct slug probe (primary — proven reliable)
        markets = await self._fetch_via_slug_scan(asset, min_liquidity, now_ts)
        if markets:
            return markets

        # Strategy B: Gamma Events search by tag (fallback)
        logger.warning("[%s] Slug scan empty — falling back to Gamma tag search.", asset.upper())
        return await self._fetch_via_gamma_search(asset, min_liquidity, now_ts)

    # Backward-compat alias
    async def _fetch_5m_updown_markets(self, asset: str, min_liquidity: float) -> List[MarketInfo]:
        return await self._fetch_updown_markets(asset, min_liquidity)

    async def _fetch_via_gamma_search(self, asset: str, min_liquidity: float, now_ts: int) -> List[MarketInfo]:
        """
        Use Gamma Events API to search for active {asset}-updown-{Nm} events.
        """
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        slug_prefix = f"{asset}-updown-{self._slug_suffix}"

        end_date_min = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            resp = await self._get(
                f"{self.GAMMA_API_URL}/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "tag_slug": self._gamma_tag_slug,
                    "end_date_min": end_date_min,
                    "limit": 20,
                },
            )
            resp.raise_for_status()
            all_events = resp.json()
        except Exception as exc:
            logger.warning("[%s] Gamma Events search failed: %s", asset.upper(), exc)
            return []

        events = [e for e in all_events if slug_prefix in e.get("slug", "").lower()]

        markets: List[MarketInfo] = []
        for event in events:
            slug = event.get("slug", "")
            title = event.get("title", "")

            if slug_prefix not in slug.lower():
                continue

            # Check endDate is in the future (active window)
            end_date_str = event.get("endDate", "")
            if not end_date_str:
                continue
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                secs_remaining = (end_dt - now_dt).total_seconds()
                # Skip already-closed windows and windows too far in the future
                if secs_remaining < -30 or secs_remaining > self._window_seconds + 100:
                    continue
            except Exception:
                continue

            liquidity = float(event.get("liquidity") or 0)
            if liquidity < min_liquidity:
                continue

            gamma_markets = event.get("markets", [])
            if not gamma_markets:
                continue

            gm = gamma_markets[0]
            condition_id = gm.get("conditionId", "")
            if not condition_id:
                continue

            # Fetch token data — try Gamma clobTokenIds first (faster), then CLOB API
            market = await self._fetch_clob_tokens(
                condition_id=condition_id,
                question=gm.get("question") or title,
                end_date_iso=end_date_str,
                liquidity=liquidity,
                volume_24h=float(event.get("volume24hr") or 0),
                min_liquidity=min_liquidity,
                gamma_market=gm,  # Pass Gamma market object for clobTokenIds parsing
            )
            if market:
                markets.append(market)

        # Sort: fewest seconds remaining first (most in-progress window first)
        markets.sort(
            key=lambda m: self._parse_end_date_remaining(m.end_date_iso, now_ts) or 9999
        )
        return markets

    async def _fetch_via_slug_scan(self, asset: str, min_liquidity: float, now_ts: int) -> List[MarketInfo]:
        """
        Primary strategy: probe {asset}-updown-{Nm} slug timestamps in PARALLEL.

        Slugs are aligned to N-minute UTC boundaries:
          5-min:  slug = f'{asset}-updown-5m-{(unix_ts // 300) * 300}'
          15-min: slug = f'{asset}-updown-15m-{(unix_ts // 900) * 900}'

        All 4 candidates (prev/current/next 2 windows) are probed concurrently
        via asyncio.gather — total latency = 1 HTTP round-trip instead of 4.
        """
        ws = self._window_seconds
        base = (now_ts // ws) * ws
        offsets = [-1, 0, 1, 2]
        candidates = [base + o * ws for o in offsets]
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)

        # Run all 4 slug probes in parallel
        results = await asyncio.gather(
            *[self._probe_one_slug(asset, ts, min_liquidity, now_dt, now_ts) for ts in candidates],
            return_exceptions=True,
        )

        markets: List[MarketInfo] = []
        seen_conditions: set = set()
        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            if result.condition_id not in seen_conditions:
                seen_conditions.add(result.condition_id)
                markets.append(result)

        # Sort: fewest seconds remaining first (most in-progress = most tradeable)
        markets.sort(
            key=lambda m: self._parse_end_date_remaining(m.end_date_iso, now_ts) or 9999
        )
        return markets

    async def _probe_one_slug(
        self,
        asset: str,
        ts: int,
        min_liquidity: float,
        now_dt,
        now_ts: int,
    ) -> Optional["MarketInfo"]:
        """Probe a single {asset}-updown-{Nm}-{ts} slug. Returns MarketInfo or None."""
        slug = f"{asset}-updown-{self._slug_suffix}-{ts}"
        try:
            gamma_resp = await self._get(
                f"{self.GAMMA_API_URL}/events",
                params={"slug": slug},
            )
            gamma_resp.raise_for_status()
            events = gamma_resp.json()
        except Exception as exc:
            logger.debug("Slug probe failed for %s: %s", slug, exc)
            return None

        if not events:
            return None

        event = events[0]
        gamma_markets = event.get("markets", [])
        if not gamma_markets:
            return None

        gm = gamma_markets[0]
        condition_id = gm.get("conditionId", "")
        if not condition_id:
            return None

        end_date_str = gm.get("endDate") or event.get("endDate", "")
        if not end_date_str:
            return None

        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            secs_remaining = (end_dt - now_dt).total_seconds()
            # Accept markets that:
            #   - haven't expired yet (secs_remaining > -10)
            #   - aren't more than 2 full windows in the future (2 * window_seconds)
            # Using 2× allows newly-started windows to be picked up even when the
            # previous window still has time left (e.g. next window has ~900+N secs
            # remaining while current has N secs left — old limit of window+10 was
            # too tight and caused a gap where no market was found).
            if secs_remaining < -10 or secs_remaining > self._window_seconds * 2:
                logger.debug(
                    "Slug %s: secs_remaining=%.0f — outside accepted range (0 – %ds), skipping.",
                    slug, secs_remaining, self._window_seconds * 2,
                )
                return None
        except Exception:
            return None

        liquidity = float(event.get("liquidity") or gm.get("liquidity") or 0)
        if liquidity < min_liquidity:
            logger.debug(
                "Slug %s: liquidity $%.0f < min $%.0f — skipping.",
                slug, liquidity, min_liquidity,
            )
            return None

        question = gm.get("question") or event.get("title", "")
        volume_24h = float(event.get("volume24hr") or gm.get("volume24hr") or 0)

        market = await self._fetch_clob_tokens(
            condition_id=condition_id,
            question=question,
            end_date_iso=end_date_str,
            liquidity=liquidity,
            volume_24h=volume_24h,
            min_liquidity=min_liquidity,
            gamma_market=gm,
        )
        if market:
            logger.debug(
                "Slug probe hit: %s | %.0fs remaining | UP=%.4f DOWN=%.4f",
                slug, secs_remaining, market.yes_price, market.no_price,
            )
        return market

    def _parse_end_date_remaining(self, end_date_iso: str, now_ts: int) -> Optional[float]:
        """Parse end_date_iso and return seconds remaining. Returns None on failure."""
        if not end_date_iso:
            return None
        try:
            end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
            return (end_dt - now_dt).total_seconds()
        except Exception:
            return None

    async def _fetch_clob_tokens(
        self,
        condition_id: str,
        question: str,
        end_date_iso: str,
        liquidity: float,
        volume_24h: float,
        min_liquidity: float,
        gamma_market: Optional[Dict] = None,
    ) -> Optional["MarketInfo"]:
        """
        Fetch token_ids and live prices for a given conditionId.

        Strategy A: Use Gamma Market Object fields directly (faster, no extra API call).
          - clobTokenIds: JSON string "[\"token_yes\", \"token_no\"]"
          - outcomePrices: JSON string "[\"0.47\", \"0.53\"]"
          - outcomes: JSON string "[\"Up\", \"Down\"]"

        Strategy B: Fallback to CLOB API /markets/{conditionId} if Gamma data incomplete.

        Per official API reference (Market Object):
          clobTokenIds: "[\"123\",\"456\"]"  — CLOB token IDs for YES/NO
          outcomePrices: "[\"0.45\",\"0.55\"]" — current prices
          outcomes: "[\"Yes\",\"No\"]"         — outcome labels
        """
        # ── Strategy A: Parse directly from Gamma Market Object ──────────────
        if gamma_market:
            try:
                clob_ids_raw = gamma_market.get("clobTokenIds", "")
                prices_raw = gamma_market.get("outcomePrices", "")
                outcomes_raw = gamma_market.get("outcomes", "")

                if clob_ids_raw and prices_raw:
                    clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])

                    if len(clob_ids) >= 2 and len(prices) >= 2:
                        # Identify UP/YES vs DOWN/NO by outcome label
                        up_idx = 0
                        for i, o in enumerate(outcomes):
                            if str(o).lower() in ("up", "yes"):
                                up_idx = i
                                break
                        down_idx = 1 - up_idx if len(clob_ids) == 2 else (1 if up_idx == 0 else 0)

                        yes_token_id = str(clob_ids[up_idx])
                        no_token_id = str(clob_ids[down_idx])
                        yes_price = float(prices[up_idx])
                        no_price = float(prices[down_idx])

                        tick_size = float(gamma_market.get("orderPriceMinTickSize") or 0.01)
                        neg_risk = bool(gamma_market.get("negRisk", False))

                        logger.debug(
                            "Token IDs from Gamma clobTokenIds: UP=%s DOWN=%s",
                            yes_token_id[:16], no_token_id[:16],
                        )
                        return MarketInfo(
                            condition_id=condition_id,
                            question=question,
                            yes_token_id=yes_token_id,
                            yes_price=yes_price,
                            no_token_id=no_token_id,
                            no_price=no_price,
                            volume_24h=volume_24h,
                            liquidity=liquidity,
                            end_date_iso=end_date_iso,
                            tick_size=tick_size,
                            neg_risk=neg_risk,
                            fetched_at=time.time(),
                        )
            except Exception as exc:
                logger.debug("Gamma clobTokenIds parse failed: %s — falling back to CLOB API", exc)

        # ── Strategy B: Fallback to CLOB API ────────────────────────────────
        try:
            clob_resp = await self._get(
                f"{self.CLOB_API_URL}/markets/{condition_id}",
            )
            clob_resp.raise_for_status()
            clob_market = clob_resp.json()
        except Exception as exc:
            logger.warning("CLOB API error for condition %s: %s", condition_id[:16], exc)
            return None

        if not clob_market.get("active", False) or clob_market.get("closed", True):
            return None

        if not clob_market.get("accepting_orders", False):
            logger.debug("Market %s not accepting orders.", condition_id[:16])
            return None

        tokens = clob_market.get("tokens", [])
        if len(tokens) < 2:
            return None

        up_token = next(
            (t for t in tokens if t.get("outcome", "").lower() in ("up", "yes")),
            tokens[0],
        )
        down_token = next(
            (t for t in tokens if t.get("outcome", "").lower() in ("down", "no")),
            tokens[1],
        )

        return MarketInfo(
            condition_id=condition_id,
            question=question,
            yes_token_id=str(up_token.get("token_id", "")),
            yes_price=float(up_token.get("price", 0.5)),
            no_token_id=str(down_token.get("token_id", "")),
            no_price=float(down_token.get("price", 0.5)),
            volume_24h=volume_24h,
            liquidity=liquidity,
            end_date_iso=end_date_iso,
            tick_size=float(clob_market.get("minimum_tick_size", 0.01)),
            neg_risk=bool(clob_market.get("neg_risk", False)),
            fetched_at=time.time(),
        )

    async def _fetch_market_by_slug(self, slug: str, min_liquidity: float) -> Optional["MarketInfo"]:
        """
        Fetch a single market by its event slug.

        Uses Gamma Events API to get conditionId, then CLOB API for token data.
        Returns None if market is not found, closed, or below liquidity threshold.
        """
        # Step 1: Gamma Events API — get event metadata and conditionId
        try:
            gamma_resp = await self._get(
                f"{self.GAMMA_API_URL}/events",
                params={"slug": slug},
            )
            gamma_resp.raise_for_status()
            events = gamma_resp.json()
        except Exception as exc:
            logger.debug("Gamma API error for slug %s: %s", slug, exc)
            return None

        if not events:
            logger.debug("No event found for slug: %s", slug)
            return None

        event = events[0]

        # Skip closed events
        if event.get("closed", True):
            logger.debug("Event %s is closed — skipping.", slug)
            return None

        gamma_markets = event.get("markets", [])
        if not gamma_markets:
            logger.debug("Event %s has no markets.", slug)
            return None

        gm = gamma_markets[0]
        condition_id = gm.get("conditionId", "")
        if not condition_id:
            logger.debug("Event %s has no conditionId.", slug)
            return None

        # Check liquidity from Gamma (fast pre-filter)
        liquidity = float(event.get("liquidity") or gm.get("liquidity") or 0)
        if liquidity < min_liquidity:
            logger.debug(
                "Market %s liquidity $%.0f below threshold $%.0f — skipping.",
                slug, liquidity, min_liquidity,
            )
            return None

        end_date = gm.get("endDate") or event.get("endDate", "")
        question = gm.get("question") or event.get("title", "")
        volume_24h = float(event.get("volume24hr") or gm.get("volume24hr") or 0)

        # Step 2: CLOB API — get token_ids and live prices
        try:
            clob_resp = await self._get(
                f"{self.CLOB_API_URL}/markets/{condition_id}",
            )
            clob_resp.raise_for_status()
            clob_market = clob_resp.json()
        except Exception as exc:
            logger.warning("CLOB API error for condition %s: %s", condition_id[:16], exc)
            return None

        # CLOB market must be active and accepting orders
        if not clob_market.get("active", False) or clob_market.get("closed", True):
            logger.debug("CLOB market %s is inactive or closed.", condition_id[:16])
            return None

        tokens = clob_market.get("tokens", [])
        if len(tokens) < 2:
            logger.warning(
                "CLOB market %s has only %d tokens — expected 2.",
                condition_id[:16], len(tokens),
            )
            return None

        # For btc-updown-5m markets:
        #   outcome="Up"   → YES token (market resolves YES if BTC goes up)
        #   outcome="Down" → NO token
        up_token = next(
            (t for t in tokens if t.get("outcome", "").lower() in ("up", "yes")),
            tokens[0],
        )
        down_token = next(
            (t for t in tokens if t.get("outcome", "").lower() in ("down", "no")),
            tokens[1],
        )

        return MarketInfo(
            condition_id=condition_id,
            question=question,
            yes_token_id=str(up_token.get("token_id", "")),
            yes_price=float(up_token.get("price", 0.5)),
            no_token_id=str(down_token.get("token_id", "")),
            no_price=float(down_token.get("price", 0.5)),
            volume_24h=volume_24h,
            liquidity=liquidity,
            end_date_iso=end_date,
            tick_size=float(clob_market.get("minimum_tick_size", 0.01)),
            neg_risk=bool(clob_market.get("neg_risk", False)),
            fetched_at=time.time(),
        )

    async def _fetch_btc_markets_from_gamma(self, min_liquidity: float) -> List[MarketInfo]:
        """Legacy method — kept for backward compatibility. Delegates to new method."""
        return await self._fetch_5m_updown_markets("btc", min_liquidity)

    # ─────────────────────────────────────────────────────────────────────────
    # Price Polling
    # ─────────────────────────────────────────────────────────────────────────

    async def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        Fetch the current best price for a given outcome token.

        Price resolution order (fastest to slowest):
          1. Polymarket CLOB WebSocket cache (if attached, ~10-50ms)
          2. REST API /price endpoint (~200-500ms)

        Args:
            token_id: The Polymarket token ID for the outcome.
            side: "BUY" or "SELL".

        Returns:
            Current price as a float (0.0 to 1.0), or None on failure.
        """
        # 1. Try WebSocket cache first (real-time, lowest latency)
        ws_feed = getattr(self, "_ws_feed", None)
        if ws_feed is not None:
            ws_price = ws_feed.get_price(token_id, side)
            if ws_price is not None:
                logger.debug("Price from PM WS cache: token=%s price=%.4f", token_id[:16], ws_price)
                return ws_price

        # 2. Fallback to REST API
        try:
            response = await self._get(
                f"{self.CLOB_API_URL}/price",
                params={"token_id": token_id, "side": side},
            )
            response.raise_for_status()
            data = response.json()
            price = float(data.get("price", 0.0))
            return price if price > 0.0 else None
        except Exception as exc:
            # 404 = token no longer tradeable (market resolved/expired) → DEBUG only
            # Other errors stay at WARNING so they're visible in the dashboard
            exc_str = str(exc)
            if "404" in exc_str or "Not Found" in exc_str:
                logger.debug("Price unavailable (market resolved?) for token %s: 404", token_id[:16])
            else:
                logger.warning("Failed to get price for token %s: %s", token_id[:16], exc)
            return None

    async def get_order_book_midpoint(self, token_id: str) -> Optional[float]:
        """
        Fetch the order book midpoint price for a token.

        Returns:
            Midpoint price (0.0 to 1.0), or None on failure.
        """
        try:
            response = await self._get(
                f"{self.CLOB_API_URL}/midpoint",
                params={"token_id": token_id},
            )
            response.raise_for_status()
            data = response.json()
            return float(data.get("mid", 0.0))
        except Exception as exc:
            logger.warning(
                "Failed to get midpoint for token %s: %s", token_id[:16], exc
            )
            return None

    async def get_order_book(self, token_id: str) -> Optional[Dict]:
        """
        Fetch the full order book for a token via CLOB API.

        Per official API reference:
          GET /book?token_id={clob_token_id}

        Returns:
            Dict with 'bids', 'asks', 'min_order_size', 'tick_size', or None on failure.
        """
        try:
            response = await self._get(
                f"{self.CLOB_API_URL}/book",
                params={"token_id": token_id},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning("Failed to get order book for token %s: %s", token_id[:16], exc)
            return None

    async def get_best_ask(self, token_id: str) -> Optional[float]:
        """
        Get the best ask price from the order book (lowest ask = best price to BUY).

        This is the most accurate price for executing a BUY order.
        Falls back to /price endpoint if order book is unavailable.
        """
        book = await self.get_order_book(token_id)
        if book and book.get("asks"):
            try:
                return min(float(a["price"]) for a in book["asks"])
            except (KeyError, ValueError):
                pass
        # Fallback to /price endpoint
        return await self.get_market_price(token_id, "BUY")

    def attach_ws_feed(self, ws_feed: Any) -> None:
        """
        Attach a PolymarketWSFeed instance for real-time price lookups.

        When attached, get_market_price() will first check the WebSocket
        price cache (sub-50ms latency) before falling back to REST API.

        Args:
            ws_feed: PolymarketWSFeed instance.
        """
        self._ws_feed = ws_feed
        logger.info("PM WebSocket feed attached to PolymarketClient.")

    # ─────────────────────────────────────────────────────────────────────────
    # Order Execution
    # ─────────────────────────────────────────────────────────────────────────

    async def place_market_order(
        self,
        token_id: str,
        side: str,
        amount_usdc: float,
        market_info: Optional[MarketInfo] = None,
    ) -> OrderResult:
        """
        Place a market order for the given outcome token.

        In paper mode, simulates the fill at the current market price.
        In live mode, submits a FAK (Fill-And-Kill) order to the CLOB.

        Args:
            token_id: Polymarket outcome token ID.
            side: "BUY" or "SELL".
            amount_usdc: For BUY — USDC to spend (minimum $1.00 enforced).
                         For SELL — shares to sell (pass position.size_shares).
            market_info: Optional MarketInfo for price lookup.

        Returns:
            OrderResult describing the outcome.
        """
        self._order_count += 1
        timestamp = time.time()
        is_buy = side.upper() == "BUY"

        current_price = await self.get_market_price(token_id, side)
        if not current_price:
            logger.warning(
                "price lookup failed for token=%s side=%s — order aborted",
                token_id[:16], side,
            )
            return OrderResult(
                order_id="", status="error", token_id=token_id,
                side=side, price=0.0, size=0.0, paper_mode=self.paper_mode,
                timestamp=time.time(), error="price unavailable",
            )

        if is_buy:
            # amount_usdc = USDC to spend; enforce $1.00 minimum
            usdc = max(round(float(amount_usdc), 2), 1.00)
            shares = round(usdc / current_price, 6) if current_price > 0 else 0.0
        else:
            # amount_usdc is shares to sell (caller passes position.size_shares)
            # We trust the recorded_shares parameter directly because on-chain balance
            # checks via get_token_balance() suffer from indexing latency and will
            # return stale (dust) balances during rapid dump-hedge exits.
            shares = round(float(amount_usdc), 6)

            logger.info(
                "[LIVE] SELL intent | shares=%.6f | price=%.4f | token=%s",
                shares, current_price, token_id[:16],
            )
            usdc = round(shares * current_price, 2)

        if self.paper_mode:
            return self._simulate_paper_order(
                token_id, side, current_price, shares, usdc, timestamp
            )

        return await self._submit_live_order(
            token_id, side, current_price, shares, usdc, timestamp
        )

    def _simulate_paper_order(
        self,
        token_id: str,
        side: str,
        price: float,
        shares: float,
        amount_usdc: float,
        timestamp: float,
    ) -> OrderResult:
        """Simulate a paper trade fill with optional randomized slippage."""
        order_id = f"PAPER-{self._order_count:06d}-{int(timestamp)}"

        # Apply random slippage when configured (default 0 = no slippage).
        # BUY slippage is adverse (higher fill price), SELL is adverse (lower fill price).
        fill_price = price
        if self.paper_slippage_pct > 0.0:
            noise = random.uniform(-self.paper_slippage_pct, self.paper_slippage_pct)
            direction = 1 if side.upper() == "BUY" else -1
            fill_price = max(0.001, min(0.999, price + direction * abs(noise) * price))
            shares = round(amount_usdc / fill_price, 6) if side.upper() == "BUY" else shares

        logger.info(
            "[PAPER] %s %.4f shares @ $%.4f%s (cost: $%.2f USDC) | token: %s",
            side,
            shares,
            fill_price,
            f" [slip {(fill_price - price) * 100:+.3f}¢]" if self.paper_slippage_pct > 0 else "",
            amount_usdc,
            token_id[:16] + "...",
        )
        price = fill_price
        return OrderResult(
            order_id=order_id,
            status="paper_filled",
            token_id=token_id,
            side=side,
            price=price,
            size=shares,
            paper_mode=True,
            timestamp=timestamp,
        )
    async def get_token_balance(self, token_id: str) -> Optional[float]:
        """
        Return actual on-chain balance of a conditional token (in shares).
        Uses fixed-point 1e6 response from CLOB balance endpoint.
        Returns None if unavailable (paper mode or API error).
        Wraps the synchronous py_clob_client call in a thread pool executor.
        """
        if self.paper_mode or not self._client:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
            )
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._client.get_balance_allowance, params
            )
            raw = float(data.get("balance") or 0)
            return raw / 1_000_000.0
        except Exception as exc:
            logger.warning("Could not fetch token balance for %s: %s", token_id[:16], exc)
            return None

    async def has_sufficient_liquidity(self, token_id: str, side: str, amount_usdc: float) -> tuple[bool, str]:
        """
        Check whether the order book has enough liquidity before submitting an order.
        Returns (ok, reason).
        """
        book = await self.get_order_book(token_id)
        if not book:
            return False, "Order book unavailable"

        orders = book.get("asks", []) if side.upper() == "BUY" else book.get("bids", [])
        if not orders:
            return False, f"Order book empty on {'ask' if side == 'BUY' else 'bid'} side"

        # Sum total available liquidity at all price levels
        total_available = sum(
            float(o.get("price", 0)) * float(o.get("size", 0))
            for o in orders
        )
        if total_available < amount_usdc:
            return False, f"Available liquidity ${total_available:.2f} < required ${amount_usdc:.2f}"

        return True, "OK"

    async def _submit_live_order(
        self,
        token_id: str,
        side: str,
        price: float,
        shares: float,
        amount_usdc: float,
        timestamp: float,
    ) -> OrderResult:
        """Submit a real order to the Polymarket CLOB using FAK (Fill-And-Kill)."""
        if not self._client:
            return OrderResult(
                order_id="",
                status="error",
                token_id=token_id,
                side=side,
                price=price,
                size=shares,
                paper_mode=False,
                timestamp=timestamp,
                error="CLOB client not initialized",
            )

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            clob_side = BUY if side.upper() == "BUY" else SELL

            # Polymarket minimum: round_down(shares, 2) must be > 0 → shares >= 0.01
            if side.upper() == "SELL" and shares < 0.01:
                logger.warning(
                    "[LIVE] SELL aborted — shares too small to trade: %.6f | token=%s",
                    shares, token_id[:16],
                )
                return OrderResult(
                    order_id="", status="error", token_id=token_id,
                    side=side, price=price, size=0.0, paper_mode=False,
                    timestamp=timestamp,
                    error=f"SELL aborted: shares {shares:.6f} below 0.01 minimum",
                )

            logger.debug(
                "[LIVE] Submitting Limit FAK %s | token=%s | shares=%.6f | price=%.4f",
                side, token_id[:16], shares, price,
            )

            mo = OrderArgs(
                token_id=token_id,
                size=shares,
                price=price,
                side=clob_side,
            )

            loop = asyncio.get_event_loop()
            signed_order = await loop.run_in_executor(
                None, self._client.create_order, mo
            )
            response = await loop.run_in_executor(
                None, self._client.post_order, signed_order, OrderType.FAK
            )

            logger.debug("[LIVE] post_order raw response: %s", response)

            order_id = response.get("orderID", "")
            status = response.get("status", "unknown")
            success = response.get("success", True)
            error_msg = response.get("errorMsg", "")

            # Server-side rejection
            if not success or error_msg:
                logger.warning("[LIVE] FAK %s rejected — %s | token=%s", side, error_msg, token_id[:16])
                return OrderResult(
                    order_id=order_id, status="error", token_id=token_id,
                    side=side, price=price, size=0.0, paper_mode=False,
                    timestamp=timestamp, error=error_msg or "Server rejected order",
                )

            # FAK unmatched — no liquidity
            if status == "unmatched":
                logger.warning(
                    "[LIVE] FAK %s killed — no match | token=%s | %.4f",
                    side, token_id[:16], mo_amount,
                )
                return OrderResult(
                    order_id=order_id, status="unmatched", token_id=token_id,
                    side=side, price=price, size=0.0, paper_mode=False,
                    timestamp=timestamp, error="FAK killed — no orders to match",
                )

            # We MUST fetch the true matched size from the order status, because the
            # makingAmount/takingAmount fields in the post_order response are the 
            # REQUESTED amounts, not the MATCHED amounts! Relying on them causes
            # ghost positions if the FAK order only partially fills.
            if side.upper() == "BUY":
                cost_usdc = amount_usdc
                size_matched = round(amount_usdc / price, 6) if price > 0 else 0.0
            else:
                cost_usdc = round(shares * price, 4)
                size_matched = shares
            price_matched = price

            sm_raw = response.get("sizeMatched") or response.get("size_matched")
            if sm_raw is not None:
                sm = float(sm_raw)
                if sm > 1000:
                    sm = sm / 1_000_000.0
                size_matched = sm
                cost_usdc = round(size_matched * price_matched, 4)
            elif order_id:
                try:
                    await asyncio.sleep(0.15) # Brief pause for matching engine
                    loop = asyncio.get_event_loop()
                    order_status = await loop.run_in_executor(
                        None, self._client.get_order, order_id
                    )
                    if isinstance(order_status, dict):
                        sm_raw = order_status.get("size_matched") or order_status.get("sizeMatched")
                        if sm_raw is not None:
                            sm = float(sm_raw)
                            if sm > 1000:
                                sm = sm / 1_000_000.0
                            if sm > 0:
                                size_matched = sm
                                cost_usdc = round(size_matched * price_matched, 4)
                except Exception as e:
                    logger.debug("[LIVE] Could not fetch get_order for %s, using estimated fill size: %s", order_id[:16], e)

            if side.upper() == "BUY":
                logger.info("[LIVE] BUY fill — confirmed: %.6f shares @ %.4f (cost $%.2f)", size_matched, price_matched, cost_usdc)
            else:
                logger.info("[LIVE] SELL fill — confirmed: %.6f shares @ %.4f (proceeds $%.4f)", size_matched, price_matched, cost_usdc)

            logger.info(
                "[LIVE] %s filled | ID: %s | %.4f shares @ %.4f | $%.4f USDC",
                side, order_id[:20], size_matched, price_matched, cost_usdc,
            )

            return OrderResult(
                order_id=order_id,
                status="matched",
                token_id=token_id,
                side=side,
                price=price_matched,
                size=size_matched,
                paper_mode=False,
                timestamp=timestamp,
            )

        except Exception as exc:
            from py_clob_client.exceptions import PolyApiException
            if isinstance(exc, PolyApiException) and exc.status_code == 400:
                err_body = exc.error_msg or {}
                err_text = err_body.get("error", str(err_body)) if isinstance(err_body, dict) else str(err_body)
                # FAK no-match is normal trading behavior, not a system error
                if "no orders found" in err_text.lower() or "fak" in err_text.lower():
                    logger.warning("[LIVE] FAK %s killed — no match | token=%s", side, token_id[:16])
                    return OrderResult(
                        order_id="", status="unmatched", token_id=token_id,
                        side=side, price=price, size=0.0, paper_mode=False,
                        timestamp=timestamp, error="FAK killed — no orders to match",
                    )
                logger.warning("[LIVE] Order rejected (400): %s", err_text)
                return OrderResult(
                    order_id="", status="error", token_id=token_id,
                    side=side, price=price, size=0.0, paper_mode=False,
                    timestamp=timestamp, error=err_text,
                )
            logger.error("Live order submission failed: %s", exc)
            return OrderResult(
                order_id="",
                status="error",
                token_id=token_id,
                side=side,
                price=price,
                size=shares,
                paper_mode=False,
                timestamp=timestamp,
                error=str(exc),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Trade History
    # ─────────────────────────────────────────────────────────────────────────

    async def get_user_fills(
        self,
        funder_address: str,
        condition_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch trade history for a wallet from the Polymarket Data API.

        Uses https://data-api.polymarket.com/activity (public endpoint, no auth).

        Args:
            funder_address: Polygon wallet address (0x...).
            condition_id:   Filter to a single market. None = all markets.
            limit:          Max number of records to return (max 1000).

        Returns:
            List of fill dicts sorted by timestamp descending.
            Each dict includes: tokenID, side, size, price, timestamp,
            orderID, conditionId, outcome, transactionHash, title.
        """
        DATA_API = "https://data-api.polymarket.com"
        user = funder_address if funder_address.startswith("0x") else f"0x{funder_address}"
        params: Dict[str, Any] = {
            "limit": min(limit, 1000),
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
            "user": user,
        }
        if condition_id:
            params["market"] = condition_id

        try:
            resp = await self._get(f"{DATA_API}/activity", params=params)
            resp.raise_for_status()
            raw = resp.json()
            arr = raw if isinstance(raw, list) else (raw.get("data") or [])
        except Exception as exc:
            logger.warning("get_user_fills failed: %s", exc)
            return []

        fills = []
        for a in arr:
            if not isinstance(a, dict):
                continue
            if a.get("type") != "TRADE":
                continue
            fills.append({
                "id":               a.get("id"),
                "tokenID":          a.get("tokenID") or a.get("asset"),
                "side":             str(a.get("side", "")),
                "size":             float(a.get("size") or 0),
                "price":            float(a.get("price") or 0),
                "usdcSize":         float(a.get("usdcSize") or 0) or None,
                "timestamp":        int(a.get("timestamp") or 0),
                "orderID":          a.get("orderID"),
                "conditionId":      a.get("conditionId"),
                "outcome":          a.get("outcome"),
                "outcomeIndex":     a.get("outcomeIndex"),
                "transactionHash":  a.get("transactionHash"),
                "title":            a.get("title"),
                "slug":             a.get("slug"),
            })
        return fills

    # ─────────────────────────────────────────────────────────────────────────
    # On-chain Redemption
    # ─────────────────────────────────────────────────────────────────────────

    async def redeem_positions(
        self,
        condition_id: str,
    ) -> Dict[str, Any]:
        """
        Redeem winning conditional tokens for USDC directly on Polygon.

        Calls the Polymarket ConditionalTokens contract (redeemPositions selector
        0x01b7037c) with indexSets=[1, 2] to redeem both outcome slots at once.
        The contract pays out whichever token the wallet actually holds;
        the losing token pays $0 and the winning token pays $1/share.

        This is the correct approach for both latency-arb (single leg) and
        dump-hedge (both legs) — sending [1, 2] covers all cases.

        This is a LIVE-only operation — never called in paper mode.
        Should only be called after a market has resolved and the bot still
        holds tokens (e.g. SELL order failed and timeout has passed).

        Args:
            condition_id:   The market's conditionId (0x hex string).

        Returns:
            Dict with keys: success (bool), tx_hash (str|None), message (str).

        Raises:
            ImportError: If web3 is not installed.
            RuntimeError: If private key is not configured.
        """
        if self.paper_mode:
            logger.info("[PAPER] redeem_positions skipped (paper mode).")
            return {"success": True, "tx_hash": None, "message": "Paper mode — no redemption needed"}

        if not self.private_key:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY is required for on-chain redemption.")

        try:
            from web3 import Web3
            from web3.middleware import geth_poa_middleware
        except ImportError as exc:
            raise ImportError(
                "web3 is not installed. Run: pip install 'web3>=6.0.0,<8.0.0'\n"
                "Or add it to requirements.txt and run: make install"
            ) from exc

        # ConditionalTokens contract on Polygon (not the Exchange contract).
        # Same address for every market — only conditionId changes per window.
        CTF_CONTRACT    = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        # Native USDC on Polygon (Circle). The old bridged USDC.e
        # (0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174) is no longer used by Polymarket.
        USDC_ADDRESS    = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
        # Multiple RPC endpoints — tried in order, first successful one is used.
        RPC_URLS = [
            "https://polygon-rpc.com",
            "https://rpc.ankr.com/polygon",
            "https://polygon-bor-rpc.publicnode.com",
            "https://1rpc.io/matic",
        ]
        # MethodID for redeemPositions(address,bytes32,bytes32,uint256[])
        REDEEM_SELECTOR = "0x01b7037c"

        loop = asyncio.get_running_loop()

        def _send_redeem() -> Dict[str, Any]:
            # Build ABI-encoded calldata once — shared across all RPC attempts.
            # redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)
            cid_clean = condition_id[2:] if condition_id.startswith("0x") else condition_id
            cid_bytes32          = cid_clean.zfill(64).lower()
            parent_collection_id = "0" * 64
            collateral_bytes32   = "0" * 24 + USDC_ADDRESS[2:].lower()

            # indexSets = [1, 2] — redeem both outcome slots simultaneously.
            # The contract pays out only the tokens the wallet holds:
            #   slot 1 (indexSet=1, binary 01) = YES/UP outcome
            #   slot 2 (indexSet=2, binary 10) = NO/DOWN outcome
            # Sending both covers latency-arb (one leg) and dump-hedge (both legs).
            array_offset = 32 * 4
            array_length = 2

            encoded = (
                collateral_bytes32 +
                parent_collection_id +
                cid_bytes32 +
                hex(array_offset)[2:].zfill(64) +
                hex(array_length)[2:].zfill(64) +
                hex(1)[2:].zfill(64) +   # indexSets[0] = 1 (YES/UP)
                hex(2)[2:].zfill(64)     # indexSets[1] = 2 (NO/DOWN)
            )
            data = REDEEM_SELECTOR + encoded

            last_exc: Exception = RuntimeError("No RPC endpoints available")
            for rpc_url in RPC_URLS:
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
                    w3.middleware_onion.inject(geth_poa_middleware, layer=0)

                    account = w3.eth.account.from_key(self.private_key)
                    wallet_address = account.address

                    nonce     = w3.eth.get_transaction_count(wallet_address)
                    gas_price = w3.eth.gas_price

                    tx = {
                        "to":       CTF_CONTRACT,
                        "data":     data,
                        "value":    0,
                        "gas":      200_000,
                        "gasPrice": gas_price,
                        "nonce":    nonce,
                        "chainId":  137,
                    }

                    signed   = account.sign_transaction(tx)
                    tx_hash  = w3.eth.send_raw_transaction(signed.rawTransaction)
                    receipt  = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

                    if not receipt.get("status"):
                        raise RuntimeError(f"Tx reverted: {tx_hash.hex()}")

                    logger.info("redeem_positions: used RPC %s", rpc_url)
                    return {
                        "success": True,
                        "tx_hash": tx_hash.hex(),
                        "message": f"Redeemed condition {condition_id[:16]}. Tx: {tx_hash.hex()}",
                    }
                except Exception as exc:
                    logger.warning(
                        "redeem_positions: RPC %s failed — %s. Trying next...",
                        rpc_url, exc,
                    )
                    last_exc = exc

            raise last_exc

        try:
            result = await loop.run_in_executor(None, _send_redeem)
            logger.info(
                "redeem_positions ✓ | condition=%s | tx=%s",
                condition_id[:16], result["tx_hash"],
            )
            return result
        except Exception as exc:
            logger.error(
                "redeem_positions failed | condition=%s | error=%s",
                condition_id[:16], exc,
            )
            return {"success": False, "tx_hash": None, "message": str(exc)}

    async def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        await self._http.aclose()
        logger.debug("PolymarketClient HTTP session closed.")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID. Returns True on success."""
        if self.paper_mode:
            logger.info("[PAPER] Cancel order %s (simulated)", order_id)
            return True
        try:
            self._client.cancel(order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Return all open orders for the authenticated wallet."""
        if self.paper_mode:
            return []
        try:
            from py_clob_client.clob_types import OpenOrderParams
            return self._client.get_orders(OpenOrderParams()) or []
        except Exception as exc:
            logger.error("Failed to fetch open orders: %s", exc)
            return []

    
    async def get_portfolio_balance(self) -> Optional[float]:
        """
        Fetch the current USDC (collateral) balance.
        Uses Web3 to check both Native USDC and bridged USDC.e on Polygon,
        summing them up to determine the total available collateral.
        Checks the correct wallet based on signature type (Proxy vs EOA).
        """
        if self.paper_mode or not self._client:
            return None
            
        try:
            from web3 import Web3
            
            # Use reliable public RPCs for Polygon
            w3 = Web3(Web3.HTTPProvider("https://polygon.llamarpc.com"))
            if not w3.is_connected():
                w3 = Web3(Web3.HTTPProvider("https://polygon.drpc.org"))
                
            usdc_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
            usdc_e_contract = w3.eth.contract(address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", abi=usdc_abi)
            usdc_native_contract = w3.eth.contract(address="0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", abi=usdc_abi)
            
            # Determine which wallet holds the collateral based on signature type
            target_address = self.funder if self.signature_type in (1, 2) else self._client.get_address()
            if not target_address:
                return 0.0
                
            target_address = w3.to_checksum_address(target_address)
            
            # Run blocking web3 calls in an executor
            loop = asyncio.get_event_loop()
            
            def fetch_balances():
                usdc_e = usdc_e_contract.functions.balanceOf(target_address).call()
                usdc_native = usdc_native_contract.functions.balanceOf(target_address).call()
                return (usdc_e + usdc_native) / 1_000_000.0
                
            total_balance = await loop.run_in_executor(None, fetch_balances)
            return float(total_balance)
            
        except Exception as exc:
            logger.warning("Failed to fetch portfolio balance via Web3: %s", exc)
            return None
