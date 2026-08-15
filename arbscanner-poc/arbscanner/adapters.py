from __future__ import annotations
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
import httpx
from .models import DepthLevel, ExchangeMarket, Quote, canonical_utc_iso, source_time_is_naive, utc_now_iso
from .normalization import classify_market
from .sports import BETFAIR_EVENT_TYPE_ALIASES, BETFAIR_SAFE_MARKET_CODES, SUPPORTED_MARKETS, is_allowed_market_shape, normalize_sport
from .racing import canonical_runner_key, extract_race_number, extract_trap_number, is_withdrawn_status, normalize_runner_name, normalize_track
from .venues import BETFAIR, MATCHBOOK, ProviderCapabilities, VenueType, provider_id_for_name, venue_identity_for_name


class ExchangeError(RuntimeError):
    pass


class ReadOnlyVenueProvider:
    """Provider-neutral read-only market-data contract.

    The legacy ``ReadOnlyExchangeAdapter`` name remains as a compatibility
    subclass below. Provider capability metadata describes what a future
    execution adapter can do; the market-data contract itself stays read-only.
    """
    name = "base"
    provider_id = "base"
    venue_id = "base"
    venue_type = VenueType.EXCHANGE
    capabilities = ProviderCapabilities()

    def provider_manifest(self) -> dict[str, Any]:
        identity = venue_identity_for_name(self.name)
        return {
            "provider_id": self.provider_id or identity.provider_id,
            "venue_id": self.venue_id or identity.venue_id,
            "venue_name": self.name,
            "venue_type": self.venue_type.value if isinstance(self.venue_type, VenueType) else str(self.venue_type),
            "capabilities": self.capabilities.as_dict(),
        }

    async def fetch_markets(self, horizon_hours: int = 24, minimum_liquidity: float = 2.0) -> list[ExchangeMarket]:
        raise NotImplementedError

    async def fetch_market_state(self, event_id: str, market_id: str) -> dict[str, Any]:
        """Return a fresh targeted best-price state for one provider market."""
        raise NotImplementedError

    async def fetch_market_states(self, requests: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Bulk targeted state refresh. Providers override when batching is available."""
        async def one(req):
            try:
                return await self.fetch_market_state(str(req.get("event_id") or ""), str(req.get("market_id") or ""))
            except Exception as exc:
                return {"ok": False, "exchange": self.name, "venue_id": self.venue_id, "provider_id": self.provider_id,
                        "event_id": str(req.get("event_id") or ""), "market_id": str(req.get("market_id") or ""),
                        "status": "ERROR", "in_play": None, "latency_ms": 0, "captured_at": utc_now_iso(),
                        "quotes": {}, "error": str(exc)}
        return list(await asyncio.gather(*(one(req) for req in requests)))

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "exchange": self.name, "venue_id": self.venue_id, "provider_id": self.provider_id}

    async def account_balance(self) -> dict[str, Any]:
        raise ExchangeError(f"{self.name} account balance is not available")


class ReadOnlyExchangeAdapter(ReadOnlyVenueProvider):
    """Compatibility name for existing exchange adapters."""
    pass


class MatchbookAdapter(ReadOnlyExchangeAdapter):
    name = "Matchbook"
    provider_id = MATCHBOOK.provider_id
    venue_id = MATCHBOOK.venue.venue_id
    venue_type = MATCHBOOK.venue.venue_type
    capabilities = MATCHBOOK.capabilities
    EDGE_BASE = "https://api.matchbook.com/edge/rest"
    LOGIN_URL = "https://api.matchbook.com/bpapi/rest/security/session"

    _sport_cache: dict[str, str] = {}
    _sport_cache_at: float = 0.0

    def __init__(self, session_token: str | None = None, username: str | None = None, password: str | None = None,
                 mfa_code: str | None = None, commission_pct: float = 2.0, enabled_sports: list[str] | None = None,
                 live_lookback_hours: int = 8):
        self.session_token = session_token
        self.username = username
        self.password = password
        self.mfa_code = mfa_code
        self.commission_pct = commission_pct
        self.enabled_sports = [normalize_sport(x) for x in (enabled_sports or list(SUPPORTED_MARKETS))]
        self.live_lookback_hours = max(1, int(live_lookback_hours))
        # Read-only diagnostic evidence for the shared Matchbook adapter. This is
        # deliberately aggregate-only for Sports; Racing stores full runner-level
        # raw sides in source snapshots. No executable-side interpretation changes.
        self.last_price_side_audit: dict[str, Any] = {
            "current_interpretation": "back", "markets": 0, "by_sport": {},
            "note": "Diagnostic only; Matchbook side interpretation unchanged.",
        }

    async def login(self) -> str:
        if not self.username or not self.password:
            raise ExchangeError("Matchbook username/password not configured")
        payload: dict[str, str] = {"username": self.username, "password": self.password}
        if self.mfa_code:
            payload["mfa-code"] = self.mfa_code
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(self.LOGIN_URL, json=payload,
                                  headers={"Accept": "application/json", "Content-Type": "application/json"})
            if r.status_code >= 400:
                raise ExchangeError(f"Matchbook login failed ({r.status_code})")
            data = r.json()
        token = data.get("session-token") or data.get("session_token")
        if not token:
            raise ExchangeError("Matchbook login returned no session token")
        self.session_token = token
        return token

    async def account_balance(self) -> dict[str, Any]:
        if not self.session_token:
            await self.login()
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        if self.session_token:
            headers["session-token"] = self.session_token
        async with httpx.AsyncClient(timeout=20) as client:
            started = time.perf_counter()
            r = await client.get(f"{self.EDGE_BASE}/account/balance", headers=headers)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if r.status_code == 401 and self.username and self.password:
                await self.login()
                headers["session-token"] = self.session_token or ""
                started = time.perf_counter()
                r = await client.get(f"{self.EDGE_BASE}/account/balance", headers=headers)
                latency_ms = int((time.perf_counter() - started) * 1000)
            if r.status_code >= 400:
                raise ExchangeError(f"Matchbook account balance failed ({r.status_code})")
            data = r.json()
        # Matchbook has used a small number of field-name variants over time;
        # preserve the raw response while normalising the financial facts that
        # ArbScanner needs.  No value is silently fabricated.
        raw = data.get("balance") if isinstance(data, dict) and isinstance(data.get("balance"), dict) else data
        raw = raw if isinstance(raw, dict) else {}
        def num(*keys):
            for key in keys:
                value = raw.get(key)
                if value is None:
                    continue
                try: return float(value)
                except (TypeError, ValueError): pass
            return None
        available = num("balance", "available-balance", "available_balance", "available", "cash-balance", "cash_balance")
        exposure = num("exposure", "current-exposure", "current_exposure")
        currency = str(raw.get("currency") or raw.get("currency-code") or raw.get("currency_code") or "").upper() or None
        return {"ok": True, "exchange": self.name, "available": available, "exposure": exposure,
                "currency": currency, "latency_ms": latency_ms, "captured_at": utc_now_iso(), "raw": raw}

    async def _sports_lookup(self) -> dict[str, str]:
        now = time.time()
        if MatchbookAdapter._sport_cache and now - MatchbookAdapter._sport_cache_at < 6 * 3600:
            return dict(MatchbookAdapter._sport_cache)
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{self.EDGE_BASE}/lookups/sports", params={"per-page": "200", "status": "active"}, headers=headers)
            if r.status_code >= 400:
                return dict(MatchbookAdapter._sport_cache)
            data = r.json()
        rows = self._list_container(data, "sports")
        mapping = {}
        for row in rows:
            sid = str(row.get("id") or row.get("sport-id") or row.get("sport_id") or "")
            name = normalize_sport(str(row.get("name") or row.get("sport-name") or row.get("sport_name") or ""))
            if sid and name:
                mapping[sid] = name
        if mapping:
            MatchbookAdapter._sport_cache = mapping
            MatchbookAdapter._sport_cache_at = now
        return mapping

    async def _get_events(self, horizon_hours: int, minimum_liquidity: float) -> dict:
        if not self.session_token:
            await self.login()
        sports = await self._sports_lookup()
        selected_ids = [sid for sid, name in sports.items() if normalize_sport(name) in self.enabled_sports]
        now = datetime.now(timezone.utc)
        base_params = {
            "after": int((now - timedelta(hours=self.live_lookback_hours)).timestamp()),
            "before": int((now + timedelta(hours=horizon_hours)).timestamp()),
            "per-page": "100",
            "states": "open,suspended",
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "include-prices": "true",
            "price-depth": "3",
            "price-mode": "expanded",
            "minimum-liquidity": str(max(0.0, minimum_liquidity)),
            "markets-limit": "50",
        }
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        if self.session_token:
            headers["session-token"] = self.session_token
        events, latency_ms = [], 0

        # Query each enabled sport separately. A single combined request is ordered
        # by Matchbook and can fill the first 200 event slots with football/tennis,
        # making newly-enabled team sports look unsupported even though they are
        # available later in the result set. Two pages per sport keeps the request
        # bounded while preventing one busy sport from crowding out the others.
        sport_queries = selected_ids or [None]
        async with httpx.AsyncClient(timeout=25) as client:
            for sport_id in sport_queries:
                sport_params = dict(base_params)
                if sport_id:
                    sport_params["sport-ids"] = str(sport_id)
                for offset in (0, 100):
                    params = {**sport_params, "offset": str(offset)}
                    started = time.perf_counter()
                    r = await client.get(f"{self.EDGE_BASE}/events", params=params, headers=headers)
                    latency_ms = max(latency_ms, int((time.perf_counter() - started) * 1000))
                    if r.status_code == 401 and self.username and self.password:
                        await self.login()
                        headers["session-token"] = self.session_token or ""
                        started = time.perf_counter()
                        r = await client.get(f"{self.EDGE_BASE}/events", params=params, headers=headers)
                        latency_ms = max(latency_ms, int((time.perf_counter() - started) * 1000))
                    if r.status_code >= 400:
                        raise ExchangeError(f"Matchbook events failed ({r.status_code})")
                    page = r.json()
                    page_events = self._list_container(page, "events")
                    events.extend(page_events)
                    if len(page_events) < 100:
                        break
        return {"events": events, "_arbscanner_latency_ms": latency_ms, "_sport_map": sports}

    @staticmethod
    def _list_container(data: dict, key: str) -> list:
        value = data.get(key, []) if isinstance(data, dict) else []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for inner in (key, "items", "results"):
                if isinstance(value.get(inner), list):
                    return value[inner]
        return []

    @staticmethod
    def _canonical_price_side(side: str | None) -> str | None:
        """Canonicalise Matchbook's equivalent BACK/LAY vocabulary.

        Matchbook documents both ``back/lay`` and ``win/lose`` side names for
        back-lay prices.  ArbScanner keeps the original source value for audit
        while using one stable pair of diagnostic labels.  This does *not* alter
        the executable quote interpretation.
        """
        raw = str(side or "").strip().lower()
        if raw in {"back", "win"}:
            return "back"
        if raw in {"lay", "lose"}:
            return "lay"
        return None

    @classmethod
    def _raw_price_rows(cls, prices: list[dict], requested_side: str | None = None, source: str = "event_feed", observed_at: str | None = None) -> list[dict]:
        """Compact Matchbook two-sided price evidence for diagnostics.

        ``side`` is canonical BACK/LAY, while ``source_side`` preserves the exact
        value returned by Matchbook.  A side-specific probe may infer the
        canonical side from the request only when the payload omits a side label.
        """
        out = []
        requested = cls._canonical_price_side(requested_side)
        for p in prices or []:
            if not isinstance(p, dict):
                continue
            source_side = str(p.get("side") or p.get("type") or "").strip().lower()
            side = cls._canonical_price_side(source_side) or requested
            odds = p.get("odds") if p.get("odds") is not None else p.get("price")
            amount = p.get("available-amount")
            if amount is None:
                amount = p.get("available_amount", p.get("size", p.get("amount", 0)))
            try:
                odds_f, amount_f = float(odds), float(amount)
            except (TypeError, ValueError):
                continue
            if side and odds_f > 1.0 and amount_f >= 0:
                out.append({
                    "side": side, "source_side": source_side or None,
                    "requested_side": requested, "source": source,
                    "observed_at": observed_at, "odds": odds_f,
                    "available_amount": amount_f,
                    "side_inferred_from_request": bool(not source_side and requested),
                })
        return out

    @classmethod
    def _raw_side_book(cls, runners: list[dict], side: str) -> float | None:
        """Return an implied book for one raw Matchbook side, if complete."""
        values = []
        for runner in runners or []:
            candidates = [
                row for row in cls._raw_price_rows(runner.get("prices") or runner.get("offers") or [])
                if str(row.get("side") or "").lower() == str(side).lower()
                and float(row.get("available_amount") or 0.0) > 0.0
            ]
            if not candidates:
                return None
            best = max(candidates, key=lambda row: float(row.get("odds") or 0.0))
            odds = float(best.get("odds") or 0.0)
            if odds <= 1.0:
                return None
            values.append(odds)
        if len(values) < 2:
            return None
        return round(sum(100.0 / x for x in values), 6)

    def _record_price_side_audit(self, sport: str, event_name: str, market_name: str, runners: list[dict]) -> None:
        """Capture compact raw-side evidence without affecting quote selection."""
        back_book = self._raw_side_book(runners, "back")
        lay_book = self._raw_side_book(runners, "lay")
        root = self.last_price_side_audit
        root["markets"] = int(root.get("markets") or 0) + 1
        by_sport = root.setdefault("by_sport", {})
        item = by_sport.setdefault(str(sport or "Unknown"), {
            "markets": 0, "back_complete": 0, "lay_complete": 0, "both_complete": 0,
            "back_book_sum": 0.0, "lay_book_sum": 0.0, "suspicious": 0, "samples": [],
        })
        item["markets"] += 1
        if back_book is not None:
            item["back_complete"] += 1
            item["back_book_sum"] += float(back_book)
        if lay_book is not None:
            item["lay_complete"] += 1
            item["lay_book_sum"] += float(lay_book)
        suspicious = bool(back_book is not None and lay_book is not None and back_book >= 200.0 and lay_book + 20.0 < back_book)
        if back_book is not None and lay_book is not None:
            item["both_complete"] += 1
        if suspicious:
            item["suspicious"] += 1
        if len(item["samples"]) < 5 or suspicious:
            if len(item["samples"]) < 12:
                item["samples"].append({
                    "event_name": str(event_name or ""), "market_name": str(market_name or ""),
                    "runner_count": len(runners or []), "back_book_pct": back_book,
                    "lay_book_pct": lay_book, "suspicious": suspicious,
                })

    def _finalize_price_side_audit(self) -> None:
        for item in (self.last_price_side_audit.get("by_sport") or {}).values():
            bc, lc = int(item.get("back_complete") or 0), int(item.get("lay_complete") or 0)
            item["avg_back_book_pct"] = round(float(item.pop("back_book_sum", 0.0)) / bc, 6) if bc else None
            item["avg_lay_book_pct"] = round(float(item.pop("lay_book_sum", 0.0)) / lc, 6) if lc else None

    @classmethod
    def _depth_levels(cls, prices: list[dict], depth: int = 3) -> tuple[DepthLevel, ...]:
        """Canonical top-N BACK/LAY levels from Matchbook price rows."""
        rows = cls._raw_price_rows(prices)
        out: list[DepthLevel] = []
        for side in ("back", "lay"):
            candidates = [r for r in rows if r.get("side") == side and float(r.get("available_amount") or 0.0) > 0.0]
            candidates.sort(key=lambda r: float(r.get("odds") or 0.0), reverse=(side == "back"))
            seen = set()
            level = 0
            for row in candidates:
                odds = float(row.get("odds") or 0.0)
                if odds <= 1.0 or odds in seen:
                    continue
                seen.add(odds); level += 1
                out.append(DepthLevel(side=side.upper(), level=level, odds=odds, available_size=float(row.get("available_amount") or 0.0)))
                if level >= max(1, int(depth or 3)):
                    break
        return tuple(out)

    @classmethod
    def _best_back(cls, prices: list[dict]) -> tuple[float, float] | None:
        backs = []
        for p in prices or []:
            side = cls._canonical_price_side(p.get("side") or p.get("type"))
            if side != "back":
                continue
            odds = p.get("odds") or p.get("price")
            amount = p.get("available-amount")
            if amount is None:
                amount = p.get("available_amount", p.get("size", p.get("amount", 0)))
            try:
                odds_f, amount_f = float(odds), float(amount)
            except (TypeError, ValueError):
                continue
            if odds_f > 1.0 and amount_f > 0:
                backs.append((odds_f, amount_f))
        return max(backs, key=lambda x: x[0]) if backs else None

    @staticmethod
    def _event_sport(event: dict, sport_map: dict[str, str]) -> str:
        raw_sport = event.get("sport") or {}
        if isinstance(raw_sport, dict):
            name = raw_sport.get("name")
            sid = raw_sport.get("id")
        else:
            name, sid = raw_sport, None
        sid = str(event.get("sport-id") or event.get("sport_id") or sid or "")
        name = event.get("sport-name") or event.get("sport_name") or name or sport_map.get(sid)
        return normalize_sport(str(name or "Unknown"))

    @staticmethod
    def _in_play_flag(event: dict, market: dict) -> bool | None:
        for obj in (market, event):
            for key in ("in-play", "in_play", "in-running", "in_running", "live"):
                if key in obj:
                    value = obj.get(key)
                    if isinstance(value, bool):
                        return value
                    if str(value).lower() in {"true", "1", "yes"}: return True
                    if str(value).lower() in {"false", "0", "no"}: return False
        return None

    @staticmethod
    def _event_country(event: dict) -> str | None:
        raw = (
            event.get("country-code") or event.get("country_code") or event.get("countryCode")
            or event.get("country") or event.get("venue-country") or event.get("venue_country")
        )
        if isinstance(raw, dict):
            raw = raw.get("code") or raw.get("country-code") or raw.get("country_code") or raw.get("name")
        text = str(raw or "").strip()
        return text.upper() if text else None

    async def fetch_markets(self, horizon_hours: int = 24, minimum_liquidity: float = 2.0) -> list[ExchangeMarket]:
        self.last_price_side_audit = {
            "current_interpretation": "back", "markets": 0, "by_sport": {},
            "note": "Diagnostic only; Matchbook side interpretation unchanged.",
        }
        data = await self._get_events(horizon_hours, minimum_liquidity)
        latency_ms = int(data.pop("_arbscanner_latency_ms", 0))
        sport_map = data.pop("_sport_map", {})
        captured = utc_now_iso()
        markets_out: list[ExchangeMarket] = []
        for event in self._list_container(data, "events"):
            event_id = str(event.get("id") or event.get("event-id") or event.get("event_id") or "")
            event_name = str(event.get("name") or event.get("event-name") or event.get("event_name") or "")
            source_start_raw = event.get("start") or event.get("start-time") or event.get("start_time")
            start = canonical_utc_iso(source_start_raw) or (str(source_start_raw) if source_start_raw else None)
            sport = self._event_sport(event, sport_map)
            if sport not in self.enabled_sports:
                continue
            markets = event.get("markets") or []
            if isinstance(markets, dict):
                markets = markets.get("markets") or markets.get("items") or []
            for market in markets:
                market_id = str(market.get("id") or market.get("market-id") or market.get("market_id") or "")
                market_name = str(market.get("name") or market.get("market-name") or market.get("market_name") or "")
                runners = market.get("runners") or []
                if isinstance(runners, dict):
                    runners = runners.get("runners") or runners.get("items") or []
                active_runners = [r for r in runners if not is_withdrawn_status(r.get("status") or r.get("state"))]
                canonical, strategy = classify_market(market_name, len(active_runners), sport)
                if not is_allowed_market_shape(sport, canonical, strategy):
                    continue
                self._record_price_side_audit(sport, event_name, market_name, active_runners)
                in_play = self._in_play_flag(event, market)
                # v0.8.27 Racing is deliberately pre-race research only.
                if sport == "Greyhounds" and in_play is True:
                    continue
                mstatus = str(market.get("status") or market.get("state") or event.get("status") or event.get("state") or "OPEN").upper()
                section = "racing" if sport == "Greyhounds" else "sports"
                race_track = normalize_track(event_name) if section == "racing" else None
                race_number = extract_race_number(market_name, event_name) if section == "racing" else None
                quotes: list[Quote] = []
                for runner in active_runners:
                    best = self._best_back(runner.get("prices") or runner.get("offers") or [])
                    if not best:
                        continue
                    odds, liquidity = best
                    selection_raw = str(runner.get("name") or runner.get("runner-name") or runner.get("runner_name") or "")
                    selection_id = str(runner.get("id") or runner.get("runner-id") or runner.get("runner_id") or "")
                    trap = extract_trap_number(selection_raw, runner) if section == "racing" else None
                    selection = normalize_runner_name(selection_raw) if section == "racing" else selection_raw
                    quotes.append(Quote(
                        exchange=self.name, event_id=event_id, market_id=market_id, event_name=event_name,
                        market_name=market_name, selection_id=selection_id, selection=selection, odds=odds,
                        liquidity=liquidity, captured_at=captured, start_time=start,
                        commission_pct=self.commission_pct, commission_source="configured Matchbook rate",
                        source_latency_ms=latency_ms, market_type=canonical, strategy=strategy, sport=sport,
                        in_play=in_play, market_status=mstatus, raw=runner, section=section, trap_number=trap,
                        canonical_selection_key=canonical_runner_key(selection, trap) if section == "racing" else None,
                        runner_status=str(runner.get("status") or runner.get("state") or "ACTIVE"),
                        depth_levels=self._depth_levels(runner.get("prices") or runner.get("offers") or [], 3),
                    ))
                expected = len(active_runners) if strategy == "multi_runner_win" else (3 if strategy == "1x2" else 2)
                if expected >= 2 and len(quotes) == expected:
                    raw_market = dict(market)
                    raw_market["_arbscanner_source_start_raw"] = source_start_raw
                    raw_market["_arbscanner_start_utc"] = start
                    raw_market["_arbscanner_source_time_naive"] = source_time_is_naive(source_start_raw)
                    raw_market["_arbscanner_event_country"] = self._event_country(event)
                    raw_market["_arbscanner_catalogue_runner_count"] = int(expected)
                    raw_market["_arbscanner_priced_runner_count"] = int(len(quotes))
                    markets_out.append(ExchangeMarket(
                        exchange=self.name, event_id=event_id, market_id=market_id, event_name=event_name,
                        market_name=market_name, start_time=start, quotes=quotes, status=mstatus,
                        market_type=canonical, strategy=strategy, sport=sport, in_play=in_play, raw=raw_market,
                        section=section, race_track=race_track, race_number=race_number,
                    ))
        self._finalize_price_side_audit()
        return markets_out

    async def probe_racing_price_sides(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch only missing raw sides for already-matched Greyhound markets.

        This is a discovery-time diagnostic fallback.  It never replaces the
        normal Matchbook quote used by the arbitrage engine.  Requests are batched
        by side and event so a dozen matched races do not become dozens of serial
        HTTP calls.
        """
        if not requests:
            return []
        if not self.session_token:
            await self.login()
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        if self.session_token:
            headers["session-token"] = self.session_token
        wanted: dict[str, dict[tuple[str, str], dict[str, Any]]] = {"back": {}, "lay": {}}
        for req in requests:
            eid, mid = str(req.get("event_id") or ""), str(req.get("market_id") or "")
            if not eid or not mid:
                continue
            for raw_side in req.get("missing_sides") or []:
                side = self._canonical_price_side(raw_side)
                if side:
                    wanted[side][(eid, mid)] = req
        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for side in ("back", "lay"):
                pairs = wanted[side]
                if not pairs:
                    continue
                event_ids = sorted({eid for eid, _ in pairs})
                for offset in range(0, len(event_ids), 20):
                    chunk = event_ids[offset:offset + 20]
                    params = {
                        "ids": ",".join(chunk), "per-page": str(max(20, len(chunk))),
                        "states": "open,suspended", "exchange-type": "back-lay",
                        "odds-type": "DECIMAL", "include-prices": "true",
                        "price-depth": "3", "price-mode": "expanded",
                        "minimum-liquidity": "0", "markets-limit": "50", "side": side,
                    }
                    started = time.perf_counter()
                    r = await client.get(f"{self.EDGE_BASE}/events", params=params, headers=headers)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    if r.status_code == 401 and self.username and self.password:
                        await self.login(); headers["session-token"] = self.session_token or ""
                        started = time.perf_counter()
                        r = await client.get(f"{self.EDGE_BASE}/events", params=params, headers=headers)
                        latency_ms = int((time.perf_counter() - started) * 1000)
                    observed_at = utc_now_iso()
                    if r.status_code >= 400:
                        for eid, mid in pairs:
                            if eid in chunk:
                                results.append({"ok": False, "event_id": eid, "market_id": mid, "side": side,
                                                "latency_ms": latency_ms, "observed_at": observed_at,
                                                "error": f"Matchbook side probe failed ({r.status_code})", "runners": {}})
                        continue
                    events = self._list_container(r.json(), "events")
                    seen: set[tuple[str, str]] = set()
                    for event in events:
                        eid = str(event.get("id") or event.get("event-id") or event.get("event_id") or "")
                        markets = event.get("markets") or []
                        if isinstance(markets, dict):
                            markets = markets.get("markets") or markets.get("items") or []
                        for market in markets:
                            mid = str(market.get("id") or market.get("market-id") or market.get("market_id") or "")
                            if (eid, mid) not in pairs:
                                continue
                            seen.add((eid, mid))
                            runners = market.get("runners") or []
                            if isinstance(runners, dict):
                                runners = runners.get("runners") or runners.get("items") or []
                            runner_rows = {}
                            for runner in runners:
                                sid = str(runner.get("id") or runner.get("runner-id") or runner.get("runner_id") or "")
                                raw_prices = runner.get("prices") or runner.get("offers") or []
                                rows = [x for x in self._raw_price_rows(raw_prices, requested_side=side, source="side_probe", observed_at=observed_at) if x.get("side") == side]
                                if sid and rows:
                                    runner_rows[sid] = rows
                            results.append({"ok": True, "event_id": eid, "market_id": mid, "side": side,
                                            "latency_ms": latency_ms, "observed_at": observed_at, "runners": runner_rows})
                    for eid, mid in pairs:
                        if eid in chunk and (eid, mid) not in seen:
                            results.append({"ok": False, "event_id": eid, "market_id": mid, "side": side,
                                            "latency_ms": latency_ms, "observed_at": observed_at,
                                            "error": "Matched market absent from Matchbook side probe", "runners": {}})
        return results

    async def fetch_market_state(self, event_id: str, market_id: str) -> dict[str, Any]:
        if not self.session_token:
            await self.login()
        params = {
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "include-prices": "true",
            "price-depth": "3",
            "price-mode": "expanded",
            "minimum-liquidity": "0",
            "markets-limit": "50",
        }
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        if self.session_token:
            headers["session-token"] = self.session_token
        async with httpx.AsyncClient(timeout=15) as client:
            started = time.perf_counter()
            r = await client.get(f"{self.EDGE_BASE}/events/{event_id}", params=params, headers=headers)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if r.status_code == 401 and self.username and self.password:
                await self.login()
                headers["session-token"] = self.session_token or ""
                started = time.perf_counter()
                r = await client.get(f"{self.EDGE_BASE}/events/{event_id}", params=params, headers=headers)
                latency_ms = int((time.perf_counter() - started) * 1000)
            if r.status_code >= 400:
                raise ExchangeError(f"Matchbook event refresh failed ({r.status_code})")
            data = r.json()
        event = data
        if isinstance(data, dict) and isinstance(data.get("event"), dict):
            event = data["event"]
        elif isinstance(data, dict) and isinstance(data.get("events"), list) and data["events"]:
            event = data["events"][0]
        elif isinstance(data, dict) and isinstance(data.get("events"), dict):
            rows = self._list_container(data, "events")
            event = rows[0] if rows else data
        if not isinstance(event, dict):
            raise ExchangeError("Matchbook event refresh returned no event")
        markets = event.get("markets") or []
        if isinstance(markets, dict):
            markets = markets.get("markets") or markets.get("items") or []
        market = next((m for m in markets if str(m.get("id") or m.get("market-id") or m.get("market_id") or "") == str(market_id)), None)
        if market is None:
            return {"ok": False, "exchange": self.name, "event_id": str(event_id), "market_id": str(market_id),
                    "status": "MISSING", "in_play": None, "latency_ms": latency_ms, "captured_at": utc_now_iso(),
                    "quotes": {}, "error": "Market not present in refreshed event"}
        quotes = {}
        runners = market.get("runners") or []
        if isinstance(runners, dict):
            runners = runners.get("runners") or runners.get("items") or []
        for runner in runners:
            best = self._best_back(runner.get("prices") or runner.get("offers") or [])
            if not best:
                continue
            sid = str(runner.get("id") or runner.get("runner-id") or runner.get("runner_id") or "")
            if sid:
                raw_prices = runner.get("prices") or runner.get("offers") or []
                quotes[sid] = {"odds": float(best[0]), "liquidity": float(best[1]),
                               "raw_prices": self._raw_price_rows(raw_prices)}
        status = str(market.get("status") or market.get("state") or event.get("status") or event.get("state") or "OPEN").upper()
        return {"ok": True, "exchange": self.name, "event_id": str(event_id), "market_id": str(market_id),
                "status": status, "in_play": self._in_play_flag(event, market), "latency_ms": latency_ms,
                "captured_at": utc_now_iso(), "quotes": quotes}

    async def fetch_market_states(self, requests: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not requests:
            return []
        if not self.session_token:
            await self.login()
        headers = {"Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}
        if self.session_token:
            headers["session-token"] = self.session_token
        requested = {(str(x.get("event_id") or ""), str(x.get("market_id") or "")): x for x in requests}
        by_event: dict[str, set[str]] = {}
        for event_id, market_id in requested:
            by_event.setdefault(event_id, set()).add(market_id)
        out: dict[tuple[str, str], dict[str, Any]] = {}
        event_ids = [x for x in by_event if x]
        async with httpx.AsyncClient(timeout=20) as client:
            for offset in range(0, len(event_ids), 20):
                chunk = event_ids[offset:offset + 20]
                params = {
                    "ids": ",".join(chunk), "per-page": str(max(20, len(chunk))), "states": "open,suspended,closed,graded",
                    "exchange-type": "back-lay", "odds-type": "DECIMAL", "include-prices": "true",
                    "price-depth": "3", "price-mode": "expanded", "minimum-liquidity": "0", "markets-limit": "50",
                }
                started = time.perf_counter()
                r = await client.get(f"{self.EDGE_BASE}/events", params=params, headers=headers)
                latency_ms = int((time.perf_counter() - started) * 1000)
                if r.status_code == 401 and self.username and self.password:
                    await self.login(); headers["session-token"] = self.session_token or ""
                    started = time.perf_counter(); r = await client.get(f"{self.EDGE_BASE}/events", params=params, headers=headers)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                if r.status_code >= 400:
                    for eid in chunk:
                        for mid in by_event.get(eid, set()):
                            out[(eid, mid)] = {"ok": False, "exchange": self.name, "event_id": eid, "market_id": mid,
                                "status": "ERROR", "in_play": None, "latency_ms": latency_ms, "captured_at": utc_now_iso(),
                                "quotes": {}, "error": f"Matchbook bulk refresh failed ({r.status_code})"}
                    continue
                data = r.json(); events = self._list_container(data, "events")
                returned_events = set()
                for event in events:
                    eid = str(event.get("id") or event.get("event-id") or event.get("event_id") or "")
                    if eid not in by_event: continue
                    returned_events.add(eid)
                    markets = event.get("markets") or []
                    if isinstance(markets, dict): markets = markets.get("markets") or markets.get("items") or []
                    market_map = {str(m.get("id") or m.get("market-id") or m.get("market_id") or ""): m for m in markets}
                    for mid in by_event.get(eid, set()):
                        market = market_map.get(mid)
                        if market is None:
                            out[(eid, mid)] = {"ok": False, "exchange": self.name, "event_id": eid, "market_id": mid,
                                "status": "MISSING", "in_play": None, "latency_ms": latency_ms, "captured_at": utc_now_iso(),
                                "quotes": {}, "error": "Market not present in bulk refresh"}
                            continue
                        quotes = {}
                        runners = market.get("runners") or []
                        if isinstance(runners, dict): runners = runners.get("runners") or runners.get("items") or []
                        for runner in runners:
                            best = self._best_back(runner.get("prices") or runner.get("offers") or [])
                            if not best: continue
                            sid = str(runner.get("id") or runner.get("runner-id") or runner.get("runner_id") or "")
                            if sid:
                                raw_prices = runner.get("prices") or runner.get("offers") or []
                                quotes[sid] = {"odds": float(best[0]), "liquidity": float(best[1]),
                                               "raw_prices": self._raw_price_rows(raw_prices)}
                        status = str(market.get("status") or market.get("state") or event.get("status") or event.get("state") or "OPEN").upper()
                        out[(eid, mid)] = {"ok": True, "exchange": self.name, "event_id": eid, "market_id": mid,
                            "status": status, "in_play": self._in_play_flag(event, market), "latency_ms": latency_ms,
                            "captured_at": utc_now_iso(), "quotes": quotes}
                for eid in chunk:
                    if eid not in returned_events:
                        for mid in by_event.get(eid, set()):
                            out.setdefault((eid, mid), {"ok": False, "exchange": self.name, "event_id": eid, "market_id": mid,
                                "status": "MISSING", "in_play": None, "latency_ms": latency_ms, "captured_at": utc_now_iso(),
                                "quotes": {}, "error": "Event not present in bulk refresh"})
        return [out.get((str(req.get("event_id") or ""), str(req.get("market_id") or "")),
                        {"ok": False, "exchange": self.name, "event_id": str(req.get("event_id") or ""),
                         "market_id": str(req.get("market_id") or ""), "status": "MISSING", "in_play": None,
                         "latency_ms": 0, "captured_at": utc_now_iso(), "quotes": {}, "error": "No bulk state"}) for req in requests]

    async def health(self) -> dict[str, Any]:
        try:
            markets = await self.fetch_markets(horizon_hours=6, minimum_liquidity=2.0)
            counts = {}
            for m in markets: counts[m.sport] = counts.get(m.sport, 0) + 1
            return {"ok": True, "exchange": self.name, "markets": len(markets), "sport_counts": counts,
                    "message": "Read-only multi-sport market data available"}
        except Exception as e:
            return {"ok": False, "exchange": self.name, "message": str(e)}


class BetfairDelayedAdapter(ReadOnlyExchangeAdapter):
    name = "Betfair delayed"
    provider_id = BETFAIR.provider_id
    venue_id = BETFAIR.venue.venue_id
    venue_type = BETFAIR.venue.venue_type
    capabilities = BETFAIR.capabilities
    RPC = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    ACCOUNT_RPC = "https://api.betfair.com/exchange/account/json-rpc/v1"
    KEEPALIVE = "https://identitysso.betfair.com/api/keepAlive"

    def __init__(self, app_key: str | None = None, session_token: str | None = None, commission_pct: float = 2.0,
                 enabled_sports: list[str] | None = None, live_lookback_hours: int = 8,
                 requested_feed_entitlement: str = "delayed"):
        self.app_key = app_key
        self.session_token = session_token
        requested = str(requested_feed_entitlement or "delayed").strip().lower()
        self.requested_feed_entitlement = requested if requested in {"delayed", "live"} else "delayed"
        self.effective_feed_entitlement = "delayed" if self.requested_feed_entitlement == "delayed" else "unknown"
        self.feed_reason = "Configured Delayed App Key" if self.requested_feed_entitlement == "delayed" else "Awaiting provider market-book confirmation"
        self.commission_pct = commission_pct  # fallback only
        self._discount_rate: float | None = None
        self.enabled_sports = [normalize_sport(x) for x in (enabled_sports or list(SUPPORTED_MARKETS))]
        self.live_lookback_hours = max(1, int(live_lookback_hours))
        # Read-only discovery telemetry. This deliberately sits outside the
        # executable ExchangeMarket list so an incomplete Betfair field can be
        # visible in Racing Monitor without being eligible for matching/arbitrage.
        self.last_racing_discovery: dict[str, Any] = {
            "event_type_visible": None, "event_type_name": None, "catalogue": 0,
            "books_returned": 0, "fully_priced": 0, "incomplete_prices": 0,
            "missing_books": 0, "in_play_excluded": 0, "normalised": 0, "rows": [],
        }
        self.last_available_event_types: list[str] = []

    def _effective_feed_from_book(self, book: dict[str, Any] | None) -> str:
        """Derive effective Betfair data quality from provider evidence.

        ``isMarketDataDelayed`` is returned on MarketBook responses by Betfair.
        Requested LIVE never upgrades evidence by configuration alone: when the
        provider flag is absent, LIVE-requested data remains UNKNOWN/fail-closed.
        """
        book = book or {}
        delayed_flag = book.get("isMarketDataDelayed")
        if delayed_flag is True:
            effective, reason = "delayed", "Betfair MarketBook reports isMarketDataDelayed=true"
        elif delayed_flag is False:
            effective, reason = "live", "Betfair MarketBook reports isMarketDataDelayed=false"
        elif self.requested_feed_entitlement == "delayed":
            effective, reason = "delayed", "Delayed App Key selected; provider delay flag unavailable"
        else:
            effective, reason = "unknown", "LIVE requested but provider delay flag unavailable"
        self.effective_feed_entitlement = effective
        self.feed_reason = reason
        return effective

    def _headers(self) -> dict[str, str]:
        if not self.app_key or not self.session_token:
            raise ExchangeError(f"Betfair {self.requested_feed_entitlement} app key/session token not configured")
        return {"X-Application": self.app_key, "X-Authentication": self.session_token,
                "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ArbScanner-PoC/0.9.36"}

    @staticmethod
    def _rpc_error_text(err: Any) -> str:
        """Preserve Betfair's useful APING error code/details instead of only ANGX-xxxx."""
        if not isinstance(err, dict):
            return str(err)
        parts: list[str] = []
        message = err.get("message")
        if message:
            parts.append(str(message))
        data = err.get("data")
        if isinstance(data, dict):
            api_exc = data.get("APINGException") or data.get("AccountAPINGException") or {}
            if isinstance(api_exc, dict):
                code = api_exc.get("errorCode")
                details = api_exc.get("errorDetails")
                if code:
                    parts.append(str(code))
                if details:
                    parts.append(str(details))
            exception_name = data.get("exceptionname")
            if exception_name and not parts:
                parts.append(str(exception_name))
        return " · ".join(dict.fromkeys(parts)) or str(err)

    async def _rpc(self, method: str, params: dict, rpc_id: int = 1) -> tuple[Any, int]:
        payload = {"jsonrpc": "2.0", "method": f"SportsAPING/v1.0/{method}", "params": params, "id": rpc_id}
        async with httpx.AsyncClient(timeout=25) as client:
            started = time.perf_counter()
            r = await client.post(self.RPC, json=payload, headers=self._headers())
            latency_ms = int((time.perf_counter() - started) * 1000)
            if r.status_code >= 400:
                detail = r.text.strip().replace("\n", " ")[:500]
                suffix = f": {detail}" if detail else ""
                raise ExchangeError(f"Betfair {method} failed ({r.status_code}){suffix}")
            try:
                data = r.json()
            except Exception as exc:
                raise ExchangeError(f"Betfair {method} returned invalid JSON: {exc}") from exc
        if isinstance(data, dict) and data.get("error"):
            raise ExchangeError(f"Betfair {method}: {self._rpc_error_text(data['error'])}")
        return (data.get("result", []) if isinstance(data, dict) else data), latency_ms

    async def _account_rpc(self, method: str, params: dict | None = None, rpc_id: int = 50) -> Any:
        payload = {"jsonrpc": "2.0", "method": f"AccountAPING/v1.0/{method}", "params": params or {}, "id": rpc_id}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(self.ACCOUNT_RPC, json=payload, headers=self._headers())
            if r.status_code >= 400:
                raise ExchangeError(f"Betfair account {method} failed ({r.status_code})")
            data = r.json()
        if isinstance(data, dict) and data.get("error"):
            raise ExchangeError(f"Betfair account {method}: {self._rpc_error_text(data['error'])}")
        return data.get("result", {}) if isinstance(data, dict) else data

    async def get_discount_rate(self) -> float:
        if self._discount_rate is not None:
            return self._discount_rate
        try:
            details = await self._account_rpc("getAccountDetails")
            self._discount_rate = max(0.0, min(100.0, float(details.get("discountRate") or 0.0)))
        except Exception:
            self._discount_rate = 0.0
        return self._discount_rate

    async def account_balance(self) -> dict[str, Any]:
        started = time.perf_counter()
        funds = await self._account_rpc("getAccountFunds")
        details = {}
        try:
            details = await self._account_rpc("getAccountDetails")
        except Exception:
            details = {}
        latency_ms = int((time.perf_counter() - started) * 1000)
        available = funds.get("availableToBetBalance") if isinstance(funds, dict) else None
        exposure = funds.get("exposure") if isinstance(funds, dict) else None
        retained = funds.get("retainedCommission") if isinstance(funds, dict) else None
        currency = details.get("currencyCode") if isinstance(details, dict) else None
        return {"ok": True, "exchange": self.name,
                "available": None if available is None else float(available),
                "exposure": None if exposure is None else float(exposure),
                "retained_commission": None if retained is None else float(retained),
                "currency": str(currency or "").upper() or None,
                "latency_ms": latency_ms, "captured_at": utc_now_iso(),
                "raw": {"funds": funds, "details": details}}

    async def keep_alive(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(self.KEEPALIVE, headers=self._headers())
            if r.status_code >= 400:
                raise ExchangeError(f"Betfair keepAlive failed ({r.status_code})")
            return r.json()

    async def list_event_types(self) -> dict[str, str]:
        result, _ = await self._rpc("listEventTypes", {"filter": {}}, rpc_id=79)
        out: dict[str, str] = {}
        self.last_available_event_types = []
        greyhound_name = None
        for row in result:
            et = (row or {}).get("eventType") or {}
            eid, name = str(et.get("id") or ""), str(et.get("name") or "")
            if name:
                self.last_available_event_types.append(name)
            if normalize_sport(name) == "Greyhounds":
                greyhound_name = name
            # Prefer explicit exchange aliases, then fall back to canonical
            # sport normalization so newly-supported event types do not require IDs.
            sport = BETFAIR_EVENT_TYPE_ALIASES.get(name) or normalize_sport(name)
            if eid and sport in SUPPORTED_MARKETS and sport in self.enabled_sports:
                out[eid] = sport
        self.last_racing_discovery["event_type_visible"] = bool(greyhound_name)
        self.last_racing_discovery["event_type_name"] = greyhound_name
        return out

    async def _catalogue_window(self, event_type_id: str, sport: str, market_code: str,
                                start: datetime, end: datetime, depth: int = 0) -> list[dict]:
        # MARKET_DESCRIPTION has weight 1. Betfair caps market-data requests at 200 points,
        # so maxResults must not exceed 200. v0.6 used 1000 and could start failing with
        # TOO_MUCH_DATA as the number of matching markets grew.
        max_results = 200
        params = {
            "filter": {
                "eventTypeIds": [event_type_id],
                "marketTypeCodes": [market_code],
                "marketStartTime": {
                    "from": start.isoformat().replace("+00:00", "Z"),
                    "to": end.isoformat().replace("+00:00", "Z"),
                },
            },
            "marketProjection": ["EVENT", "EVENT_TYPE", "RUNNER_DESCRIPTION", "MARKET_START_TIME", "MARKET_DESCRIPTION"],
            "sort": "FIRST_TO_START",
            "maxResults": str(max_results),
        }
        rows, _ = await self._rpc("listMarketCatalogue", params, rpc_id=100 + depth)

        # listMarketCatalogue has no offset. If a window is full, split it so we do not
        # silently truncate a busy sport/day. Stop splitting below 15 minutes.
        span = end - start
        if len(rows) >= max_results and span > timedelta(minutes=15) and depth < 12:
            mid = start + span / 2
            left = await self._catalogue_window(event_type_id, sport, market_code, start, mid, depth + 1)
            right = await self._catalogue_window(event_type_id, sport, market_code, mid, end, depth + 1)
            dedup: dict[str, dict] = {}
            for row in left + right:
                market_id = str(row.get("marketId") or "")
                if market_id:
                    dedup[market_id] = row
            return list(dedup.values())
        return rows

    async def list_catalogue(self, horizon_hours: int) -> list[dict]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=self.live_lookback_hours)
        end = now + timedelta(hours=horizon_hours)
        event_types = await self.list_event_types()
        all_rows: list[dict] = []
        seen: set[str] = set()
        for event_type_id, sport in event_types.items():
            # Query each market type separately. Besides keeping request weights predictable,
            # this stops a very busy MATCH_ODDS universe crowding out smaller safe market types.
            for market_code in sorted(BETFAIR_SAFE_MARKET_CODES.get(sport, {"MATCH_ODDS"})):
                rows = await self._catalogue_window(event_type_id, sport, market_code, start, end)
                for row in rows:
                    market_id = str(row.get("marketId") or "")
                    if not market_id or market_id in seen:
                        continue
                    row["_arbscanner_sport"] = sport
                    all_rows.append(row)
                    seen.add(market_id)
        return all_rows

    async def list_books(self, market_ids: list[str]) -> tuple[list[dict], int]:
        if not market_ids:
            return [], 0
        all_books, latencies = [], []
        for i in range(0, len(market_ids), 20):
            chunk = market_ids[i:i + 20]
            params = {"marketIds": chunk, "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True}}
            books, latency = await self._rpc("listMarketBook", params, rpc_id=2 + i)
            all_books.extend(books)
            latencies.append(latency)
        return all_books, max(latencies) if latencies else 0

    @staticmethod
    def _depth_levels(runner: dict, depth: int = 3) -> tuple[DepthLevel, ...]:
        """Canonical top-N BACK/LAY levels from a Betfair market-book runner."""
        ex = runner.get("ex") or {}
        out: list[DepthLevel] = []
        for source, side, reverse in (("availableToBack", "BACK", True), ("availableToLay", "LAY", False)):
            rows = []
            for raw in ex.get(source) or []:
                try:
                    odds, size = float(raw.get("price") or 0.0), float(raw.get("size") or 0.0)
                except (TypeError, ValueError):
                    continue
                if odds > 1.0 and size > 0.0:
                    rows.append((odds, size))
            rows.sort(key=lambda x: x[0], reverse=reverse)
            for level, (odds, size) in enumerate(rows[:max(1, int(depth or 3))], 1):
                out.append(DepthLevel(side=side, level=level, odds=odds, available_size=size))
        return tuple(out)

    @staticmethod
    def _effective_commission(cat: dict, discount_rate: float, fallback: float) -> tuple[float, str]:
        desc = cat.get("description") or {}
        base = desc.get("marketBaseRate")
        try:
            base_f = float(base)
        except (TypeError, ValueError):
            return float(fallback), "configured fallback (Betfair API market rate unavailable)"
        discount_allowed = bool(desc.get("discountAllowed", False))
        discount = max(0.0, min(100.0, float(discount_rate))) if discount_allowed else 0.0
        effective = max(0.0, base_f * (1.0 - discount / 100.0))
        if discount_allowed:
            source = f"Betfair API marketBaseRate {base_f:.2f}% less account discount {discount:.2f}%"
        else:
            source = f"Betfair API marketBaseRate {base_f:.2f}% (market discount not allowed)"
        return effective, source

    async def fetch_markets(self, horizon_hours: int = 24, minimum_liquidity: float = 2.0) -> list[ExchangeMarket]:
        # Reset per-call telemetry while preserving the event-type visibility set
        # by list_event_types() during list_catalogue().
        self.last_racing_discovery = {
            "event_type_visible": None, "event_type_name": None, "catalogue": 0,
            "books_returned": 0, "fully_priced": 0, "incomplete_prices": 0,
            "missing_books": 0, "in_play_excluded": 0, "normalised": 0, "rows": [],
        }
        catalogue = await self.list_catalogue(horizon_hours)
        discount_rate = await self.get_discount_rate()
        ids = [str(m.get("marketId")) for m in catalogue if m.get("marketId")]
        books, latency_ms = await self.list_books(ids)
        book_by_id = {str(b.get("marketId")): b for b in books}
        captured = utc_now_iso()
        out: list[ExchangeMarket] = []
        racing_rows: list[dict[str, Any]] = []
        for cat in catalogue:
            market_id = str(cat.get("marketId") or "")
            book = book_by_id.get(market_id, {})
            event = cat.get("event") or {}
            event_id = str(event.get("id") or "")
            event_name = str(event.get("name") or "")
            source_start_raw = cat.get("marketStartTime") or event.get("openDate")
            start = canonical_utc_iso(source_start_raw) or (str(source_start_raw) if source_start_raw else None)
            sport = normalize_sport(cat.get("_arbscanner_sport") or ((cat.get("eventType") or {}).get("name")))
            if sport not in self.enabled_sports:
                continue
            runner_catalog = cat.get("runners") or []
            is_greyhound = sport == "Greyhounds"
            raw_racing_row = None
            if is_greyhound:
                self.last_racing_discovery["catalogue"] += 1
                raw_event = event if isinstance(event, dict) else {}
                raw_racing_row = {
                    "exchange": self.name, "event_id": event_id, "market_id": market_id,
                    "event_name": event_name, "market_name": str(cat.get("marketName") or "Win"),
                    "event_start": start,
                    "source_start_raw": source_start_raw,
                    "source_time_naive": source_time_is_naive(source_start_raw),
                    "race_track": normalize_track(event_name),
                    "race_number": extract_race_number(str(cat.get("marketName") or ""), event_name),
                    "runner_count": len(runner_catalog),
                    "catalogue_runner_count": len(runner_catalog),
                    "country": raw_event.get("countryCode") or raw_event.get("country"),
                    "book_returned": bool(book), "priced_runner_count": 0,
                    "match_status": "diagnostic", "quality_band": "failed",
                    "in_play": bool(book.get("inplay")) if "inplay" in book else None,
                    "market_status": str(book.get("status") or ("MISSING" if not book else "OPEN")).upper(),
                    "reason": "Betfair catalogue race detected",
                }
                if book:
                    self.last_racing_discovery["books_returned"] += 1
                else:
                    self.last_racing_discovery["missing_books"] += 1
                    raw_racing_row["reason"] = "Catalogue race detected; market book not returned"
            
            runner_by_id = {str(r.get("selectionId") or ""): r for r in runner_catalog}
            book_runners = book.get("runners") or []
            active_book_runners = [r for r in book_runners if not is_withdrawn_status(r.get("status"))]
            active_count = len(active_book_runners) or len(runner_catalog)
            if raw_racing_row is not None:
                raw_priced = 0
                for raw_runner in active_book_runners:
                    available = ((raw_runner.get("ex") or {}).get("availableToBack") or [])
                    try:
                        if any(float(p.get("price") or 0.0) > 1.0 and float(p.get("size") or 0.0) > 0.0 for p in available):
                            raw_priced += 1
                    except (TypeError, ValueError):
                        pass
                raw_racing_row["runner_count"] = active_count
                raw_racing_row["priced_runner_count"] = raw_priced
                raw_racing_row["missing_price_count"] = max(0, int(active_count) - int(raw_priced))
            desc_type = str((cat.get("description") or {}).get("marketType") or "").upper()
            market_name = str(cat.get("marketName") or "")
            canonical, strategy = classify_market(market_name, active_count, sport)
            if sport == "Greyhounds" and desc_type in {"WIN", "WINNER"}:
                canonical, strategy = "win", "multi_runner_win"
            elif sport == "Football":
                if desc_type == "OVER_UNDER_25": canonical, strategy = "over/under 2.5 goals", "two-way"
                elif desc_type == "BOTH_TEAMS_TO_SCORE": canonical, strategy = "both teams to score", "two-way"
                elif desc_type == "MATCH_ODDS": canonical, strategy = "match odds", "1x2" if active_count == 3 else "two-way"
            elif desc_type in {"MATCH_ODDS", "MONEYLINE", "MONEY_LINE", "WINNER"}:
                if active_count == 2:
                    canonical, strategy = "match winner", "two-way"
                elif active_count == 3:
                    canonical, strategy = classify_market(market_name or "Match Odds", 3, sport)
            if not is_allowed_market_shape(sport, canonical, strategy):
                if raw_racing_row is not None:
                    raw_racing_row["match_status"] = "rejected"
                    raw_racing_row["reason"] = f"Betfair market shape not eligible: {desc_type or market_name or 'unknown'}"
                    racing_rows.append(raw_racing_row)
                continue
            expected = active_count if strategy == "multi_runner_win" else (3 if strategy == "1x2" else 2)
            if expected < 2:
                if raw_racing_row is not None:
                    raw_racing_row["match_status"] = "rejected"
                    raw_racing_row["reason"] = f"Betfair field unavailable: {expected} active runners"
                    racing_rows.append(raw_racing_row)
                continue
            commission_pct, commission_source = self._effective_commission(cat, discount_rate, self.commission_pct)
            in_play = bool(book.get("inplay")) if "inplay" in book else None
            if sport == "Greyhounds" and in_play is True:
                self.last_racing_discovery["in_play_excluded"] += 1
                if raw_racing_row is not None:
                    raw_racing_row["reason"] = "Catalogue race detected; in-play excluded from Racing pre-race qualification"
                    raw_racing_row["match_status"] = "rejected"
                    racing_rows.append(raw_racing_row)
                continue
            mstatus = str(book.get("status") or "OPEN").upper()
            section = "racing" if sport == "Greyhounds" else "sports"
            race_track = normalize_track(event_name) if section == "racing" else None
            race_number = extract_race_number(market_name, event_name) if section == "racing" else None
            quotes: list[Quote] = []
            for runner in active_book_runners:
                selection_id = str(runner.get("selectionId") or "")
                cat_runner = runner_by_id.get(selection_id, {})
                selection_raw = str(cat_runner.get("runnerName") or selection_id)
                available = ((runner.get("ex") or {}).get("availableToBack") or [])
                if not available:
                    continue
                best = max(available, key=lambda p: float(p.get("price") or 0))
                try:
                    odds, liquidity = float(best.get("price")), float(best.get("size"))
                except (TypeError, ValueError):
                    continue
                if odds <= 1.0 or liquidity < minimum_liquidity:
                    continue
                trap = extract_trap_number(selection_raw, cat_runner) if section == "racing" else None
                selection = normalize_runner_name(selection_raw) if section == "racing" else selection_raw
                raw = dict(cat_runner)
                raw["book_runner"] = runner
                quotes.append(Quote(
                    exchange=self.name, event_id=event_id, market_id=market_id, event_name=event_name,
                    market_name=market_name or canonical, selection_id=selection_id, selection=selection,
                    odds=odds, liquidity=liquidity, captured_at=captured, start_time=start,
                    commission_pct=commission_pct, commission_source=commission_source, source_latency_ms=latency_ms,
                    market_type=canonical, strategy=strategy, sport=sport, in_play=in_play, market_status=mstatus, raw=raw,
                    section=section, trap_number=trap,
                    canonical_selection_key=canonical_runner_key(selection, trap) if section == "racing" else None,
                    runner_status=str(runner.get("status") or "ACTIVE"),
                    feed_entitlement=self._effective_feed_from_book(book), market_data_transport="poll",
                    depth_levels=self._depth_levels(runner, 3),
                ))
            if raw_racing_row is not None:
                raw_racing_row["priced_runner_count"] = len(quotes)
                raw_racing_row["missing_price_count"] = max(0, int(expected) - len(quotes))
                if len(quotes) == expected:
                    self.last_racing_discovery["fully_priced"] += 1
                    self.last_racing_discovery["normalised"] += 1
                    raw_racing_row["quality_band"] = "complete"
                    raw_racing_row["reason"] = "Complete Betfair field; eligible for strict race matching"
                    raw_racing_row["match_status"] = "unmatched"
                elif not book:
                    raw_racing_row["reason"] = "Catalogue race detected; market book not returned"
                    raw_racing_row["match_status"] = "rejected"
                else:
                    self.last_racing_discovery["incomplete_prices"] += 1
                    raw_racing_row["reason"] = f"Incomplete Betfair prices: {len(quotes)}/{expected} active runners priced"
                    raw_racing_row["match_status"] = "rejected"
                racing_rows.append(raw_racing_row)
            if len(quotes) == expected:
                out.append(ExchangeMarket(
                    exchange=self.name, event_id=event_id, market_id=market_id, event_name=event_name,
                    market_name=market_name or canonical, start_time=start, quotes=quotes,
                    status=mstatus, market_type=canonical, strategy=strategy, sport=sport, in_play=in_play,
                    raw={"catalogue": cat, "book": book, "discount_rate": discount_rate,
                         "effective_commission_pct": commission_pct, "commission_source": commission_source,
                         "_arbscanner_source_start_raw": source_start_raw, "_arbscanner_start_utc": start,
                         "_arbscanner_source_time_naive": source_time_is_naive(source_start_raw),
                         "_arbscanner_event_country": (event.get("countryCode") or event.get("country")),
                         "_arbscanner_catalogue_runner_count": int(expected),
                         "_arbscanner_priced_runner_count": int(len(quotes)),
                         "_arbscanner_requested_feed_entitlement": self.requested_feed_entitlement,
                         "_arbscanner_effective_feed_entitlement": self._effective_feed_from_book(book),
                         "_arbscanner_market_data_delayed": book.get("isMarketDataDelayed")},
                    section=section, race_track=race_track, race_number=race_number,
                ))
        self.last_racing_discovery["rows"] = racing_rows
        return out

    async def fetch_market_state(self, event_id: str, market_id: str) -> dict[str, Any]:
        books, latency_ms = await self.list_books([str(market_id)])
        if not books:
            return {"ok": False, "exchange": self.name, "event_id": str(event_id), "market_id": str(market_id),
                    "status": "MISSING", "in_play": None, "latency_ms": latency_ms, "captured_at": utc_now_iso(),
                    "quotes": {}, "error": "Market book not returned"}
        book = books[0]
        quotes = {}
        for runner in book.get("runners") or []:
            sid = str(runner.get("selectionId") or "")
            available = ((runner.get("ex") or {}).get("availableToBack") or [])
            if not sid or not available:
                continue
            best = max(available, key=lambda row: float(row.get("price") or 0.0))
            try:
                odds = float(best.get("price") or 0.0)
                liquidity = float(best.get("size") or 0.0)
            except (TypeError, ValueError):
                continue
            if odds > 1.0 and liquidity > 0.0:
                quotes[sid] = {"odds": odds, "liquidity": liquidity}
        effective_feed = self._effective_feed_from_book(book)
        return {"ok": True, "exchange": self.name, "event_id": str(event_id), "market_id": str(market_id),
                "status": str(book.get("status") or "OPEN").upper(),
                "in_play": bool(book.get("inplay")) if "inplay" in book else None,
                "latency_ms": latency_ms, "captured_at": utc_now_iso(), "quotes": quotes,
                "requested_feed_entitlement": self.requested_feed_entitlement,
                "effective_feed_entitlement": effective_feed, "feed_reason": self.feed_reason}

    async def fetch_market_states(self, requests: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not requests:
            return []
        market_ids = [str(x.get("market_id") or "") for x in requests if x.get("market_id")]
        books, latency_ms = await self.list_books(market_ids)
        by_id = {str(book.get("marketId") or ""): book for book in books}
        out=[]
        captured=utc_now_iso()
        for req in requests:
            event_id=str(req.get("event_id") or ""); market_id=str(req.get("market_id") or "")
            book=by_id.get(market_id)
            if not book:
                out.append({"ok": False, "exchange": self.name, "event_id": event_id, "market_id": market_id,
                            "status": "MISSING", "in_play": None, "latency_ms": latency_ms, "captured_at": captured,
                            "quotes": {}, "error": "Market book not returned"}); continue
            quotes={}
            for runner in book.get("runners") or []:
                sid=str(runner.get("selectionId") or ""); available=((runner.get("ex") or {}).get("availableToBack") or [])
                if not sid or not available: continue
                best=max(available,key=lambda row: float(row.get("price") or 0.0))
                try: odds=float(best.get("price") or 0.0); liquidity=float(best.get("size") or 0.0)
                except (TypeError,ValueError): continue
                if odds>1.0 and liquidity>0: quotes[sid]={"odds":odds,"liquidity":liquidity}
            effective_feed = self._effective_feed_from_book(book)
            out.append({"ok": True, "exchange": self.name, "event_id": event_id, "market_id": market_id,
                        "status": str(book.get("status") or "OPEN").upper(),
                        "in_play": bool(book.get("inplay")) if "inplay" in book else None,
                        "latency_ms": latency_ms, "captured_at": captured, "quotes": quotes,
                        "requested_feed_entitlement": self.requested_feed_entitlement,
                        "effective_feed_entitlement": effective_feed, "feed_reason": self.feed_reason})
        return out

    async def market_result(self, market_id: str, runner_names: dict[str, str] | None = None) -> dict[str, Any] | None:
        books, _ = await self.list_books([market_id])
        if not books:
            return None
        book = books[0]
        if str(book.get("status") or "").upper() != "CLOSED":
            return None
        winners = []
        for r in book.get("runners") or []:
            if str(r.get("status") or "").upper() == "WINNER":
                sid = str(r.get("selectionId") or "")
                winners.append({"winner": (runner_names or {}).get(sid, sid), "winner_id": sid})
        if len(winners) != 1:
            return None
        return {**winners[0], "market_id": market_id, "status": "CLOSED"}

    async def health(self) -> dict[str, Any]:
        try:
            result, _ = await self._rpc("listEventTypes", {"filter": {}}, rpc_id=99)
            discount = await self.get_discount_rate()
            available = [str((x.get("eventType") or {}).get("name") or "") for x in result]
            return {"ok": True, "exchange": self.name, "event_types": len(result), "enabled_sports": self.enabled_sports,
                    "available_event_types": available, "discount_rate": discount,
                    "message": f"Delayed API authenticated; multi-sport discovery ready; account discount {discount:.2f}%"}
        except Exception as e:
            return {"ok": False, "exchange": self.name, "message": str(e)}


async def fetch_all(adapters: list[ReadOnlyExchangeAdapter], horizon_hours: int, minimum_liquidity: float):
    async def fetch_one(adapter: ReadOnlyExchangeAdapter):
        started = time.perf_counter()
        try:
            result = await adapter.fetch_markets(horizon_hours, minimum_liquidity)
            return result, int((time.perf_counter() - started) * 1000), None
        except Exception as exc:
            return None, int((time.perf_counter() - started) * 1000), exc

    results = await asyncio.gather(*(fetch_one(a) for a in adapters))
    markets: list[ExchangeMarket] = []
    statuses: list[dict[str, Any]] = []
    for adapter, packed in zip(adapters, results):
        result, latency_ms, error = packed
        provider_id = str(getattr(adapter, "provider_id", None) or provider_id_for_name(getattr(adapter, "name", "")) or getattr(adapter, "name", "unknown")).lower()
        identity = venue_identity_for_name(getattr(adapter, "name", ""))
        venue_id = str(getattr(adapter, "venue_id", None) or (identity.venue_id if identity else provider_id))
        if error is not None:
            status = {"exchange": adapter.name, "provider_id": provider_id, "venue_id": venue_id, "ok": False, "message": str(error), "markets": 0, "latency_ms": latency_ms}
            if hasattr(adapter, "requested_feed_entitlement"):
                status["requested_feed_entitlement"] = str(getattr(adapter, "requested_feed_entitlement", "unknown") or "unknown")
                status["effective_feed_entitlement"] = str(getattr(adapter, "effective_feed_entitlement", "unknown") or "unknown")
                status["feed_reason"] = str(getattr(adapter, "feed_reason", "") or "")
            statuses.append(status)
        else:
            result = result or []
            markets.extend(result)
            sport_counts = {}
            live_count = 0
            for m in result:
                sport_counts[m.sport] = sport_counts.get(m.sport, 0) + 1
                if m.in_play is True: live_count += 1
            status = {"exchange": adapter.name, "provider_id": provider_id, "venue_id": venue_id, "ok": True, "message": "OK", "markets": len(result),
                      "sport_counts": sport_counts, "live_markets": live_count, "latency_ms": latency_ms}
            if hasattr(adapter, "requested_feed_entitlement"):
                status["requested_feed_entitlement"] = str(getattr(adapter, "requested_feed_entitlement", "unknown") or "unknown")
                status["effective_feed_entitlement"] = str(getattr(adapter, "effective_feed_entitlement", "unknown") or "unknown")
                status["feed_reason"] = str(getattr(adapter, "feed_reason", "") or "")
            racing_discovery = getattr(adapter, "last_racing_discovery", None)
            if isinstance(racing_discovery, dict):
                status["racing_discovery"] = racing_discovery
            price_side_audit = getattr(adapter, "last_price_side_audit", None)
            if isinstance(price_side_audit, dict):
                status["price_side_audit"] = price_side_audit
            statuses.append(status)
    return markets, statuses
