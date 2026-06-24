#!/usr/bin/env python3
"""Polymarket CLOB WebSocket book feed for shadow dual strategies."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("shadow_clob_ws")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL = 5.0
RECONNECT_BASE = 2.0
RECONNECT_MAX = 30.0


class ClobWSBookFeed:
    """Thread-hosted CLOB market WS; maintains per-token ask ladders."""

    def __init__(
        self,
        on_book_update: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_book_update = on_book_update
        self._asks: dict[str, list[tuple[float, float]]] = {}
        self._best_ask: dict[str, float] = {}
        self._lock = threading.Lock()
        self._subscribed: set[str] = set()
        self._pending: set[str] = set()
        self._running = False
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws = None
        self._updates = 0
        self._last_msg = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def updates(self) -> int:
        return self._updates

    def start(self, timeout: float = 20.0) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._thread_main, name="clob_ws", daemon=True)
        self._thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._connected:
                return
            time.sleep(0.2)
        log.warning("CLOB WS not connected within %.0fs — will retry in background", timeout)

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)

    def subscribe(self, token_ids: list[str]) -> None:
        new = [t for t in token_ids if t and t not in self._subscribed]
        if not new:
            return
        with self._lock:
            for t in new:
                self._pending.add(t)
        if self._loop and self._connected:
            asyncio.run_coroutine_threadsafe(self._send_subscribe(new), self._loop)

    def seed_ladder(self, token_id: str, ladder: list[tuple[float, float]]) -> None:
        if not token_id or not ladder:
            return
        ladder = sorted(ladder, key=lambda x: x[0])
        with self._lock:
            self._asks[token_id] = list(ladder)
            self._best_ask[token_id] = ladder[0][0]

    def get_ask_ladder(self, token_id: str) -> list[tuple[float, float]]:
        with self._lock:
            return list(self._asks.get(token_id, []))

    def get_best_ask(self, token_id: str) -> float:
        with self._lock:
            return self._best_ask.get(token_id, 0.0)

    def stats(self) -> dict:
        with self._lock:
            cached = len(self._asks)
        age = time.time() - self._last_msg if self._last_msg else None
        return {
            "connected": self._connected,
            "subscribed": len(self._subscribed),
            "cached_tokens": cached,
            "updates": self._updates,
            "last_msg_age": round(age, 1) if age is not None else None,
        }

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_forever())
        finally:
            self._loop.close()

    async def _run_forever(self) -> None:
        delay = RECONNECT_BASE
        while self._running:
            try:
                await self._connect_and_run()
                delay = RECONNECT_BASE
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                log.warning("CLOB WS disconnected (%s), retry in %.0fs", exc, delay)
                self._connected = False
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)

    async def _connect_and_run(self) -> None:
        headers = [
            ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"),
            ("Origin", "https://polymarket.com"),
        ]
        async with websockets.connect(
            WS_URL,
            open_timeout=15,
            ping_interval=None,
            ping_timeout=None,
            max_size=2**20,
            extra_headers=headers,
        ) as ws:
            self._ws = ws
            self._connected = True
            log.info("CLOB WS connected %s", WS_URL)
            with self._lock:
                pending = list(self._pending | self._subscribed)
            if pending:
                await self._send_subscribe(pending)
            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    self._last_msg = time.time()
                    self._handle_raw(raw)
            finally:
                ping_task.cancel()
                self._ws = None
                self._connected = False

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except ConnectionClosed:
                break

    async def _close_ws(self) -> None:
        if self._ws:
            await self._ws.close()

    async def _send_subscribe(self, token_ids: list[str]) -> None:
        if not self._ws or not token_ids:
            return
        msg = {"type": "market", "assets_ids": token_ids}
        await self._ws.send(json.dumps(msg))
        with self._lock:
            for t in token_ids:
                self._subscribed.add(t)
                self._pending.discard(t)
        log.info("CLOB WS subscribed %d token(s)", len(token_ids))

    def _handle_raw(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        items = data if isinstance(data, list) else [data]
        for msg in items:
            if not isinstance(msg, dict):
                continue
            et = msg.get("event_type") or msg.get("type") or ""
            if et == "book":
                self._handle_book(msg)
            elif et == "price_change":
                self._handle_price_change(msg)

    def _handle_book(self, msg: dict) -> None:
        token_id = str(msg.get("asset_id") or msg.get("market") or "")
        if not token_id:
            return
        asks_raw = msg.get("asks") or []
        ladder: list[tuple[float, float]] = []
        for a in asks_raw:
            try:
                px = float(a.get("price", 0))
                sz = float(a.get("size", 0))
            except (TypeError, ValueError):
                continue
            if px > 0 and sz > 0:
                ladder.append((px, sz))
        ladder.sort(key=lambda x: x[0])
        with self._lock:
            self._asks[token_id] = ladder
            if ladder:
                self._best_ask[token_id] = ladder[0][0]
        self._updates += 1
        if self._on_book_update:
            self._on_book_update(token_id)

    def _handle_price_change(self, msg: dict) -> None:
        token_id = str(msg.get("asset_id") or msg.get("token_id") or "")
        side = str(msg.get("side") or "").upper()
        if not token_id or side != "SELL":
            return
        try:
            px = float(msg.get("price", 0))
            sz = float(msg.get("size") or msg.get("size_matched") or 0)
        except (TypeError, ValueError):
            return
        if px <= 0 or px > 1.0:
            return
        if sz <= 0:
            sz = 1.0
        with self._lock:
            ladder = list(self._asks.get(token_id, []))
            found = False
            for i, (lp, ls) in enumerate(ladder):
                if abs(lp - px) < 1e-9:
                    ladder[i] = (lp, max(ls, sz))
                    found = True
                    break
            if not found:
                ladder.append((px, sz))
            ladder.sort(key=lambda x: x[0])
            self._asks[token_id] = ladder
            self._best_ask[token_id] = ladder[0][0]
        self._updates += 1
        if self._on_book_update:
            self._on_book_update(token_id)
