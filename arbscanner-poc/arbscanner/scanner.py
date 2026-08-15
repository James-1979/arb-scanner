from __future__ import annotations
import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from .adapters import BetfairDelayedAdapter, MatchbookAdapter, fetch_all
from .alerts import qualifies_for_alert, send_macos_notification_diagnostic
from .db import DB
from .engine import arb_edge, best_strategy_legs, diagnose_equal_return, simulate_equal_return, strategy_routing_diagnostics
from .execution import build_execution_plan, stress_test_plan
from .monitor_timing import MonitorTimingObserver
from .models import ExchangeMarket, Leg, MarketMatch, Quote, Scenario, source_time_is_naive, utc_now_iso
from .normalization import align_quotes, classify_market, event_similarity, match_markets, norm_selection, norm_text, normalize_sport, racing_pair_identity
from .quality import assess_data_quality, quality_profile
from .sports import enabled_sports_from_config
from .racing import normalize_track, seconds_to_off, track_similarity, runner_match_score
from .lifecycle import event_phase
from .secrets import SecretStore
from .provider_runtime import ProviderRuntimeRegistry, default_provider_runtime_registry
from .modes import canonical_mode_value
from .strategy_engines import EngineRuntime, MarketEvidence
from . import __version__


class Scanner:
    def __init__(self, db: DB, secrets: SecretStore, producer: str = "app", provider_runtime: ProviderRuntimeRegistry | None = None):
        self.db = db
        self.secrets = secrets
        self.producer = str(producer or "app").lower()
        self.provider_runtime = provider_runtime or default_provider_runtime_registry()
        # 0.9.16: strategy selection is routed through the engine framework. The
        # default legacy instances preserve established behaviour without retaining
        # a second embedded strategy path.
        self.db.ensure_default_engines()
        self.engine_runtime = EngineRuntime(self.db, mode_provider=lambda: canonical_mode_value(self.db.get_setting("mode", "sim")))

    def _settings(self) -> dict:
        # The scanner is continuous in v0.7.5. Strategy/risk settings are the
        # current saved configuration; operational economic modes are SIM and LIVE.
        return self.db.get_setting("config", {}) or {}

    def _adapters(self, mode: str | None = None):
        """Build market-data adapters, then apply the SIM/LIVE venue eligibility gate.

        A provider transport may be shared physically, but evidence eligibility is
        independent per economic mode. Disabled mode feeds are never handed to the
        scanner/engine pipeline for that mode.
        """
        adapters = self.provider_runtime.build_market_data_adapters(self._settings(), self.secrets)
        if mode is None:
            return adapters
        mode = canonical_mode_value(mode)
        controls = {x["provider_id"]: x for x in self.db.venue_controls()}
        flag = "live_feed_enabled" if mode == "live" else "sim_feed_enabled"
        return [a for a in adapters if bool((controls.get(str(getattr(a, "provider_id", "")).lower()) or {}).get(flag, False))]

    def _persist_refreshed_sessions(self, adapters) -> None:
        updates = {}
        for adapter in adapters:
            if isinstance(adapter, MatchbookAdapter) and adapter.session_token:
                current = self.secrets.get("matchbook_session_token")
                if adapter.session_token != current:
                    updates["matchbook_session_token"] = adapter.session_token
        if updates:
            self.secrets.set_many(updates)

    def _persist_snapshots(self, markets):
        rows = []
        for market in markets:
            for q in market.quotes:
                rows.append({
                    "captured_at": q.captured_at, "exchange": q.exchange, "event_id": q.event_id,
                    "event_name": q.event_name, "market_id": q.market_id, "market_name": q.market_name,
                    "selection_id": q.selection_id, "selection": q.selection, "side": "back", "odds": q.odds,
                    "liquidity": q.liquidity, "source_latency_ms": q.source_latency_ms,
                    "commission_pct": q.commission_pct, "commission_source": q.commission_source,
                    "market_type": q.market_type, "strategy": q.strategy, "sport": q.sport,
                    "in_play": None if q.in_play is None else int(bool(q.in_play)), "market_status": q.market_status,
                    "section": q.section, "trap_number": q.trap_number, "canonical_selection_key": q.canonical_selection_key,
                    "runner_status": q.runner_status, "provider_id": q.provider_id, "venue_id": q.venue_id,
                    "feed_entitlement": q.feed_entitlement, "market_data_transport": q.market_data_transport,
                    "source_timestamp": q.source_timestamp, "timestamp_quality": q.timestamp_quality, "quote_age_ms": q.quote_age_ms,
                    "source_state_version": q.source_state_version,
                    "depth_levels_json": json.dumps([x.as_dict() for x in q.depth_levels], separators=(",", ":")),
                    "raw_json": json.dumps(q.raw or {}, separators=(",", ":")),
                })
        return self.db.upsert_latest_snapshots(rows)

    def _candidate_legs(self, market_match, racing_threshold: float = 0.92) -> dict[str, list[Leg]]:
        groups = align_quotes(market_match, racing_threshold=racing_threshold)
        expected = (market_match.runner_count or len(market_match.markets[0].quotes)) if market_match.strategy == "multi_runner_win" else (3 if market_match.strategy == "1x2" else 2)
        if expected < 2 or len(groups) != expected:
            return {}
        candidates: dict[str, list[Leg]] = {}
        for label, quotes in groups.items():
            candidates[label] = [Leg(
                exchange=q.exchange, selection=label, odds=q.odds, liquidity=q.liquidity,
                commission_pct=q.commission_pct, commission_source=q.commission_source,
                event_id=q.event_id, market_id=q.market_id, selection_id=q.selection_id,
                captured_at=q.captured_at, source_latency_ms=q.source_latency_ms,
                market_type=q.market_type, strategy=q.strategy, sport=q.sport, in_play=q.in_play, market_status=q.market_status,
                section=q.section, trap_number=q.trap_number, canonical_selection_key=q.canonical_selection_key, runner_status=q.runner_status,
                venue_id=q.venue_id, provider_id=q.provider_id, underlying_venue_id=q.underlying_venue_id,
                canonical_event_id=q.canonical_event_id, canonical_market_id=q.canonical_market_id, canonical_selection_id=q.canonical_selection_id,
                currency=q.currency, side=q.side, executable_capacity=q.executable_capacity, fee_model=q.fee_model,
                displayed_odds=q.displayed_odds, executable_odds=q.executable_odds, capacity_source=q.capacity_source,
                feed_entitlement=q.feed_entitlement, market_data_transport=q.market_data_transport,
                source_timestamp=q.source_timestamp, timestamp_quality=q.timestamp_quality, quote_age_ms=q.quote_age_ms, source_state_version=q.source_state_version,
                depth_levels=q.depth_levels,
            ) for q in quotes]
        return candidates

    @staticmethod
    def _raw_matchbook_prices(raw_runner: dict | None) -> list[dict]:
        """Retain canonicalised Matchbook two-sided depth for Racing audit only.

        Raw source labels are preserved (``back/lay/win/lose``) while ``side`` is
        normalised to BACK/LAY. Discovery-time side probes are merged as diagnostic
        evidence only; they never replace the executable quote used by the engine.
        """
        if not isinstance(raw_runner, dict):
            return []
        groups = []
        raw_prices = raw_runner.get("raw_prices") or raw_runner.get("prices") or raw_runner.get("offers") or []
        if isinstance(raw_prices, dict):
            raw_prices = raw_prices.get("prices") or raw_prices.get("offers") or raw_prices.get("items") or []
        groups.append((raw_prices or [], "event_feed", None))
        groups.append((raw_runner.get("_arbscanner_side_probe_prices") or [], "side_probe", raw_runner.get("_arbscanner_side_probe_observed_at")))
        out, seen = [], set()
        for rows, default_source, observed_at in groups:
            for item in rows or []:
                if not isinstance(item, dict):
                    continue
                source_side = str(item.get("source_side") or item.get("side") or item.get("type") or "").strip().lower()
                side = MatchbookAdapter._canonical_price_side(item.get("side") or item.get("type") or item.get("source_side"))
                requested_side = MatchbookAdapter._canonical_price_side(item.get("requested_side"))
                if side is None and requested_side is not None:
                    side = requested_side
                odds = item.get("odds") if item.get("odds") is not None else item.get("price")
                amount = item.get("available-amount")
                if amount is None:
                    amount = item.get("available_amount", item.get("size", item.get("amount", 0)))
                try:
                    odds_f, amount_f = float(odds), float(amount)
                except (TypeError, ValueError):
                    continue
                if not side or odds_f <= 1.0 or amount_f < 0:
                    continue
                source = str(item.get("source") or default_source)
                observed = item.get("observed_at") or observed_at
                key = (side, round(odds_f, 8), round(amount_f, 8), source_side, source)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "side": side, "source_side": source_side or None,
                    "requested_side": requested_side, "source": source,
                    "observed_at": observed, "odds": odds_f,
                    "available_amount": amount_f,
                    "side_inferred_from_request": bool(item.get("side_inferred_from_request")),
                })
        return out

    async def _augment_racing_matchbook_side_evidence(self, matches: list[MarketMatch], adapters: list) -> dict:
        """Probe only missing Matchbook sides on already-matched Greyhound races."""
        adapter = next((a for a in adapters if isinstance(a, MatchbookAdapter)), None)
        if adapter is None:
            return {"requested_markets": 0, "requests": 0, "completed": 0, "failed": 0, "sides": {}}
        targets: dict[tuple[str, str], ExchangeMarket] = {}
        requests = []
        for mm in matches:
            if not (mm.section == "racing" or mm.strategy == "multi_runner_win" or normalize_sport(mm.sport) == "Greyhounds"):
                continue
            for market in mm.markets:
                if str(market.exchange) != "Matchbook":
                    continue
                per_runner_sides = [
                    {x.get("side") for x in self._raw_matchbook_prices(q.raw) if x.get("side")}
                    for q in market.quotes
                ]
                missing = [side for side in ("back", "lay") if not per_runner_sides or not all(side in sides for sides in per_runner_sides)]
                if missing:
                    key = (str(market.event_id or ""), str(market.market_id or ""))
                    targets[key] = market
                    requests.append({"event_id": key[0], "market_id": key[1], "missing_sides": missing})
        summary = {"requested_markets": len(requests), "requests": sum(len(x.get("missing_sides") or []) for x in requests),
                   "completed": 0, "failed": 0, "sides": {"back": 0, "lay": 0}}
        if not requests:
            return summary
        try:
            results = await adapter.probe_racing_price_sides(requests)
        except Exception as exc:
            summary["failed"] = summary["requests"]
            summary["error"] = str(exc)
            return summary
        for result in results:
            key = (str(result.get("event_id") or ""), str(result.get("market_id") or ""))
            market = targets.get(key)
            if market is None:
                continue
            side = MatchbookAdapter._canonical_price_side(result.get("side"))
            if not result.get("ok"):
                summary["failed"] += 1
                continue
            summary["completed"] += 1
            if side:
                summary["sides"][side] = int(summary["sides"].get(side) or 0) + 1
            by_runner = result.get("runners") or {}
            for q in market.quotes:
                rows = list(by_runner.get(str(q.selection_id or "")) or [])
                if not rows:
                    continue
                if not isinstance(q.raw, dict):
                    q.raw = {}
                existing = list(q.raw.get("_arbscanner_side_probe_prices") or [])
                existing.extend(rows)
                q.raw["_arbscanner_side_probe_prices"] = existing
                q.raw["_arbscanner_side_probe_observed_at"] = result.get("observed_at")
                q.raw["_arbscanner_side_probe_version"] = __version__
        return summary

    def _source_markets(self, market_match):
        out = []
        for m in market_match.markets:
            source = {
                "exchange": m.exchange, "event_id": m.event_id, "market_id": m.market_id,
                "event_name": m.event_name, "market_name": m.market_name, "market_type": m.market_type,
                "strategy": m.strategy, "sport": m.sport, "in_play": m.in_play, "status": m.status, "start_time": m.start_time,
                "runner_names": {q.selection_id: q.selection for q in m.quotes},
                "runner_traps": {q.selection_id: q.trap_number for q in m.quotes if q.trap_number is not None},
                "runner_keys": {q.selection_id: q.canonical_selection_key for q in m.quotes if q.canonical_selection_key},
                "runner_statuses": {q.selection_id: q.runner_status for q in m.quotes if q.runner_status},
                "section": m.section, "race_track": m.race_track, "race_number": m.race_number,
                "commission": sorted({(round(q.commission_pct, 6), q.commission_source) for q in m.quotes}),
            }
            # Racing qualification needs an auditable runner-by-runner price book. Keep
            # these exact scan prices with the source market so Overview can show
            # Betfair vs Matchbook books without querying or guessing later. This
            # is intentionally Racing-only to avoid growing the Sports cache.
            if m.section == "racing" or m.strategy == "multi_runner_win" or m.sport == "Greyhounds":
                source["runner_prices"] = [
                    {
                        "selection_id": q.selection_id, "selection": q.selection,
                        "trap_number": q.trap_number, "canonical_selection_key": q.canonical_selection_key,
                        "odds": q.odds, "liquidity": q.liquidity,
                        "commission_pct": q.commission_pct, "commission_source": q.commission_source,
                        "interpreted_source_side": "back" if q.exchange == "Matchbook" else "availableToBack" if q.exchange.lower().startswith("betfair") else None,
                        "raw_prices": self._raw_matchbook_prices(q.raw) if q.exchange == "Matchbook" else [],
                    }
                    for q in m.quotes
                ]
            out.append(source)
        return out

    @staticmethod
    def _signature(event_key: str, market_name: str, legs: list[Leg]) -> str:
        core = {"e": event_key, "m": market_name,
                "l": sorted((l.exchange, l.selection, round(l.odds, 4), round(l.liquidity, 2), round(l.commission_pct, 4)) for l in legs)}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:24]

    @staticmethod
    def _timing_evidence(legs: list[Leg]) -> dict:
        """Return auditable quote-age/skew evidence without inventing provider time.

        ``captured_at`` is a local receipt timestamp. ``source_timestamp`` is only
        compared when every selected leg explicitly declares PROVIDER_SOURCE.
        This keeps delayed/polled Racing evidence useful without presenting local
        receipt timing as exchange-originated precision.
        """
        now = datetime.now(timezone.utc)
        ages: list[int] = []
        receipts: list[datetime] = []
        sources: list[datetime] = []
        qualities: list[str] = []
        by_venue: list[dict] = []

        def parse_dt(value):
            if not value:
                return None
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                return None

        for leg in legs or []:
            receipt = parse_dt(getattr(leg, "captured_at", None))
            source = parse_dt(getattr(leg, "source_timestamp", None))
            quality = str(getattr(leg, "timestamp_quality", None) or ("PROVIDER_SOURCE" if source else "LOCAL_RECEIPT")).upper()
            if quality not in {"PROVIDER_SOURCE", "LOCAL_RECEIPT", "ESTIMATED", "UNKNOWN"}:
                quality = "UNKNOWN"
            qualities.append(quality)
            if receipt:
                receipts.append(receipt)
            if source and quality == "PROVIDER_SOURCE":
                sources.append(source)
            age = getattr(leg, "quote_age_ms", None)
            if age is None and receipt:
                age = max(0, int((now - receipt).total_seconds() * 1000))
            if age is not None:
                try:
                    age = max(0, int(age)); ages.append(age)
                except Exception:
                    age = None
            by_venue.append({
                "provider_id": str(getattr(leg, "resolved_provider_id", None) or getattr(leg, "provider_id", None) or getattr(leg, "exchange", "") or ""),
                "venue_id": str(getattr(leg, "resolved_venue_id", None) or getattr(leg, "venue_id", None) or getattr(leg, "exchange", "") or ""),
                "selection": str(getattr(leg, "selection", "") or ""),
                "quote_age_ms": age,
                "captured_at": getattr(leg, "captured_at", None),
                "source_timestamp": getattr(leg, "source_timestamp", None) if quality == "PROVIDER_SOURCE" else None,
                "timestamp_quality": quality,
                "feed_entitlement": str(getattr(leg, "feed_entitlement", "") or ""),
                "transport": str(getattr(leg, "market_data_transport", "") or ""),
            })

        receipt_spread = int((max(receipts) - min(receipts)).total_seconds() * 1000) if len(receipts) >= 2 else 0 if receipts else None
        all_provider_source = bool(qualities) and all(q == "PROVIDER_SOURCE" for q in qualities) and len(sources) == len(qualities)
        source_spread = int((max(sources) - min(sources)).total_seconds() * 1000) if all_provider_source and len(sources) >= 2 else 0 if all_provider_source and sources else None
        aggregate_quality = "UNKNOWN"
        if qualities:
            uq = set(qualities)
            aggregate_quality = next(iter(uq)) if len(uq) == 1 else "MIXED"
        return {
            "quote_oldest_age_ms": max(ages) if ages else None,
            "quote_newest_age_ms": min(ages) if ages else None,
            "quote_receipt_spread_ms": receipt_spread,
            "source_timestamp_spread_ms": source_spread,
            "timestamp_quality": aggregate_quality,
            "legs": by_venue,
        }

    def _application_data_mode(self) -> str:
        """Return the UI/data context shared with the background scanner.

        The economic execution setting remains SIM/locked. 0.9.8 persists the
        global data context separately so LIVE observation can never accidentally
        enable an execution provider or real-money pathway.
        """
        return canonical_mode_value(self.db.get_setting("data_context_mode", self.db.get_setting("mode", "sim")))

    @staticmethod
    def _live_reason_code(status: str, reason: str | None = None) -> str:
        status = str(status or "").lower()
        mapping = {
            "incomplete": "RUNNER_MAPPING_INCOMPLETE",
            "no_strategy_combo": "NO_STRATEGY_COMBO",
            "not_executable": "SIMULATION_CONSTRUCTION_FAILED",
            "below_liquidity": "INSUFFICIENT_LIQUIDITY",
            "single_exchange": "SINGLE_VENUE",
            "no_arb": "NO_ARB",
            "commission_removed": "COMMISSION_REMOVES_EDGE",
            "below_threshold": "BELOW_MIN_ROI",
            "below_profit_threshold": "BELOW_MIN_PROFIT",
            "below_quality": "BELOW_MIN_QUALITY",
            "racing_in_play_excluded": "EVENT_STARTED",
            "racing_identity_rejected": "MAPPING_UNCERTAIN",
            "racing_runner_field_incomplete": "RUNNER_MAPPING_INCOMPLETE",
            "racing_stale_quotes": "STALE_QUOTE",
            "racing_time_skew": "QUOTE_SKEW_TOO_HIGH",
            "recommended": "NONE",
        }
        return mapping.get(status, str(status or "OTHER").upper())

    def _build_live_decision_evidence(self, *, mm: MarketMatch, status: str, reason: str,
                                      selected_legs: list[Leg], diagnostic_legs: list[Leg], profile: dict | None,
                                      timing_evidence: dict, cfg: dict, theoretical, net_roi, max_executable_stake,
                                      limiting_provider, limiting_selection, limiting_side, liquidity_capable,
                                      reference_bankroll: float, reference_cap_pct: float,
                                      max_event_exposure_pct: float, decision_started: float,
                                      book_revision: str | None) -> dict:
        """Build provider-neutral LIVE-context evidence using existing economics.

        This function never touches wallets/accounts/orders and requires no
        ExecutionProvider. It classifies the exact same canonical decision that
        normal scanning has already produced, then optionally runs the existing
        pure simulation model for engineering evidence.
        """
        legs = list(selected_legs or diagnostic_legs or [])
        revision = str(book_revision or (self._signature(mm.event_key, mm.display_market, legs) if legs else hashlib.sha256(
            f"{mm.event_key}|{mm.display_market}|{mm.strategy}|{status}".encode()).hexdigest()[:24]))
        providers = sorted({str(l.resolved_provider_id or "unknown").lower() for l in legs})
        provider_pair = " + ".join(providers) if providers else "unknown"
        max_age_seconds = max(0.0, float(cfg.get("live_decision_max_quote_age_seconds", cfg.get("price_quote_max_age_seconds", 10.0)) or 0.0))
        max_age_ms = int(max_age_seconds * 1000)
        max_skew_ms = max(0, int(float(cfg.get("live_decision_max_receipt_spread_ms", 1500) or 0)))
        oldest = timing_evidence.get("quote_oldest_age_ms")
        receipt_spread = timing_evidence.get("quote_receipt_spread_ms")
        timing_legs = timing_evidence.get("legs") or []
        ages_known = bool(timing_legs) and all(x.get("quote_age_ms") is not None for x in timing_legs)
        freshness_pass = bool(ages_known and (max_age_ms <= 0 or (oldest is not None and int(oldest) <= max_age_ms)))
        timing_pass = bool(receipt_spread is not None and (max_skew_ms <= 0 or int(receipt_spread) <= max_skew_ms))
        feed_live = bool(legs) and all(str(getattr(l, "feed_entitlement", "unknown") or "unknown").lower() == "live" for l in legs)
        base_mapping_threshold = float(cfg.get("live_decision_min_mapping_confidence", cfg.get("event_match_threshold", 0.72)) or 0.72)
        mapping_threshold = max(base_mapping_threshold, float(cfg.get("racing_match_threshold", 0.90) or 0.90)) if mm.section == "racing" else base_mapping_threshold
        mapping_pass = float(mm.match_score or 0.0) + 1e-12 >= mapping_threshold
        def market_open(leg: Leg) -> bool:
            state = str(getattr(leg, "market_status", "") or mm.status or "").strip().upper()
            return bool(state and state not in {"SUSPENDED", "CLOSED", "VOID", "UNKNOWN"})
        market_status_pass = bool(legs) and all(market_open(l) for l in legs)
        economics_pass = bool(theoretical is not None and float(theoretical) > 0 and net_roi is not None and float(net_roi) > 0)
        liquidity_pass = bool(liquidity_capable and selected_legs)
        strategy_risk_pass = str(status or "").lower() == "recommended"
        quality_checks = {
            "feed_quality_pass": feed_live,
            "freshness_pass": freshness_pass,
            "timing_pass": timing_pass,
            "mapping_pass": mapping_pass,
            "market_status_pass": market_status_pass,
            "liquidity_pass": liquidity_pass,
            "economics_pass": economics_pass,
            "strategy_risk_pass": strategy_risk_pass,
        }
        execution_grade = all(quality_checks.values())
        evidence_quality = "EXECUTION_GRADE" if execution_grade else "OBSERVATIONAL"
        reason_code = self._live_reason_code(status, reason)
        if strategy_risk_pass and not execution_grade:
            entitlements = {str(getattr(l, "feed_entitlement", "unknown") or "unknown").lower() for l in legs}
            if "delayed" in entitlements:
                reason_code = "DELAYED_DATA"
            elif not freshness_pass:
                reason_code = "STALE_QUOTE"
            elif not timing_pass:
                reason_code = "QUOTE_SKEW_TOO_HIGH"
            elif not mapping_pass:
                reason_code = "MAPPING_UNCERTAIN"
            elif not market_status_pass:
                reason_code = "MARKET_SUSPENDED"
            elif not liquidity_pass:
                reason_code = "INSUFFICIENT_LIQUIDITY"
        simulation = None
        state = "NO_ARB" if str(status).lower() == "no_arb" else "REJECTED"
        requested_stake = min(
            float(reference_bankroll),
            float(reference_bankroll) * min(max(0.0, float(reference_cap_pct)), max(0.0, float(max_event_exposure_pct))) / 100.0,
        )
        if strategy_risk_pass and selected_legs:
            try:
                simulation = simulate_equal_return(
                    selected_legs,
                    Scenario("live-data-sim", reference_bankroll, reference_cap_pct, max_event_exposure_pct),
                )
                if simulation.get("executable"):
                    state = "SIM_FULL_FILL"
                else:
                    state = "SIM_MISS"
                    if reason_code == "NONE":
                        reason_code = "SIMULATION_CONSTRUCTION_FAILED"
            except Exception as exc:
                simulation = {"executable": False, "reason": str(exc)}
                state = "SIM_MISS"
                reason_code = "SIMULATION_CONSTRUCTION_FAILED"
        elif str(status).lower() in {"incomplete", "no_strategy_combo"}:
            state = "OBSERVED"
        simulated_stake = float((simulation or {}).get("deployed") or 0.0) if simulation else None
        simulated_filled_stake = simulated_stake if simulation and simulation.get("executable") else 0.0 if simulation else None
        expected_profit = float((simulation or {}).get("expected_profit") or 0.0) if simulation else 0.0
        if reason_code == "NONE" and evidence_quality == "EXECUTION_GRADE":
            decision_reason = "All feed-quality, freshness, timing, mapping, liquidity and strategy/risk gates passed."
        elif reason_code == "NONE":
            decision_reason = str(reason or "Observed provider-derived decision state")
        else:
            decision_reason = str(reason or reason_code.replace("_", " ").title())
        completed_ms = (time.perf_counter() - decision_started) * 1000.0
        state_key = hashlib.sha256(f"{mm.canonical_market_id or mm.event_key}|{mm.strategy}|{mm.section}".encode()).hexdigest()[:32]
        decision_id = hashlib.sha256(f"live|{mm.canonical_market_id or mm.event_key}|{mm.strategy}|{revision}".encode()).hexdigest()[:32]
        return {
            "decision_id": decision_id,
            "state_key": state_key,
            "canonical_event_id": mm.canonical_event_id,
            "canonical_market_id": mm.canonical_market_id or f"legacy:{hashlib.sha256((mm.event_key+'|'+mm.display_market).encode()).hexdigest()[:20]}",
            "book_revision": revision,
            "strategy": mm.strategy,
            "domain": "racing" if mm.section == "racing" else "sports",
            "section": mm.section,
            "sport": mm.sport,
            "market_type": mm.market_type or mm.display_market,
            "event_name": mm.display_event,
            "market_name": mm.display_market,
            "in_play": bool(mm.in_play),
            "application_mode": "live",
            "decision_type": "simulated",
            "state": state,
            "evidence_quality": evidence_quality,
            "reason_code": reason_code,
            "reason": decision_reason,
            "gross_edge_pct": theoretical,
            "net_roi_pct": net_roi,
            "expected_simulated_profit": expected_profit,
            "requested_stake": requested_stake,
            "max_executable_stake": max_executable_stake,
            "simulated_stake": simulated_stake,
            "simulated_filled_stake": simulated_filled_stake,
            "oldest_quote_age_ms": oldest,
            "receipt_spread_ms": receipt_spread,
            "source_time_spread_ms": timing_evidence.get("source_timestamp_spread_ms"),
            "decision_compute_ms": round(completed_ms, 4),
            "provider_pair": provider_pair,
            "limiting_provider": limiting_provider,
            "limiting_selection": limiting_selection,
            "limiting_side": limiting_side,
            "qualification": quality_checks,
            "legs": [asdict(l) for l in legs],
            "simulation": simulation,
            "material": bool(state != "NO_ARB" or (theoretical is not None and float(theoretical) > 0)),
        }

    @staticmethod
    def _track_key(event_key: str, market_name: str, strategy: str, sport: str = "Unknown") -> str:
        return hashlib.sha256(f"{sport}|{event_key}|{market_name}|{strategy}".encode()).hexdigest()[:24]

    def _maybe_alert(self, track_key: str, event_name: str, market_name: str, profile: dict, uses_delayed: bool, cfg: dict):
        qualifies, _ = qualifies_for_alert(profile, cfg)
        if not qualifies:
            return
        band = str(profile.get("quality_band") or "")
        if self.db.alert_was_sent(track_key, band):
            return
        if not cfg.get("alert_quality_improvements", True) and self.db.any_alert_sent(track_key):
            return
        # A failed desktop-notification command used to be recorded as though it
        # had succeeded, permanently suppressing retries for that opportunity.
        # Retry failures on a conservative cadence instead.
        if self.db.recent_failed_alert_attempt(track_key, band, within_minutes=int(cfg.get("alert_retry_minutes", 15) or 15)):
            return
        title = f"{band} Monitor opportunity"
        delayed = " · Betfair delayed data" if uses_delayed else ""
        message = (
            f"{event_name} · {market_name}\n"
            f"Paper profit £{float(profile.get('expected_profit') or 0):.2f}; "
            f"bankroll ROI {float(profile.get('bankroll_roi_pct') or 0):.3f}%; "
            f"{float(profile.get('capital_used_pct') or 0):.1f}% capital usable{delayed}"
        )
        result = send_macos_notification_diagnostic(title, message, sound=bool(cfg.get("alert_sound", True)))
        score = float(profile.get("quality_score") or 0.0)
        self.db.record_alert_attempt(track_key, band, score, bool(result.get("ok")), str(result.get("message") or ""))
        if result.get("ok"):
            self.db.record_alert(track_key, band, score)
        return bool(result.get("ok"))

    @staticmethod
    def _cache_key_for_match(mm: MarketMatch) -> str:
        source_ids = sorted((str(m.exchange), str(m.event_id), str(m.market_id)) for m in mm.markets)
        raw = json.dumps([mm.sport, mm.event_key, mm.market_key, source_ids], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _refresh_interval_for(event_start: str | None, cfg: dict, in_play: bool | None = None) -> int:
        if in_play is True:
            return max(1, int(cfg.get("price_refresh_inplay_seconds", 2) or 2))
        high = max(2, int(cfg.get("price_refresh_near_seconds", 3) or 3))
        normal = max(high, int(cfg.get("price_refresh_today_seconds", 8) or 8))
        low = max(normal, int(cfg.get("price_refresh_later_seconds", 30) or 30))
        if not event_start:
            return normal
        try:
            dt = datetime.fromisoformat(str(event_start).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hours = (dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 3600.0
            if hours <= 2.0:
                return high
            if hours <= 24.0:
                return normal
            return low
        except Exception:
            return normal

    def _racing_discovery_diagnostics(self, markets: list[ExchangeMarket], matches: list[MarketMatch], cfg: dict, statuses: list[dict] | None = None) -> dict:
        """Describe Greyhound discovery plus why cross-exchange races do or do not pair.

        v0.8.27 keeps Racing LIVE locked, but makes the matching boundary explainable.
        Raw Betfair catalogue rows remain visible even when they fail full-field
        pricing, while fully normalised Betfair/Matchbook markets receive a best
        counterpart diagnostic. Strict matching is conservative: race identity and the complete runner field must align.
        """
        racing_markets: list[ExchangeMarket] = []
        for market in markets:
            canonical, inferred_strategy = classify_market(market.market_name, len(market.quotes), market.sport)
            strategy = str(market.strategy or inferred_strategy)
            canonical_type = str(market.market_type or canonical).lower()
            if normalize_sport(market.sport) != "Greyhounds":
                continue
            # Adapter validation is authoritative for Racing WIN. Display names
            # are not guaranteed to be stable enough to re-classify later.
            if strategy != "multi_runner_win" and canonical_type != "win":
                continue
            market.market_type = "win"
            market.strategy = "multi_runner_win"
            market.section = "racing"
            market.race_track = market.race_track or normalize_track(market.event_name)
            racing_markets.append(market)

        matched_sources: dict[tuple[str, str], MarketMatch] = {}
        for mm in matches:
            if mm.section != "racing":
                continue
            for src in mm.markets:
                matched_sources[(str(src.exchange), str(src.market_id))] = mm

        by_exchange: dict[str, list[ExchangeMarket]] = {}
        for market in racing_markets:
            by_exchange.setdefault(str(market.exchange), []).append(market)

        def market_meta(market: ExchangeMarket) -> dict:
            raw = market.raw if isinstance(market.raw, dict) else {}
            catalogue = raw.get("catalogue") if isinstance(raw.get("catalogue"), dict) else {}
            raw_event = catalogue.get("event") if isinstance(catalogue.get("event"), dict) else {}
            country = (
                raw.get("_arbscanner_event_country") or raw.get("country") or raw.get("countryCode")
                or raw.get("country_code") or raw_event.get("countryCode") or raw_event.get("country")
            )
            source_start_raw = raw.get("_arbscanner_source_start_raw")
            if source_start_raw is None and catalogue:
                source_start_raw = catalogue.get("marketStartTime") or raw_event.get("openDate")
            priced = raw.get("_arbscanner_priced_runner_count")
            field = raw.get("_arbscanner_catalogue_runner_count")
            return {
                "country": str(country).upper() if country else None,
                "source_start_raw": source_start_raw if source_start_raw is not None else market.start_time,
                "source_time_naive": bool(raw.get("_arbscanner_source_time_naive", source_time_is_naive(source_start_raw))),
                "priced_runner_count": int(priced) if priced is not None else len(market.quotes),
                "catalogue_runner_count": int(field) if field is not None else len(market.quotes),
            }


        match_threshold = float(cfg.get("racing_match_threshold", 0.90) or 0.90)
        runner_threshold = float(cfg.get("racing_runner_match_threshold", 0.92) or 0.92)

        def candidate_detail(left: ExchangeMarket, right: ExchangeMarket) -> dict:
            identity = racing_pair_identity(
                left, right, runner_threshold=runner_threshold,
                event_threshold=match_threshold,
            )
            left_meta, right_meta = market_meta(left), market_meta(right)
            delta_min = identity.get("time_delta_minutes")
            time_format_suspect = bool(
                delta_min is not None and 20.0 < float(delta_min) <= 90.0
                and (left_meta.get("source_time_naive") or right_meta.get("source_time_naive"))
                and float(identity.get("track_score") or 0.0) >= 0.88
            )
            pair_key = "|".join(sorted((f"{left.exchange}:{left.market_id}", f"{right.exchange}:{right.market_id}")))
            checks = {
                "track": "PASS" if identity.get("track_compatible") else "FAIL",
                "time": "UNKNOWN" if delta_min is None else "PASS" if identity.get("time_compatible") else "FAIL",
                "country": "UNKNOWN" if not (identity.get("source_country") and identity.get("candidate_country")) else "PASS" if identity.get("country_compatible") else "FAIL",
                "race_number": "UNKNOWN" if identity.get("source_race_number") is None or identity.get("candidate_race_number") is None else "PASS" if identity.get("race_number_compatible") else "FAIL",
                "field": "PASS" if identity.get("field_compatible") else "FAIL",
                "runners": "PASS" if identity.get("runner_aligned") else "FAIL",
                "event_confidence": "PASS" if float(identity.get("event_score") or 0.0) >= match_threshold else "FAIL",
                "strict": "PASS" if identity.get("strict_match") else "FAIL",
            }
            def audit_market(market: ExchangeMarket, meta: dict) -> dict:
                return {
                    "exchange": market.exchange,
                    "event_id": market.event_id,
                    "market_id": market.market_id,
                    "raw_track": market.race_track or market.event_name,
                    "canonical_track": normalize_track(market.race_track or market.event_name),
                    "source_start_raw": meta.get("source_start_raw"),
                    "event_start_utc": market.start_time,
                    "country": meta.get("country"),
                    "race_number": market.race_number,
                    "runner_count": len(market.quotes),
                    "priced_runner_count": int(meta.get("priced_runner_count") or len(market.quotes)),
                    "catalogue_runner_count": int(meta.get("catalogue_runner_count") or len(market.quotes)),
                }
            return {
                "exchange": right.exchange,
                "event_name": right.event_name,
                "market_id": right.market_id,
                "event_id": right.event_id,
                "event_start": right.start_time,
                "event_start_utc": right.start_time,
                "source_start_raw": right_meta.get("source_start_raw"),
                "source_time_naive": bool(right_meta.get("source_time_naive")),
                "country": right_meta.get("country"),
                "race_track": normalize_track(right.race_track or right.event_name),
                "race_number": right.race_number,
                "runner_count": len(right.quotes),
                "priced_runner_count": int(right_meta.get("priced_runner_count") or len(right.quotes)),
                "catalogue_runner_count": int(right_meta.get("catalogue_runner_count") or len(right.quotes)),
                "track_score": round(float(identity.get("track_score") or 0.0), 4),
                "time_delta_minutes": None if delta_min is None else round(float(delta_min), 3),
                "event_score": round(float(identity.get("event_score") or 0.0), 4),
                "runner_match_count": int(identity.get("runner_match_count") or 0),
                "runner_expected": int(identity.get("runner_expected") or len(left.quotes)),
                "runner_score": round(float(identity.get("runner_score") or 0.0), 4),
                "runner_min_score": round(float(identity.get("runner_min_score") or 0.0), 4),
                "country_compatible": bool(identity.get("country_compatible")),
                "race_compatible": bool(identity.get("race_number_compatible")),
                "field_compatible": bool(identity.get("field_compatible")),
                "runner_aligned": bool(identity.get("runner_aligned")),
                "time_format_suspect": time_format_suspect,
                "identity_likely": bool(identity.get("event_identity")),
                "strict_eligible": bool(identity.get("strict_match")),
                "pair_key": pair_key,
                "checks": checks,
                "audit": {
                    "source": audit_market(left, left_meta),
                    "candidate": audit_market(right, right_meta),
                    "checks": checks,
                },
            }

        rows = []
        for market in racing_markets:
            key = (str(market.exchange), str(market.market_id))
            matched = matched_sources.get(key)
            status = "matched" if matched else "unmatched"
            reason = "Cross-exchange race and runner field matched" if matched else "No suitable counterpart found"
            counterpart = None
            counterparts = []
            score = float(matched.match_score) if matched else None
            candidate_pair_key = None
            best_pair_key = None

            if matched:
                others = [x for x in matched.markets if not (str(x.exchange) == str(market.exchange) and str(x.market_id) == str(market.market_id))]
                for other in sorted(others, key=lambda x: (str(x.provider_id or x.exchange), str(x.market_id))):
                    detail = candidate_detail(market, other)
                    detail["identity_likely"] = True
                    counterparts.append(detail)
                counterpart = counterparts[0] if counterparts else None
                if counterpart is not None:
                    candidate_pair_key = counterpart.get("pair_key")
                    best_pair_key = counterpart.get("pair_key")
            else:
                ranked = []
                for exchange, others in by_exchange.items():
                    if exchange == str(market.exchange):
                        continue
                    for other in others:
                        detail = candidate_detail(market, other)
                        track_score = float(detail.get("track_score") or 0.0)
                        delta = detail.get("time_delta_minutes")
                        same_race = market.race_number is not None and other.race_number is not None and int(market.race_number) == int(other.race_number)
                        # Only surface a diagnostic counterpart when venue identity is
                        # at least plausible. The wider 20-minute window deliberately
                        # exposes near misses without making them matchable.
                        if track_score < 0.72:
                            continue
                        if delta is not None and float(delta) > 20.0 and not same_race and not detail.get("time_format_suspect"):
                            continue
                        time_component = 0.0 if delta is None else max(0.0, 1.0 - float(delta) / 20.0)
                        field_component = 1.0 if len(market.quotes) == len(other.quotes) else max(0.0, 1.0 - abs(len(market.quotes)-len(other.quotes))/12.0)
                        rank = 0.62 * track_score + 0.23 * time_component + 0.10 * field_component + (0.05 if same_race else 0.0)
                        ranked.append((rank, other, detail))
                if ranked:
                    _, other, detail = max(ranked, key=lambda x: x[0])
                    counterpart = detail
                    best_pair_key = detail.get("pair_key")
                    score = float(detail.get("event_score") or 0.0)
                    candidate_pair_key = detail.get("pair_key") if detail.get("identity_likely") else None
                    if detail.get("identity_likely"):
                        status = "candidate"
                        if detail.get("field_compatible") is False:
                            reason = f"Race identity matched; field mismatch {len(market.quotes)} vs {len(other.quotes)} runners"
                        elif detail.get("runner_aligned") is False:
                            reason = f"Race identity matched; runner alignment {int(detail.get('runner_match_count') or 0)}/{len(market.quotes)} below {runner_threshold:.2f}"
                        elif score < match_threshold:
                            reason = f"Race identity matched; confidence {score:.3f} below {match_threshold:.3f}"
                        elif detail.get("strict_eligible"):
                            reason = "Strict matcher did not pair a strict-eligible pair found by the shared identity audit"
                        else:
                            reason = "Race identity matched but strict matching gates were not all satisfied"
                    else:
                        delta = detail.get("time_delta_minutes")
                        track_score = float(detail.get("track_score") or 0.0)
                        if track_score < 0.88:
                            reason = f"Nearest opposite-exchange track similarity {track_score:.3f} below 0.880"
                        elif detail.get("country_compatible") is False:
                            reason = f"Same track/time candidate but country differs {market_meta(market).get('country') or '?'} vs {detail.get('country') or '?'}"
                        elif detail.get("time_format_suspect"):
                            reason = f"Possible source-time timezone mismatch: canonical off-times differ by {float(delta):.1f} min"
                        elif delta is not None and float(delta) > 5.0:
                            reason = f"Same/similar track found but off-time differs by {float(delta):.1f} min"
                        elif market.race_number is not None and other.race_number is not None and int(market.race_number) != int(other.race_number):
                            reason = f"Same/similar track found but race number differs R{market.race_number} vs R{other.race_number}"
                        else:
                            reason = "No counterpart passed strict race identity checks"

            meta = market_meta(market)
            rows.append({
                "exchange": market.exchange,
                "provider_id": market.provider_id,
                "venue_id": market.venue_id,
                "event_id": market.event_id,
                "market_id": market.market_id,
                "event_name": market.event_name,
                "market_name": market.market_name,
                "event_start": market.start_time,
                "event_start_utc": market.start_time,
                "source_start_raw": meta.get("source_start_raw"),
                "source_time_naive": bool(meta.get("source_time_naive")),
                "race_track": normalize_track(market.race_track or market.event_name),
                "race_number": market.race_number,
                "runner_count": len(market.quotes),
                "priced_runner_count": int(meta.get("priced_runner_count") or len(market.quotes)),
                "catalogue_runner_count": int(meta.get("catalogue_runner_count") or len(market.quotes)),
                "in_play": market.in_play,
                "market_status": market.status,
                "country": meta.get("country"),
                "match_status": status,
                "match_score": score,
                "reason": reason,
                "counterpart": counterpart,  # compatibility: first matched/diagnostic counterpart
                "counterparts": counterparts if matched else ([counterpart] if counterpart else []),
                "candidate_pair_key": candidate_pair_key,
                "best_pair_key": best_pair_key,
                "feed_quality": (
                    "complete" if int(meta.get("catalogue_runner_count") or len(market.quotes)) >= 2
                    and int(meta.get("priced_runner_count") or len(market.quotes)) >= int(meta.get("catalogue_runner_count") or len(market.quotes))
                    else "partial" if int(meta.get("priced_runner_count") or 0) > 0 else "missing"
                ),
                "matched_event_key": matched.event_key if matched else None,
            })

        # Betfair's executable adapter intentionally requires a complete priced
        # field. For Monitor observability, merge catalogue-level races that were
        # rejected before ExchangeMarket construction. Never pass these rows back
        # into the matching engine.
        betfair_feed = {}
        for status_row in statuses or []:
            if str(status_row.get("exchange") or "").startswith("Betfair"):
                candidate = status_row.get("racing_discovery")
                if isinstance(candidate, dict):
                    betfair_feed = candidate
                    break
        existing = {(str(x.get("exchange")), str(x.get("market_id"))): x for x in rows}
        for raw_row in betfair_feed.get("rows") or []:
            key = (str(raw_row.get("exchange") or "Betfair delayed"), str(raw_row.get("market_id") or ""))
            current = existing.get(key)
            if current is not None:
                for field in ("book_returned", "priced_runner_count", "missing_price_count", "catalogue_runner_count", "source_start_raw", "source_time_naive", "event_start_utc"):
                    if field in raw_row:
                        current[field] = raw_row.get(field)
                if not current.get("country") and raw_row.get("country"):
                    current["country"] = raw_row.get("country")
                current["discovery_quality"] = raw_row.get("quality_band")
                continue
            item = dict(raw_row)
            item.setdefault("exchange", "Betfair delayed")
            item.setdefault("provider_id", "betfair")
            item.setdefault("venue_id", "betfair")
            item.setdefault("event_start_utc", item.get("event_start"))
            item.setdefault("source_start_raw", item.get("event_start"))
            item.setdefault("source_time_naive", source_time_is_naive(item.get("source_start_raw")))
            item.setdefault("match_status", "rejected")
            item.setdefault("match_score", None)
            item.setdefault("counterpart", None)
            item.setdefault("candidate_pair_key", None)
            item.setdefault("best_pair_key", None)
            item.setdefault("matched_event_key", None)
            item["discovery_quality"] = item.get("quality_band")
            priced = int(item.get("priced_runner_count") or 0)
            field = int(item.get("catalogue_runner_count") or item.get("runner_count") or 0)
            item["feed_quality"] = "complete" if field >= 2 and priced >= field else "partial" if priced > 0 else "missing"
            rows.append(item)
            existing[key] = item

        exchanges = sorted({str(x.get("exchange") or "") for x in rows if x.get("exchange")})
        matched_event_keys = {str(x.get("matched_event_key")) for x in rows if x.get("matched_event_key")}
        candidate_pair_keys = {str(x.get("candidate_pair_key")) for x in rows if x.get("candidate_pair_key") and x.get("match_status") == "candidate"}
        best_pair_keys = {str(x.get("best_pair_key")) for x in rows if x.get("best_pair_key")}
        event_pair_keys = {
            str(x.get("counterpart", {}).get("pair_key")) for x in rows
            if isinstance(x.get("counterpart"), dict) and x.get("counterpart", {}).get("pair_key")
            and x.get("counterpart", {}).get("identity_likely")
        }
        runner_aligned_keys = {
            str(x.get("counterpart", {}).get("pair_key")) for x in rows
            if isinstance(x.get("counterpart"), dict) and x.get("counterpart", {}).get("pair_key")
            and x.get("counterpart", {}).get("identity_likely") and x.get("counterpart", {}).get("runner_aligned")
        }
        summary = {
            "total": len(rows),
            "matched": len(matched_event_keys),
            "matched_sources": sum(1 for x in rows if x.get("match_status") == "matched"),
            "candidates": len(candidate_pair_keys),
            "race_candidates": len(best_pair_keys),
            "event_pairs": len(event_pair_keys),
            "runner_aligned": len(runner_aligned_keys),
            "candidate_sources": sum(1 for x in rows if x.get("match_status") == "candidate"),
            "unmatched": sum(1 for x in rows if x.get("match_status") == "unmatched"),
            "rejected": sum(1 for x in rows if x.get("match_status") == "rejected"),
            "time_format_suspects": sum(1 for x in rows if isinstance(x.get("counterpart"), dict) and x.get("counterpart", {}).get("time_format_suspect")),
            "problems": sum(1 for x in rows if x.get("match_status") != "matched"),
            "by_exchange": {ex: sum(1 for x in rows if x.get("exchange") == ex) for ex in exchanges},
            "betfair_feed": {
                "event_type_visible": betfair_feed.get("event_type_visible"),
                "event_type_name": betfair_feed.get("event_type_name"),
                "catalogue": int(betfair_feed.get("catalogue") or 0),
                "books_returned": int(betfair_feed.get("books_returned") or 0),
                "fully_priced": int(betfair_feed.get("fully_priced") or 0),
                "incomplete_prices": int(betfair_feed.get("incomplete_prices") or 0),
                "missing_books": int(betfair_feed.get("missing_books") or 0),
                "in_play_excluded": int(betfair_feed.get("in_play_excluded") or 0),
                "normalised": int(betfair_feed.get("normalised") or 0),
            },
        }
        return {
            "observed_at": utc_now_iso(),
            "producer": {"component": self.producer, "version": __version__},
            "rows": rows,
            "summary": summary,
        }

    def _cache_rows_from_matches(self, matches: list[MarketMatch], cfg: dict) -> list[dict]:
        rows = []
        for mm in matches:
            rows.append({
                "cache_key": self._cache_key_for_match(mm),
                "event_key": mm.event_key,
                "event_name": mm.display_event,
                "event_start": mm.start_time,
                "market_name": mm.display_market,
                "market_type": mm.market_type,
                "strategy": mm.strategy,
                "sport": mm.sport,
                "section": mm.section,
                "race_track": mm.race_track,
                "race_number": mm.race_number,
                "runner_count": mm.runner_count or (len(mm.markets[0].quotes) if mm.markets else None),
                "match_score": mm.match_score,
                "source_markets": self._source_markets(mm),
                "refresh_interval_seconds": self._refresh_interval_for(mm.start_time, cfg, mm.in_play),
            })
        return rows

    def _exchange_discovery_rows(self, markets: list[ExchangeMarket], matches: list[MarketMatch], statuses: list[dict]) -> list[dict]:
        """Build provider-native discovery evidence before cross-exchange narrowing."""
        canonical_by_source: dict[tuple[str,str], str] = {}
        for mm in matches:
            canonical = f"{str(mm.event_key or '').lower()}|{str(mm.display_market or '').lower()}"
            for src in mm.markets:
                canonical_by_source[(self.db._exchange_key(src.exchange), str(src.market_id))] = canonical
        rows: dict[tuple[str,str,str],dict] = {}
        def add(row: dict, *, quality: str):
            exchange=str(row.get('exchange') or '')
            market_id=str(row.get('market_id') or '')
            if not exchange or not market_id: return
            keyex=self.db._exchange_key(exchange)
            phase='in_play' if row.get('in_play') is True else 'pre_match'
            key=(keyex,market_id,phase)
            sport=str(row.get('sport') or ('Greyhounds' if str(row.get('section') or '')=='racing' else 'Unknown'))
            section=str(row.get('section') or ('racing' if sport=='Greyhounds' else 'sports'))
            item={
                'exchange':exchange,'market_id':market_id,'event_id':row.get('event_id'),'event_name':row.get('event_name'),
                'market_name':row.get('market_name'),'event_start':row.get('event_start') or row.get('start_time'),
                'sport':sport,'section':section,'in_play':row.get('in_play'),'race_track':row.get('race_track'),
                'race_number':row.get('race_number'),'source_quality':quality,
                'canonical_market_key':canonical_by_source.get((keyex,market_id)),
            }
            if key not in rows or quality=='catalogue': rows[key]=item
        for m in markets:
            add({
                'exchange':m.exchange,'market_id':m.market_id,'event_id':m.event_id,'event_name':m.event_name,
                'market_name':m.market_name,'event_start':m.start_time,'sport':m.sport,'section':m.section,
                'in_play':m.in_play,'race_track':m.race_track,'race_number':m.race_number,
            },quality='normalised')
        # Betfair's racing diagnostics retain catalogue markets that did not have a
        # complete executable book and therefore never became ExchangeMarket rows.
        for status in statuses or []:
            if not isinstance(status,dict): continue
            label=str(status.get('exchange') or '')
            rd=status.get('racing_discovery') or {}
            for raw in (rd.get('rows') or []) if isinstance(rd,dict) else []:
                if not isinstance(raw,dict): continue
                add({**raw,'exchange':raw.get('exchange') or label,'sport':'Greyhounds','section':'racing'},quality='catalogue')
        return list(rows.values())

    async def discover_once_async(self, job_id: int | None = None, *, data_context_mode: str | None = None) -> dict:
        """Slow market-universe discovery and cross-exchange mapping refresh."""
        cfg = self._settings()
        scan_id = self.db.start_scan(job_id=job_id, scan_kind="discovery")
        started = time.perf_counter()
        stage = {}
        statuses: list[dict] = []
        scan_mode = canonical_mode_value(data_context_mode or self._application_data_mode())
        adapters = self._adapters(scan_mode)
        if len(adapters) < 2:
            controls = {x["provider_id"]: x for x in self.db.venue_controls()}
            flag = "live_feed_enabled" if scan_mode == "live" else "sim_feed_enabled"
            disabled = [spec.venue.venue_name for spec in self.provider_runtime.providers.all()
                        if not bool((controls.get(spec.provider_id) or {}).get(flag, False))]
            msg = (f"{scan_mode.upper()} feed disabled by operator: " + ", ".join(disabled)) if disabled else f"At least two enabled {scan_mode.upper()} market-data providers are required for arbitrage matching"
            statuses = [{"exchange": name, "ok": False, "enabled": False, "state": "disabled", "message": f"{scan_mode.upper()} feed disabled by operator"} for name in disabled]
            self.db.finish_scan(scan_id, statuses=statuses, error=msg, duration_ms=int((time.perf_counter() - started) * 1000))
            return {"ok": False, "message": msg, "statuses": statuses, "kind": "discovery"}
        try:
            t = time.perf_counter()
            markets, statuses = await fetch_all(adapters, int(cfg.get("horizon_hours", 24)), 0.0)
            stage["exchange_fetch_ms"] = int((time.perf_counter() - t) * 1000)
            self._persist_refreshed_sessions(adapters)

            # Discovery builds/repairs the mapping cache; price snapshots are persisted by the fast price scanner.
            t = time.perf_counter()
            matches = match_markets(
                markets,
                threshold=float(cfg.get("event_match_threshold", 0.72)),
                racing_threshold=float(cfg.get("racing_match_threshold", 0.90)),
                racing_runner_threshold=float(cfg.get("racing_runner_match_threshold", 0.92)),
            )
            stage["market_match_ms"] = int((time.perf_counter() - t) * 1000)

            # v0.8.27: normalise Matchbook win/lose aliases and, only for already
            # matched Greyhound races, batch-probe any side absent from the normal
            # discovery payload. This evidence is diagnostic-only.
            t = time.perf_counter()
            side_probe = await self._augment_racing_matchbook_side_evidence(matches, adapters)
            stage["racing_side_probe_ms"] = int((time.perf_counter() - t) * 1000)
            stage["racing_side_probe"] = side_probe

            racing_diagnostics = self._racing_discovery_diagnostics(markets, matches, cfg, statuses=statuses)
            self.db.set_setting("racing_discovery_latest", racing_diagnostics)
            stage["racing_discovery"] = racing_diagnostics.get("summary") or {}
            discovery_rows = self._exchange_discovery_rows(markets, matches, statuses)
            stage["exchange_discovery_rows"] = self.db.record_exchange_market_discoveries(discovery_rows, utc_now_iso())
            # Shared Matchbook-adapter raw-side evidence is persisted as a compact
            # scan diagnostic. It never changes quote selection; it lets Racing
            # and Sports implications be assessed from stored scan data.
            mb_side_audit = next((x.get("price_side_audit") for x in statuses if x.get("exchange") == "Matchbook" and isinstance(x.get("price_side_audit"), dict)), None)
            if mb_side_audit is not None:
                side_payload = {**mb_side_audit, "observed_at": utc_now_iso(), "producer": {"component": self.producer, "version": __version__},
                                "racing_side_probe": side_probe}
                self.db.set_setting("matchbook_price_side_audit_latest", side_payload)
                stage["matchbook_price_side_audit"] = {
                    "markets": int(side_payload.get("markets") or 0),
                    "by_sport": side_payload.get("by_sport") or {},
                    "current_interpretation": side_payload.get("current_interpretation") or "back",
                }

            t = time.perf_counter()
            cache_rows = self._cache_rows_from_matches(matches, cfg)
            all_feeds_ok = bool(statuses) and all(bool(x.get("ok")) for x in statuses)
            cache_count = self.db.upsert_market_cache(cache_rows, deactivate_unseen=all_feeds_ok)
            stage["cache_write_ms"] = int((time.perf_counter() - t) * 1000)
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.db.finish_scan(
                scan_id, len(markets), len(matches), 0, statuses=statuses, duration_ms=duration_ms,
                stage_timings=stage, cache_entries=cache_count,
            )
            return {
                "ok": True, "kind": "discovery", "markets": len(markets), "matches": len(matches),
                "cache_entries": cache_count, "statuses": statuses, "duration_ms": duration_ms,
                "stage_timings": stage,
            }
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.db.rollback_if_needed()
            self.db.finish_scan(scan_id, statuses=statuses, error=str(exc), duration_ms=duration_ms, stage_timings=stage)
            return {"ok": False, "kind": "discovery", "message": str(exc), "statuses": statuses, "duration_ms": duration_ms}

    def discover_once(self, job_id: int | None = None) -> dict:
        return asyncio.run(self.discover_once_async(job_id=job_id))

    @staticmethod
    def _commission_from_source(source: dict) -> tuple[float, str]:
        rows = source.get("commission") or []
        if rows:
            try:
                return float(rows[0][0]), str(rows[0][1])
            except Exception:
                pass
        return 0.0, "cached market metadata"

    async def _refresh_cached_matches(self, cache_rows: list[dict], adapters: list, cfg: dict) -> tuple[list[MarketMatch], list[dict], dict, int]:
        adapter_by_name = {str(a.name): a for a in adapters}
        requests_by_exchange: dict[str, dict[tuple[str, str], dict]] = {}
        for row in cache_rows:
            for src in row.get("source_markets") or []:
                ex = str(src.get("exchange") or "")
                eid, mid = str(src.get("event_id") or ""), str(src.get("market_id") or "")
                if ex and mid:
                    requests_by_exchange.setdefault(ex, {})[(eid, mid)] = {"event_id": eid, "market_id": mid}

        state_map: dict[tuple[str, str, str], dict] = {}
        statuses: list[dict] = []
        started = time.perf_counter()

        async def fetch_exchange(exchange: str, reqmap: dict[tuple[str, str], dict]):
            adapter = adapter_by_name.get(exchange)
            if adapter is None:
                return exchange, [], 0, "Adapter unavailable"
            reqs = list(reqmap.values())
            t = time.perf_counter()
            try:
                if hasattr(adapter, "fetch_market_states"):
                    rows = await adapter.fetch_market_states(reqs)
                else:
                    rows = await asyncio.gather(*(adapter.fetch_market_state(str(r.get("event_id") or ""), str(r.get("market_id") or "")) for r in reqs))
                return exchange, list(rows), int((time.perf_counter() - t) * 1000), None
            except Exception as exc:
                return exchange, [], int((time.perf_counter() - t) * 1000), str(exc)

        fetched = await asyncio.gather(*(fetch_exchange(ex, reqmap) for ex, reqmap in requests_by_exchange.items()))
        for exchange, rows, elapsed_ms, error in fetched:
            if error:
                statuses.append({"exchange": exchange, "ok": False, "message": error, "markets": 0, "requested": len(requests_by_exchange.get(exchange, {})), "latency_ms": elapsed_ms})
                for (eid, mid) in requests_by_exchange.get(exchange, {}):
                    state_map[(exchange, eid, mid)] = {"ok": False, "exchange": exchange, "event_id": eid, "market_id": mid, "status": "ERROR", "in_play": None, "captured_at": utc_now_iso(), "latency_ms": elapsed_ms, "quotes": {}, "error": error}
                continue
            ok_rows = sum(1 for x in rows if x.get("ok"))
            max_latency = max([int(x.get("latency_ms") or 0) for x in rows] or [elapsed_ms])
            adapter = adapter_by_name.get(exchange)
            effective_values = [str(x.get("effective_feed_entitlement") or "").lower() for x in rows if x.get("effective_feed_entitlement")]
            effective_feed = effective_values[-1] if effective_values else str(getattr(adapter, "effective_feed_entitlement", "unknown") or "unknown").lower()
            requested_feed = str(getattr(adapter, "requested_feed_entitlement", "unknown") or "unknown").lower()
            feed_reason = str(next((x.get("feed_reason") for x in reversed(rows) if x.get("feed_reason")), None) or getattr(adapter, "feed_reason", "") or "")
            statuses.append({"exchange": exchange, "ok": ok_rows > 0 or not rows, "message": "OK" if ok_rows else "No targeted market states returned", "markets": ok_rows, "requested": len(rows), "latency_ms": max_latency,
                             "requested_feed_entitlement": requested_feed, "effective_feed_entitlement": effective_feed, "feed_reason": feed_reason})
            for state in rows:
                state_map[(exchange, str(state.get("event_id") or ""), str(state.get("market_id") or ""))] = state

        max_age_seconds = max(0.25, float(cfg.get("price_quote_max_age_seconds", 10.0) or 10.0))
        stale_rejections = 0
        now = datetime.now(timezone.utc)
        rebuilt: list[MarketMatch] = []
        snapshot_markets: list[ExchangeMarket] = []

        for row in cache_rows:
            markets: list[ExchangeMarket] = []
            for src in row.get("source_markets") or []:
                exchange = str(src.get("exchange") or "")
                event_id, market_id = str(src.get("event_id") or ""), str(src.get("market_id") or "")
                state = dict(state_map.get((exchange, event_id, market_id)) or {})
                captured_at = str(state.get("captured_at") or utc_now_iso())
                stale = False
                try:
                    captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                    if captured_dt.tzinfo is None:
                        captured_dt = captured_dt.replace(tzinfo=timezone.utc)
                    age = max(0.0, (now - captured_dt.astimezone(timezone.utc)).total_seconds())
                    stale = age > max_age_seconds
                except Exception:
                    age = max_age_seconds + 1.0
                    stale = True
                if stale:
                    stale_rejections += 1
                    state["ok"] = False
                    state["error"] = f"Quote state age {age:.2f}s exceeds {max_age_seconds:.2f}s maximum"
                runner_names = {str(k): str(v) for k, v in (src.get("runner_names") or {}).items()}
                runner_traps = {str(k): int(v) for k, v in (src.get("runner_traps") or {}).items() if v is not None}
                runner_keys = {str(k): str(v) for k, v in (src.get("runner_keys") or {}).items() if v}
                runner_statuses = {str(k): str(v) for k, v in (src.get("runner_statuses") or {}).items() if v}
                cached_runner_prices = {str(x.get("selection_id") or ""): x for x in (src.get("runner_prices") or []) if x.get("selection_id")}
                commission_pct, commission_source = self._commission_from_source(src)
                quotes = []
                for sid, q in (state.get("quotes") or {}).items():
                    if sid not in runner_names:
                        continue
                    try:
                        odds, liquidity = float(q.get("odds") or 0.0), float(q.get("liquidity") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if odds <= 1.0 or liquidity <= 0.0:
                        continue
                    raw_payload = {"targeted_refresh": True, "quote_age_seconds": round(age, 4), "raw_prices": list(q.get("raw_prices") or [])}
                    if exchange == "Matchbook":
                        cached = cached_runner_prices.get(sid) or {}
                        probe_rows = [x for x in (cached.get("raw_prices") or []) if str(x.get("source") or "") == "side_probe"]
                        if probe_rows:
                            raw_payload["_arbscanner_side_probe_prices"] = probe_rows
                            raw_payload["_arbscanner_side_probe_observed_at"] = next((x.get("observed_at") for x in probe_rows if x.get("observed_at")), None)
                    quotes.append(Quote(
                        exchange=exchange, event_id=event_id, market_id=market_id,
                        event_name=str(src.get("event_name") or row.get("event_name") or ""),
                        market_name=str(src.get("market_name") or row.get("market_name") or ""),
                        selection_id=sid, selection=runner_names[sid], odds=odds, liquidity=liquidity,
                        captured_at=captured_at, start_time=row.get("event_start"), commission_pct=commission_pct,
                        commission_source=commission_source, source_latency_ms=int(state.get("latency_ms") or 0),
                        market_type=str(src.get("market_type") or row.get("market_type") or row.get("market_name") or ""),
                        strategy=str(src.get("strategy") or row.get("strategy") or "two-way"), sport=str(src.get("sport") or row.get("sport") or "Unknown"),
                        in_play=state.get("in_play"), market_status=state.get("status"), raw=raw_payload,
                        section=str(src.get("section") or ("racing" if str(row.get("sport") or "") == "Greyhounds" else "sports")),
                        trap_number=runner_traps.get(sid), canonical_selection_key=runner_keys.get(sid), runner_status=runner_statuses.get(sid),
                        feed_entitlement=str(state.get("effective_feed_entitlement") or src.get("feed_entitlement") or "unknown").lower(),
                        market_data_transport=str(src.get("market_data_transport") or "poll").lower(),
                    ))
                market = ExchangeMarket(
                    exchange=exchange, event_id=event_id, market_id=market_id,
                    event_name=str(src.get("event_name") or row.get("event_name") or ""),
                    market_name=str(src.get("market_name") or row.get("market_name") or ""),
                    start_time=row.get("event_start"), quotes=quotes, status=str(state.get("status") or "UNKNOWN"),
                    market_type=str(src.get("market_type") or row.get("market_type") or ""),
                    strategy=str(src.get("strategy") or row.get("strategy") or "two-way"), sport=str(src.get("sport") or row.get("sport") or "Unknown"),
                    in_play=state.get("in_play"), raw={"targeted_refresh": True, "ok": bool(state.get("ok", False)), "error": state.get("error")},
                    section=str(src.get("section") or row.get("section") or ("racing" if str(row.get("sport") or "") == "Greyhounds" else "sports")),
                    race_track=src.get("race_track") or row.get("race_track"), race_number=src.get("race_number") or row.get("race_number"),
                )
                markets.append(market)
                if quotes:
                    snapshot_markets.append(market)
            if len(markets) < 2:
                continue
            flags = [m.in_play for m in markets if m.in_play is not None]
            in_play = True if any(x is True for x in flags) else False if flags and all(x is False for x in flags) else None
            statuses_now = [str(m.status or "").upper() for m in markets]
            status = "SUSPENDED" if any(x == "SUSPENDED" for x in statuses_now) else "CLOSED" if any(x == "CLOSED" for x in statuses_now) else "OPEN"
            rebuilt.append(MarketMatch(
                event_key=str(row.get("event_key") or ""), market_key=str(row.get("market_type") or row.get("market_name") or ""),
                display_event=str(row.get("event_name") or ""), display_market=str(row.get("market_name") or ""),
                start_time=row.get("event_start"), markets=markets, match_score=float(row.get("match_score") or 0.0),
                market_type=str(row.get("market_type") or row.get("market_name") or ""), strategy=str(row.get("strategy") or "two-way"),
                sport=str(row.get("sport") or "Unknown"), in_play=in_play, status=status,
                section=str(row.get("section") or ("racing" if str(row.get("sport") or "") == "Greyhounds" else "sports")),
                race_track=row.get("race_track"), race_number=row.get("race_number"), runner_count=int(row.get("runner_count") or 0) or (len(markets[0].quotes) if markets else None),
            ))

        snapshot_started = time.perf_counter()
        snapshot_error = None
        snapshot_result = {"rows": 0, "exchanges": 0}
        try:
            snapshot_result = self._persist_snapshots(snapshot_markets) or snapshot_result
        except Exception as exc:
            # v0.8.44: quote-storage failure must not masquerade as lost exchange
            # connectivity. Venue states are already known at this point, so keep
            # them and continue evaluating the fetched prices. The storage warning
            # remains visible in stage telemetry/Admin health.
            self.db.rollback_if_needed()
            snapshot_error = str(exc)
        self.db.mark_market_cache_refreshed([str(x.get("cache_key") or "") for x in cache_rows if x.get("cache_key")])
        stage = {"targeted_fetch_ms": int((time.perf_counter() - started) * 1000),
                 "snapshot_write_ms": int((time.perf_counter() - snapshot_started) * 1000),
                 "snapshot_rows": int(snapshot_result.get("rows") or 0),
                 "snapshot_storage_mode": "bounded_latest"}
        if snapshot_error:
            stage["snapshot_write_error"] = snapshot_error[:1000]
        for status_row in statuses:
            key = "betfair" if str(status_row.get("exchange") or "").lower().startswith("betfair") else "matchbook" if str(status_row.get("exchange") or "").lower().startswith("matchbook") else str(status_row.get("exchange") or "exchange").lower().replace(" ", "_")
            stage[f"{key}_fetch_ms"] = int(status_row.get("latency_ms") or 0)
        return rebuilt, statuses, stage, stale_rejections

    async def _evaluate_matches_async(self, *, scan_id: int, matches: list[MarketMatch], adapters: list, statuses: list[dict], cfg: dict,
                                      fetched_count: int, stage_timings: dict | None = None, cache_entries: int = 0,
                                      stale_rejections: int = 0, job_id: int | None = None, application_mode: str | None = None) -> dict:
        eval_started = time.perf_counter()
        application_mode = canonical_mode_value(application_mode or self._application_data_mode())
        live_decision_enabled = application_mode == "live" and bool(cfg.get("live_decision_evidence_enabled", True))
        require_cross = bool(cfg.get("require_cross_exchange", True))
        reference_bankroll = float(cfg.get("quality_reference_bankroll", 500.0))
        configured_bankroll_pct = float(cfg.get("max_bankroll_pct", 100.0))

        quality_rank = {"Tiny": 0, "Thin": 1, "Usable": 2, "Strong": 3, "Excellent": 4}

        def stream_rules(in_play: bool) -> dict:
            prefix = "inplay" if in_play else "pre_match"
            def value(name, legacy, default):
                return cfg.get(f"{prefix}_{name}", cfg.get(legacy, default))
            max_stake = max(0.0, float(value("execution_max_stake", "execution_max_stake", reference_bankroll) or 0.0))
            cap_pct = min(configured_bankroll_pct, (max_stake / reference_bankroll) * 100.0) if reference_bankroll > 0 and max_stake > 0 else configured_bankroll_pct
            return {
                "min_liquidity": max(0.0, float(value("minimum_liquidity", "minimum_liquidity", 2.0) or 0.0)),
                "min_roi": max(0.0, float(value("minimum_net_roi_pct", "minimum_net_roi_pct", 1.0) or 0.0)),
                "min_profit": max(0.0, float(value("minimum_profit", "minimum_profit", 0.0) or 0.0)),
                "min_quality": str(value("minimum_quality_band", "minimum_quality_band", "Tiny") or "Tiny").title(),
                "max_stake": max_stake,
                "reference_cap_pct": cap_pct,
                "max_event_exposure_pct": min(100.0, max(0.0, float(value("max_event_exposure_pct", "max_event_exposure_pct", 100.0) or 0.0))),
                "max_slippage_pct": max(0.0, float(value("execution_max_slippage_pct", "execution_max_slippage_pct", 0.5) or 0.0)),
                "max_unhedged_exposure": max(0.0, float(value("execution_max_unhedged_exposure", "execution_max_unhedged_exposure", 25.0) or 0.0)),
                "hedge_reserve_pct": min(100.0, max(0.0, float(value("execution_hedge_reserve_pct", "execution_hedge_reserve_pct", 20.0) or 0.0))),
            }
        found, seen_track_keys = [], set()
        processed_candidates = positive_opportunities = qualified_count = 0
        monitor_timing_tasks: list[tuple[dict, asyncio.Task]] = []
        monitor_timing_contexts: dict[int, tuple[list[Leg], dict, MarketMatch]] = {}
        if application_mode != "live":
            self.db.ensure_monitor_streams(
                {
                    "betfair": float(cfg.get("pre_match_monitor_betfair_starting_balance", cfg.get("monitor_betfair_starting_balance", 250.0)) or 0.0),
                    "matchbook": float(cfg.get("pre_match_monitor_matchbook_starting_balance", cfg.get("monitor_matchbook_starting_balance", 250.0)) or 0.0),
                },
                {
                    "betfair": float(cfg.get("inplay_monitor_betfair_starting_balance", cfg.get("monitor_betfair_starting_balance", 250.0)) or 0.0),
                    "matchbook": float(cfg.get("inplay_monitor_matchbook_starting_balance", cfg.get("monitor_matchbook_starting_balance", 250.0)) or 0.0),
                },
                {
                    "betfair": float(cfg.get("racing_monitor_betfair_starting_balance", cfg.get("monitor_betfair_starting_balance", 250.0)) or 0.0),
                    "matchbook": float(cfg.get("racing_monitor_matchbook_starting_balance", cfg.get("monitor_matchbook_starting_balance", 250.0)) or 0.0),
                },
            )
        checkpoints = tuple(int(x) for x in (cfg.get("monitor_timing_checkpoints_ms") or [100, 250, 500, 1000]) if int(x) > 0)
        monitor_timing_observer = MonitorTimingObserver(self.db, checkpoints_ms=checkpoints)
        monitor_timing_semaphore = asyncio.Semaphore(max(1, int(cfg.get("monitor_timing_max_concurrent_runs", 6) or 6)))
        # 0.9.16: scaled-entry execution is capability-driven, not engine-name driven.
        scaled_entry_engine = self.engine_runtime.scaled_entry_execution_config(section="sports")
        # Wallet state is scan-stable for route selection until an execution mutates
        # it. Avoid a repository read for every matched market; refresh once per
        # stream/reserve combination within this evaluation pass.
        _routing_wallet_cache: dict[tuple[str, float], dict] = {}
        def routing_wallet_snapshot(stream_name: str, reserve_pct: float) -> dict:
            if application_mode == "live":
                return {}
            cache_key = (str(stream_name), round(float(reserve_pct or 0.0), 6))
            if cache_key not in _routing_wallet_cache:
                _routing_wallet_cache[cache_key] = self.db.monitor_wallet_snapshot(reserve_pct, stream_name)
            return _routing_wallet_cache[cache_key]

        async def run_monitor_timing_measurement(**kwargs):
            async with monitor_timing_semaphore:
                return await monitor_timing_observer.observe(**kwargs)

        for mm in matches:
            decision_started = time.perf_counter()
            is_racing = mm.section == "racing" or mm.strategy == "multi_runner_win" or mm.sport == "Greyhounds"
            # 0.9.37: Sports Config owns the outer stream envelope. Engine
            # enablement cannot process a Sports stream disabled globally.
            if not is_racing:
                if mm.in_play is True and not bool(cfg.get("inplay_monitor_enabled", True)):
                    continue
                if mm.in_play is not True and not bool(cfg.get("pre_match_monitor_enabled", True)):
                    continue
            rules = stream_rules(mm.in_play is True)
            min_liquidity = rules["min_liquidity"]
            min_roi = rules["min_roi"]
            min_profit = rules["min_profit"]
            if is_racing:
                min_liquidity = max(0.0, float(cfg.get("racing_minimum_liquidity", min_liquidity) or 0.0))
                min_roi = max(0.0, float(cfg.get("racing_minimum_net_roi_pct", min_roi) or 0.0))
                min_profit = max(0.0, float(cfg.get("racing_minimum_profit", min_profit) or 0.0))
            # Strategy thresholds are owned by the canonical engine instance.
            # In-play keeps its dedicated platform stream thresholds for 0.9.16
            # compatibility; pre-match/racing strategy qualification is engine-owned.
            if is_racing or mm.in_play is not True:
                engine_policy = self.engine_runtime.primary_config(section="racing" if is_racing else "sports")
                if engine_policy:
                    # Sports/Racing guardrails are the outer envelope. Engines may
                    # be stricter, never looser than the portfolio-wide minimums.
                    min_liquidity = max(min_liquidity, max(0.0, float(engine_policy.get("minimum_liquidity", min_liquidity) or 0.0)))
                    min_roi = max(min_roi, max(0.0, float(engine_policy.get("minimum_edge", min_roi) or 0.0)))
                    min_profit = max(min_profit, max(0.0, float(engine_policy.get("minimum_profit", min_profit) or 0.0)))
            min_quality = rules["min_quality"] if rules["min_quality"] in quality_rank else "Tiny"
            max_stake = rules["max_stake"]
            reference_cap_pct = rules["reference_cap_pct"]
            max_event_exposure_pct = rules["max_event_exposure_pct"]
            max_slippage_pct = rules["max_slippage_pct"]
            if (is_racing or mm.in_play is not True) and engine_policy:
                engine_slip = engine_policy.get("maximum_slippage", engine_policy.get("max_slippage_pct"))
                if engine_slip is not None:
                    max_slippage_pct = min(max_slippage_pct, max(0.0, float(engine_slip or 0.0)))
            max_unhedged_exposure = rules["max_unhedged_exposure"]
            hedge_reserve_pct = rules["hedge_reserve_pct"]
            if is_racing:
                min_quality = str(cfg.get("racing_minimum_quality_band", min_quality) or min_quality).title()
                if min_quality not in quality_rank:
                    min_quality = "Tiny"
                max_stake = max(0.0, float(cfg.get("racing_execution_max_stake", max_stake) or 0.0))
                reference_cap_pct = min(configured_bankroll_pct, (max_stake / reference_bankroll) * 100.0) if reference_bankroll > 0 and max_stake > 0 else configured_bankroll_pct
                max_event_exposure_pct = min(100.0, max(0.0, float(cfg.get("racing_max_event_exposure_pct", max_event_exposure_pct) or 0.0)))
                max_slippage_pct = max(0.0, float(cfg.get("racing_execution_max_slippage_pct", max_slippage_pct) or 0.0))
                if engine_policy:
                    engine_slip = engine_policy.get("maximum_slippage", engine_policy.get("max_slippage_pct"))
                    if engine_slip is not None:
                        max_slippage_pct = min(max_slippage_pct, max(0.0, float(engine_slip or 0.0)))
                max_unhedged_exposure = max(0.0, float(cfg.get("racing_execution_max_unhedged_exposure", max_unhedged_exposure) or 0.0))
                hedge_reserve_pct = min(100.0, max(0.0, float(cfg.get("racing_execution_hedge_reserve_pct", hedge_reserve_pct) or 0.0)))
            if mm.strategy == "1x2" and not cfg.get("research_1x2_enabled", True):
                continue
            if mm.strategy == "two-way" and not cfg.get("research_two_way_enabled", True):
                continue
            if mm.strategy == "multi_runner_win" and not cfg.get("research_multi_runner_enabled", True):
                continue
            processed_candidates += 1
            candidates = self._candidate_legs(mm, racing_threshold=float(cfg.get("racing_runner_match_threshold", 0.92) or 0.92))
            source_markets = self._source_markets(mm)
            feed_generation = "|".join(sorted({str(getattr(leg, "source_state_version", None) or "unknown") for rows in candidates.values() for leg in rows})) if candidates else "unknown"
            engine_evidence = MarketEvidence.from_candidates(mm, candidates, feed_generation=feed_generation) if candidates else None
            routing_stream = "racing" if is_racing else ("in_play" if mm.in_play is True else "pre_match")
            routing_wallets = routing_wallet_snapshot(routing_stream, hedge_reserve_pct)
            primary_evaluation = self.engine_runtime.evaluate_primary(
                engine_evidence, minimum_liquidity=min_liquidity, require_cross_exchange=require_cross,
                reference_bankroll=reference_bankroll, minimum_edge=min_roi, minimum_profit=min_profit,
                maximum_slippage=max_slippage_pct, venue_wallets=routing_wallets,
            ) if engine_evidence is not None else None
            diagnostic_legs = list(primary_evaluation.diagnostic_legs) if primary_evaluation else []
            eligible_legs = list(primary_evaluation.selected_legs) if primary_evaluation else []
            routing_diagnostics = strategy_routing_diagnostics(
                candidates, eligible_legs, minimum_liquidity=min_liquidity, require_cross_exchange=require_cross,
                venue_wallets=routing_wallets,
            ) if candidates and eligible_legs else {"economic_tie": False, "reason": "no_selected_route", "alternatives": []}
            display_legs = eligible_legs or diagnostic_legs
            legs_payload = [asdict(l) for l in display_legs]
            theoretical = gross_roi = commission_impact = net_roi = diagnostic_deployed = diagnostic_profit = limited_by = None
            max_executable_stake = None
            book_revision = None
            timing_evidence = {"quote_oldest_age_ms": None, "quote_newest_age_ms": None, "quote_receipt_spread_ms": None, "source_timestamp_spread_ms": None, "timestamp_quality": "UNKNOWN", "legs": []}
            racing_retry = None
            limiting_provider = limiting_selection = limiting_side = liquidity_rejection_reason = None
            liquidity_capable = None
            depth_at_qualification = {}
            quote_age_at_qualification_ms = None
            profile = None
            dq = None
            status = "incomplete"
            expected_label = f"{mm.runner_count or (len(mm.markets[0].quotes) if mm.markets else 0)} racing runners" if is_racing else ("three 1X2 outcomes" if mm.strategy == "1x2" else "two outcomes")
            reason = f"Could not align all {expected_label} across the configured feeds"
            if not candidates:
                pass
            elif not diagnostic_legs:
                status, reason = "no_strategy_combo", "No complete leg combination satisfies the cross-exchange strategy rule"
            else:
                liquidity_capable = all(float(l.liquidity or 0.0) + 1e-12 >= float(min_liquidity or 0.0) for l in diagnostic_legs)
                if not liquidity_capable:
                    min_seen = min((float(l.liquidity or 0.0) for l in diagnostic_legs), default=0.0)
                    liquidity_rejection_reason = f"required leg below displayed-liquidity floor (£{min_seen:.2f} < £{float(min_liquidity or 0.0):.2f})"
                liquidity_probe = diagnose_equal_return(diagnostic_legs, 1_000_000_000.0)
                if liquidity_probe.get("valid"):
                    max_executable_stake = float(liquidity_probe.get("deployed") or 0.0)
                    limiting = liquidity_probe.get("limiting_leg")
                    if limiting is not None:
                        limiting_provider = str(getattr(limiting, "resolved_provider_id", None) or getattr(limiting, "provider_id", None) or getattr(limiting, "exchange", "") or "")
                        limiting_selection = str(getattr(limiting, "selection", "") or "")
                        limiting_side = str(getattr(limiting, "side", "BACK") or "BACK").upper()
                quote_ages = []
                for leg in diagnostic_legs:
                    levels = []
                    for dl in getattr(leg, "depth_levels", ()) or ():
                        try:
                            side = str(getattr(dl, "side", None) or dl.get("side") or "BACK").upper()
                            level = int(getattr(dl, "level", None) or dl.get("level") or 0)
                            odds = float(getattr(dl, "odds", None) or dl.get("odds") or 0.0)
                            size = float(getattr(dl, "available_size", None) or dl.get("available_size") or 0.0)
                        except Exception:
                            continue
                        if level > 0:
                            levels.append({"side": side, "level": level, "odds": odds, "available_size": size})
                    key = f"{leg.resolved_provider_id}:{leg.market_id or ''}:{leg.selection_id or leg.selection}"
                    depth_at_qualification[key] = {
                        "provider_id": leg.resolved_provider_id, "venue_id": leg.resolved_venue_id, "selection": leg.selection,
                        "side": str(leg.side or "BACK").upper(), "top_book": float(leg.liquidity or 0.0),
                        "top3": round(sum(float(x["available_size"]) for x in levels if x["side"] == str(leg.side or "BACK").upper() and int(x["level"]) <= 3), 4) if levels else float(leg.liquidity or 0.0),
                        "levels": levels, "feed_entitlement": leg.feed_entitlement, "transport": leg.market_data_transport,
                    }
                    if leg.quote_age_ms is not None:
                        quote_ages.append(int(leg.quote_age_ms))
                    elif leg.captured_at:
                        try:
                            from datetime import datetime, timezone
                            qdt = datetime.fromisoformat(str(leg.captured_at).replace("Z", "+00:00"))
                            if qdt.tzinfo is None: qdt = qdt.replace(tzinfo=timezone.utc)
                            quote_ages.append(max(0, int((datetime.now(timezone.utc) - qdt.astimezone(timezone.utc)).total_seconds() * 1000)))
                        except Exception:
                            pass
                quote_age_at_qualification_ms = max(quote_ages) if quote_ages else None
                diag = diagnose_equal_return(diagnostic_legs, 1000.0)
                theoretical = float(diag.get("theoretical_edge_pct", arb_edge(diagnostic_legs)))
                gross_roi = float(diag.get("gross_roi_pct", 0.0)) if diag.get("valid") else None
                commission_impact = float(diag.get("commission_impact_pct", 0.0)) if diag.get("valid") else None
                net_roi = float(diag.get("expected_roi_pct", 0.0)) if diag.get("valid") else None
                diagnostic_deployed = float(diag.get("deployed", 0.0)) if diag.get("valid") else None
                diagnostic_profit = float(diag.get("expected_profit", 0.0)) if diag.get("valid") else None
                limited_by = diag.get("limited_by") if diag.get("valid") else None
                if not diag.get("valid"):
                    status, reason = "not_executable", str(diag.get("reason") or "Not executable")
                elif not eligible_legs:
                    status = "below_liquidity"
                    min_seen = min((l.liquidity for l in diagnostic_legs), default=0.0)
                    reason = f"At least one required leg is below the £{min_liquidity:.2f} displayed-liquidity floor (lowest £{min_seen:.2f})"
                else:
                    diag = diagnose_equal_return(eligible_legs, 1000.0)
                    display_legs = eligible_legs; legs_payload = [asdict(l) for l in display_legs]
                    theoretical = float(diag.get("theoretical_edge_pct", arb_edge(eligible_legs)))
                    gross_roi = float(diag.get("gross_roi_pct", 0.0)) if diag.get("valid") else None
                    commission_impact = float(diag.get("commission_impact_pct", 0.0)) if diag.get("valid") else None
                    net_roi = float(diag.get("expected_roi_pct", 0.0)) if diag.get("valid") else None
                    diagnostic_deployed = float(diag.get("deployed", 0.0)) if diag.get("valid") else None
                    diagnostic_profit = float(diag.get("expected_profit", 0.0)) if diag.get("valid") else None
                    limited_by = diag.get("limited_by") if diag.get("valid") else None
                    ref_sim = simulate_equal_return(eligible_legs, Scenario("quality", reference_bankroll, reference_cap_pct, max_event_exposure_pct))
                    dq = assess_data_quality(eligible_legs, mm.match_score, stale_after_seconds=float(cfg.get("price_quote_max_age_seconds", 10.0)))
                    profile = quality_profile(ref_sim, mm.match_score, reference_bankroll, data_quality=dq)
                    distinct_exchanges = len({l.exchange for l in eligible_legs})
                    book_revision = self._signature(mm.event_key, mm.display_market, eligible_legs) if eligible_legs else None
                    timing_evidence = self._timing_evidence(eligible_legs or diagnostic_legs)
                    racing_retry = None
                    if require_cross and distinct_exchanges < 2:
                        status, reason = "single_exchange", "Selected legs are from one exchange only; not a cross-exchange arbitrage"
                    elif theoretical <= 0:
                        status, reason = "no_arb", "No theoretical arbitrage at the best eligible cross-exchange prices"
                    elif net_roi is None or net_roi <= 0:
                        status, reason = "commission_removed", "Raw arbitrage disappears after commission"
                    elif net_roi < min_roi:
                        status, reason = "below_threshold", f"Net ROI on deployed capital {net_roi:.3f}% is below the configured {min_roi:.3f}% threshold"
                    elif float(profile.get("expected_profit") or 0.0) + 1e-9 < min_profit:
                        status, reason = "below_profit_threshold", f"Monitor profit £{float(profile.get('expected_profit') or 0.0):.2f} at the £{reference_bankroll:.0f} reference bankroll is below the configured £{min_profit:.2f} threshold"
                    elif quality_rank.get(str(profile.get("quality_band") or "Invalid"), -1) < quality_rank.get(min_quality, 0):
                        status, reason = "below_quality", f"Opportunity quality {str(profile.get('quality_band') or 'Invalid')} is below the configured {min_quality} minimum"
                    elif is_racing and mm.in_play is True:
                        status, reason = "racing_in_play_excluded", "Racing v0.9.36 is pre-race MONITOR only; in-play and LIVE execution are locked"
                    elif is_racing and float(mm.match_score or 0.0) + 1e-12 < float(cfg.get("racing_match_threshold", 0.90) or 0.90):
                        status, reason = "racing_identity_rejected", "Race identity confidence is below the configured strict Racing threshold"
                    elif is_racing and len(eligible_legs) != int(mm.runner_count or len(eligible_legs)):
                        status, reason = "racing_runner_field_incomplete", "Strict runner identity did not produce one deployable leg for every Greyhound outcome"
                    elif is_racing and (not eligible_legs or any(not l.captured_at for l in eligible_legs)):
                        status, reason = "racing_stale_quotes", "Racing MONITOR requires a timestamped fresh quote for every selected runner"
                    elif is_racing and float((dq or {}).get("max_local_quote_age_seconds") or 0.0) > max(0.25, float(cfg.get("price_quote_max_age_seconds", 10.0) or 10.0)):
                        status, reason = "racing_stale_quotes", f"At least one selected Racing quote is older than the configured {float(cfg.get('price_quote_max_age_seconds', 10.0) or 10.0):g}s freshness limit"
                    elif application_mode == "live":
                        status, reason = "recommended", "Passed existing provider-neutral economics and strategy qualification for isolated LIVE decision evidence; real orders remain structurally disabled"
                    elif is_racing and not bool(cfg.get("racing_monitor_enabled", True)):
                        status, reason = "racing_research", "Racing opportunity passes pricing checks, but Racing MONITOR execution is disabled"
                    elif is_racing and self.db.monitor_has_open_market(mm.event_key, mm.display_market, "racing"):
                        status, reason = "racing_position_open", "Racing MONITOR position already open for this race"
                    elif is_racing:
                        max_skew_ms = max(0, int(float(cfg.get("racing_max_cross_venue_receipt_spread_ms", 0) or 0)))
                        receipt_skew = timing_evidence.get("quote_receipt_spread_ms")
                        if max_skew_ms and receipt_skew is not None and int(receipt_skew) > max_skew_ms:
                            status, reason = "racing_time_skew", f"Selected Racing quote receipt spread {int(receipt_skew)}ms exceeds the configured {max_skew_ms}ms limit"
                        else:
                            racing_retry = self.db.racing_retry_gate(
                                mm.event_key, mm.display_market, mm.sport, str(book_revision or ""),
                                cooldown_seconds=float(cfg.get("racing_monitor_retry_cooldown_seconds", 5.0) or 0.0),
                                max_attempts=int(cfg.get("racing_monitor_max_attempts_per_race", 3) or 3),
                            )
                            if not bool(racing_retry.get("allowed")):
                                code = str(racing_retry.get("code") or "SUPPRESSED")
                                status = "racing_retry_wait" if code in {"COOLDOWN", "UNCHANGED_BOOK", "ATTEMPT_UNRESOLVED"} else "already_recommended"
                                reason = str(racing_retry.get("reason") or "Racing MONITOR retry is not currently eligible")
                            else:
                                status, reason = "racing_monitor", "Qualified for pre-race Greyhound MONITOR execution; LIVE orders remain hard-locked"
                    elif mm.in_play is True and bool(cfg.get("inplay_monitor_enabled", True)):
                        cooldown_seconds = max(0.0, float(cfg.get("inplay_monitor_cooldown_seconds", 8.0) or 0.0))
                        if self.db.monitor_has_open_market(mm.event_key, mm.display_market, "in_play"):
                            status, reason = "in_play_position_open", "IN-PLAY Monitor position already open for this event/market"
                        elif self.db.recent_recommendation(mm.event_key, mm.display_market, mm.sport, in_play=True, within_seconds=cooldown_seconds):
                            status, reason = "in_play_cooldown", f"Fresh IN-PLAY signal suppressed for {cooldown_seconds:g}s after the previous Monitor attempt"
                        else:
                            status, reason = "in_play_monitor", "Qualified for IN-PLAY Monitor simulation; LIVE orders remain blocked"
                    elif mm.in_play is True:
                        status, reason = "in_play_research", "In-play research only — Monitor in-play simulation is disabled"
                    elif cfg.get("one_recommendation_per_market", True) and self.db.existing_recommendation(mm.event_key, mm.display_market, mm.sport, in_play=False):
                        status, reason = "already_recommended", "This event/market already has a stored Monitor candidate"
                    else:
                        status, reason = "recommended", "Passed cross-exchange, commission, liquidity and ROI checks"
                    if application_mode != "live" and (not is_racing) and net_roi is not None and net_roi > 0 and profile and profile.get("quality_band") != "Invalid":
                        track_key = self._track_key(mm.event_key, mm.display_market, mm.strategy, mm.sport)
                        seen_track_keys.add(track_key)
                        self.db.upsert_track(track_key, scan_id, mm.event_key, mm.display_event, mm.display_market, mm.strategy,
                            net_roi, float(profile.get("bankroll_roi_pct") or 0.0), float(profile.get("deployed") or 0.0),
                            float(profile.get("expected_profit") or 0.0), float(profile.get("quality_score") or 0.0),
                            str(profile.get("quality_band") or "Invalid"), reference_bankroll, status, reason, sport=mm.sport)
            if net_roi is not None and float(net_roi) > 0:
                positive_opportunities += 1
            if status in {"recommended", "in_play_monitor", "racing_monitor"}:
                qualified_count += 1
            if live_decision_enabled:
                live_evidence = self._build_live_decision_evidence(
                    mm=mm, status=status, reason=reason, selected_legs=eligible_legs, diagnostic_legs=diagnostic_legs,
                    profile=profile, timing_evidence=timing_evidence, cfg=cfg, theoretical=theoretical, net_roi=net_roi,
                    max_executable_stake=max_executable_stake, limiting_provider=limiting_provider,
                    limiting_selection=limiting_selection, limiting_side=limiting_side, liquidity_capable=liquidity_capable,
                    reference_bankroll=reference_bankroll, reference_cap_pct=reference_cap_pct,
                    max_event_exposure_pct=max_event_exposure_pct, decision_started=decision_started, book_revision=book_revision,
                )
                write_result = self.db.record_live_decision(live_evidence)
                live_evidence["created"] = bool(write_result.get("created"))
                live_evidence["duplicate_revision"] = bool(write_result.get("duplicate_revision"))
                if live_evidence.get("state") != "NO_ARB" or live_evidence.get("created"):
                    found.append({
                        "id": live_evidence.get("decision_id"), "decision_id": live_evidence.get("decision_id"),
                        "event": mm.display_event, "market": mm.display_market, "strategy": mm.strategy, "sport": mm.sport,
                        "section": mm.section, "book_revision": live_evidence.get("book_revision"),
                        "qualification_status": "live_decision", "live_decision": live_evidence,
                        "application_mode": "live", "decision_type": "simulated", "real_orders_sent": 0,
                    })
                continue
            self.db.add_matched_market(scan_id=scan_id, event_key=mm.event_key, event_name=mm.display_event, event_start=mm.start_time,
                market_name=mm.display_market, match_score=mm.match_score, theoretical_edge_pct=theoretical,
                gross_roi_pct=gross_roi, commission_impact_pct=commission_impact, net_roi_pct=net_roi,
                diagnostic_deployed=diagnostic_deployed, diagnostic_profit=diagnostic_profit, limited_by=limited_by,
                status=status, reason=reason, legs=legs_payload, source_markets=source_markets,
                strategy=mm.strategy, quality=profile, sport=mm.sport, in_play=mm.in_play, event_status=mm.status,
                section=mm.section, race_track=mm.race_track, race_number=mm.race_number, runner_count=mm.runner_count,
                time_to_off_seconds=seconds_to_off(mm.start_time), max_executable_stake=max_executable_stake,
                limiting_provider=limiting_provider, limiting_selection=limiting_selection, limiting_side=limiting_side,
                liquidity_capable=liquidity_capable, liquidity_rejection_reason=liquidity_rejection_reason,
                depth_at_qualification=depth_at_qualification, quote_age_at_qualification_ms=quote_age_at_qualification_ms,
                book_revision=book_revision, quote_oldest_age_ms=timing_evidence.get("quote_oldest_age_ms"),
                quote_newest_age_ms=timing_evidence.get("quote_newest_age_ms"), quote_receipt_spread_ms=timing_evidence.get("quote_receipt_spread_ms"),
                source_timestamp_spread_ms=timing_evidence.get("source_timestamp_spread_ms"), timestamp_quality=timing_evidence.get("timestamp_quality"),
                book_complete=bool(candidates))
            # v0.8.27: qualified Greyhound Win races may enter deterministic
            # MONITOR execution, but Racing LIVE and in-play remain hard-locked.
            research_only = status == "in_play_research"
            in_play_monitor = status == "in_play_monitor"
            racing_monitor = status == "racing_monitor"
            if status not in {"recommended", "in_play_monitor", "in_play_research", "racing_monitor"}:
                continue
            # Research-only captures remain lifetime-deduplicated. Executable IN-PLAY
            # Monitor signals are intentionally NOT lifetime-deduplicated: recurring
            # signals may create a new attempt after the short cooldown, provided no
            # position for the canonical event/market is still open.
            if research_only and cfg.get("one_recommendation_per_market", True) and self.db.existing_recommendation(mm.event_key, mm.display_market, mm.sport, in_play=True):
                continue
            legs = eligible_legs
            probe = simulate_equal_return(legs, Scenario("probe", 1000.0, 100.0, 100.0))
            if not probe.get("executable"):
                continue
            sig = self._signature(mm.event_key, mm.display_market, legs)
            oid = self.db.add_opportunity(mm.event_key, mm.display_event, mm.start_time, mm.display_market, theoretical, net_roi,
                [asdict(l) for l in legs], source_markets, mm.match_score, sig, strategy=mm.strategy,
                sport=mm.sport, in_play=mm.in_play, event_status=mm.status, job_id=job_id,
                section=mm.section, race_track=mm.race_track, race_number=mm.race_number, runner_count=mm.runner_count,
                time_to_off_seconds=seconds_to_off(mm.start_time), max_executable_stake=max_executable_stake,
                limiting_provider=limiting_provider, limiting_selection=limiting_selection, limiting_side=limiting_side,
                liquidity_capable=liquidity_capable, liquidity_rejection_reason=liquidity_rejection_reason,
                depth_at_qualification=depth_at_qualification, quote_age_at_qualification_ms=quote_age_at_qualification_ms,
                book_revision=book_revision or sig, quote_oldest_age_ms=timing_evidence.get("quote_oldest_age_ms"),
                quote_newest_age_ms=timing_evidence.get("quote_newest_age_ms"), quote_receipt_spread_ms=timing_evidence.get("quote_receipt_spread_ms"),
                source_timestamp_spread_ms=timing_evidence.get("source_timestamp_spread_ms"), timestamp_quality=timing_evidence.get("timestamp_quality"),
                engine_instance_id=(primary_evaluation.context.engine_instance_id if primary_evaluation else None),
                engine_type=(primary_evaluation.context.engine_type if primary_evaluation else None),
                engine_version=(primary_evaluation.context.engine_version if primary_evaluation else None),
                engine_config_version=(primary_evaluation.context.config_version if primary_evaluation else None),
                routing_diagnostics=routing_diagnostics)
            if research_only:
                self.db.set_opportunity_qualification(
                    oid,
                    "in_play_research",
                    "In-play research only — timed price/liquidity observations; no Monitor or LIVE orders",
                    scan_id=scan_id,
                )
            elif in_play_monitor:
                self.db.set_opportunity_qualification(
                    oid,
                    "in_play_qualified",
                    "Qualified for IN-PLAY Monitor simulation using the configured deterministic delay model; LIVE orders blocked",
                    scan_id=scan_id,
                )
            elif racing_monitor:
                self.db.set_opportunity_qualification(
                    oid,
                    "racing_qualified",
                    "Strict Greyhound identity, fresh complete pricing, full-plan liquidity and positive post-commission ROI passed; MONITOR only, LIVE locked",
                    scan_id=scan_id,
                )
            scenario_results = []
            scenario_capitals = list(self.db.get_setting("scenarios", [500, 1000, 5000, 10000]) or [])
            if not any(abs(float(x) - reference_bankroll) < 0.0001 for x in scenario_capitals):
                scenario_capitals.insert(0, reference_bankroll)
            for capital in scenario_capitals:
                capital = float(capital)
                cap_pct = min(configured_bankroll_pct, (max_stake / capital) * 100.0) if capital > 0 and max_stake > 0 else configured_bankroll_pct
                sc = Scenario(f"£{capital:g}", capital, cap_pct, max_event_exposure_pct)
                sim = simulate_equal_return(legs, sc)
                if sim.get("executable"):
                    self.db.add_scenario_run(oid, sc.name, sc.bankroll, sim["deployed"], sim["expected_profit"], sim["expected_roi_pct"], sim["limited_by"], sim["stakes"], sim["outcome_pnls"])
                scenario_results.append({"capital": capital, **sim})
            found_item = {"id": oid, "event": mm.display_event, "market": mm.display_market, "strategy": mm.strategy, "sport": mm.sport,
                "event_timing": event_phase(mm.start_time, mm.status, mm.in_play), "edge_pct": round(float(theoretical or 0.0), 4),
                "net_roi_pct": round(float(net_roi or 0.0), 4), "match_score": round(mm.match_score, 3), "quality": profile,
                "legs": [asdict(l) for l in legs], "scenarios": scenario_results,
                "qualification_status": "in_play_research" if research_only else ("in_play_qualified" if in_play_monitor else ("racing_qualified" if racing_monitor else "qualified")),
                "monitor_stream": "racing" if racing_monitor else ("in_play" if in_play_monitor else "pre_match"),
                "book_revision": str(book_revision or sig),
                "timing_evidence": timing_evidence,
                "racing_retry": racing_retry if racing_monitor else None,
                "section": mm.section,
                "_alert_track_key": self._track_key(mm.event_key, mm.display_market, mm.strategy, mm.sport),
                "_alert_delayed": any("delayed" in l.exchange.lower() for l in legs)}
            runtime_mode = str(self.db.get_setting("mode", "sim") or "sim").lower()
            if research_only or canonical_mode_value(runtime_mode) == "sim":
                try:
                    monitor_timing_sim = simulate_equal_return(legs, Scenario("monitor", reference_bankroll, reference_cap_pct, max_event_exposure_pct))
                    if monitor_timing_sim.get("executable"):
                        found_item["monitor_timing_execution"] = {
                            "mode": "research" if research_only else "sim",
                            "monitor_stream": "racing" if racing_monitor else ("in_play" if in_play_monitor else "pre_match"),
                            "live_order_placement": False,
                            "research_only": bool(research_only),
                            "state": "MEASURING",
                            "checkpoints_ms": list(checkpoints),
                        }
                        monitor_timing_contexts[oid] = (legs, monitor_timing_sim, mm)
                        inplay_delay_model = {
                            "betfair": {
                                "delay_ms": float(cfg.get("inplay_betfair_delay_ms", 5000) or 0),
                                "adverse_odds_pct_per_second": float(cfg.get("inplay_adverse_odds_pct_per_second", 0.20) or 0),
                                "liquidity_decay_pct_per_second": float(cfg.get("inplay_liquidity_decay_pct_per_second", 8.0) or 0),
                            },
                            "matchbook": {
                                "delay_ms": float(cfg.get("inplay_matchbook_delay_ms", 1000) or 0),
                                "adverse_odds_pct_per_second": float(cfg.get("inplay_adverse_odds_pct_per_second", 0.20) or 0),
                                "liquidity_decay_pct_per_second": float(cfg.get("inplay_liquidity_decay_pct_per_second", 8.0) or 0),
                            },
                        } if in_play_monitor else {}
                        task = asyncio.create_task(run_monitor_timing_measurement(
                            opportunity_id=oid, original_legs=legs, original_simulation=monitor_timing_sim, adapters=adapters,
                            event_start=mm.start_time, bankroll=reference_bankroll, max_bankroll_pct=reference_cap_pct,
                            max_event_exposure_pct=max_event_exposure_pct, min_roi=min_roi, min_profit=min_profit,
                            pre_match_only=False if (research_only or in_play_monitor) else bool(cfg.get("execution_pre_match_only", True)), reference_checkpoint_ms=int(cfg.get("monitor_timing_reference_checkpoint_ms", 250) or 250),
                            execution_checkpoint_ms=int(cfg.get("monitor_execution_checkpoint_ms", 500) or 500), hedge_checkpoint_ms=int(cfg.get("monitor_hedge_checkpoint_ms", 1000) or 1000),
                            event_key=mm.event_key, market_name=mm.display_market, hedge_reserve_pct=hedge_reserve_pct,
                            plan_ttl_ms=int(cfg.get("execution_plan_ttl_ms", 1500) or 1500), max_slippage_pct=max_slippage_pct,
                            max_unhedged_exposure=max_unhedged_exposure, balance_tolerance=float(cfg.get("execution_balance_tolerance", 0.10) or 0.0),
                            research_only=research_only, monitor_stream="racing" if racing_monitor else ("in_play" if in_play_monitor else "pre_match"),
                            delay_model_by_exchange=inplay_delay_model,
                            scaled_entry_enabled=bool(scaled_entry_engine.get("enabled", False)),
                            scaled_entry_max_tranches=scaled_entry_engine.get("max_tranches", 3),
                            scaled_entry_tranche_size_mode=str(scaled_entry_engine.get("tranche_size_mode", "base") or "base"),
                            scaled_entry_tranche_size=float(scaled_entry_engine.get("tranche_size", 0.0) or 0.0),
                            scaled_entry_max_total_stake=float(scaled_entry_engine.get("max_total_stake", 100.0) or 0.0),
                            scaled_entry_min_net_edge=float(scaled_entry_engine.get("min_net_edge", 1.0) or 0.0),
                            scaled_entry_min_depth_multiplier=float(scaled_entry_engine.get("min_depth_multiplier", 1.25) or 1.0),
                            scaled_entry_recheck_delay_ms=int(scaled_entry_engine.get("recheck_delay_ms", 100) or 0),
                            scaled_entry_global_bankroll_pct=configured_bankroll_pct,
                            handoff_in_play=bool((not is_racing) and (not research_only) and (not in_play_monitor) and cfg.get("inplay_monitor_enabled", True))))
                        monitor_timing_tasks.append((found_item, task))
                    elif racing_monitor:
                        # A Racing opportunity has already been recorded as qualified.
                        # If the actual configured MONITOR scenario cannot be created,
                        # persist a terminal attempt instead of leaving an unresolved
                        # qualification that would block every future retry forever.
                        simulation_reason = str(monitor_timing_sim.get("reason") or "Configured MONITOR scenario is not executable")
                        execution_id = self.db.add_execution_run(
                            oid, mode="sim", execution_type="modeled_racing_monitor", state="MONITOR_MISSED",
                            deployed=0.0, expected_profit=0.0, captured_profit=0.0, max_unhedged_exposure=0.0,
                            details={
                                "first_failure_reason": "CONFIGURATION_REJECTION",
                                "simulation_reason": simulation_reason,
                                "monitor_stream": "racing",
                                "book_revision": str(book_revision or sig),
                                "timing_evidence": timing_evidence,
                                "live_order_placement": False,
                            },
                            is_real=False,
                        )
                        found_item["monitor_timing_execution"] = {
                            "mode": "sim", "monitor_stream": "racing", "live_order_placement": False,
                            "state": "MONITOR_MISSED", "monitor_execution_id": execution_id,
                            "first_failure_reason": "CONFIGURATION_REJECTION", "reason": simulation_reason,
                        }
                except Exception as monitor_timing_exc:
                    found_item["monitor_timing_execution"] = {"mode": "sim", "live_order_placement": False, "error": str(monitor_timing_exc)}
                    if racing_monitor and oid not in monitor_timing_contexts:
                        # Fail closed and leave durable forensic evidence.  Do not
                        # leave a qualified Racing opportunity in an unresolved state.
                        execution_id = self.db.add_execution_run(
                            oid, mode="sim", execution_type="modeled_racing_monitor", state="MONITOR_FAILED",
                            deployed=0.0, expected_profit=0.0, captured_profit=0.0, max_unhedged_exposure=0.0,
                            details={
                                "first_failure_reason": "OTHER",
                                "error": str(monitor_timing_exc),
                                "monitor_stream": "racing",
                                "book_revision": str(book_revision or sig),
                                "timing_evidence": timing_evidence,
                                "live_order_placement": False,
                            },
                            is_real=False,
                        )
                        found_item["monitor_timing_execution"].update({
                            "state": "MONITOR_FAILED", "monitor_execution_id": execution_id,
                            "first_failure_reason": "OTHER",
                        })
            found.append(found_item)

        in_play_failures = {"EVENT_STARTED", "BETFAIR_IN_PLAY", "MATCHBOOK_IN_PLAY", "BOTH_IN_PLAY"}
        for found_item, task in monitor_timing_tasks:
            try:
                found_item["monitor_timing_execution"] = await task
                first_failure = str((found_item.get("monitor_timing_execution") or {}).get("first_failure_reason") or "").upper()
                if first_failure in in_play_failures and str(found_item.get("section") or "sports") != "racing":
                    if bool(cfg.get("inplay_monitor_enabled", True)):
                        ctx = monitor_timing_contexts.get(int(found_item["id"]))
                        if ctx:
                            legs2, sim2, mm2 = ctx
                            cooldown_seconds = max(0.0, float(cfg.get("inplay_monitor_cooldown_seconds", 8.0) or 0.0))
                            if self.db.monitor_has_open_market(mm2.event_key, mm2.display_market, "in_play"):
                                found_item["qualification_status"] = "in_play_position_open"
                                found_item["monitor_stream"] = "in_play"
                                reason = "Fresh exchange state confirmed in-play, but an IN-PLAY Monitor position is already open for this event/market"
                                self.db.set_opportunity_qualification(found_item["id"], "in_play_position_open", reason, scan_id=scan_id)
                                continue
                            if self.db.recent_recommendation(mm2.event_key, mm2.display_market, mm2.sport, in_play=True, within_seconds=cooldown_seconds):
                                found_item["qualification_status"] = "in_play_cooldown"
                                found_item["monitor_stream"] = "in_play"
                                reason = f"Fresh exchange state confirmed in-play; waiting {cooldown_seconds:g}s after the previous IN-PLAY Monitor attempt"
                                self.db.set_opportunity_qualification(found_item["id"], "in_play_cooldown", reason, scan_id=scan_id)
                                continue
                            inplay_rules = stream_rules(True)
                            inplay_min_quality = inplay_rules.get("min_quality", "Tiny")
                            item_band = str((found_item.get("quality") or {}).get("quality_band") or "Invalid")
                            if quality_rank.get(item_band, -1) < quality_rank.get(inplay_min_quality, 0):
                                found_item["qualification_status"] = "below_quality"
                                found_item["monitor_stream"] = "in_play"
                                reason = f"Fresh exchange state confirmed in-play, but opportunity quality {item_band} is below the configured {inplay_min_quality} minimum"
                                self.db.set_opportunity_qualification(found_item["id"], "below_quality", reason, scan_id=scan_id)
                                continue
                            found_item["qualification_status"] = "in_play_qualified"
                            found_item["monitor_stream"] = "in_play"
                            reason = f"Fresh exchange state returned {first_failure.replace('_', ' ').title()}; moved to IN-PLAY Monitor stream"
                            self.db.set_opportunity_qualification(found_item["id"], "in_play_qualified", reason, scan_id=scan_id)
                            delay2 = {
                                "betfair": {"delay_ms": float(cfg.get("inplay_betfair_delay_ms", 5000) or 0), "adverse_odds_pct_per_second": float(cfg.get("inplay_adverse_odds_pct_per_second", 0.20) or 0), "liquidity_decay_pct_per_second": float(cfg.get("inplay_liquidity_decay_pct_per_second", 8.0) or 0)},
                                "matchbook": {"delay_ms": float(cfg.get("inplay_matchbook_delay_ms", 1000) or 0), "adverse_odds_pct_per_second": float(cfg.get("inplay_adverse_odds_pct_per_second", 0.20) or 0), "liquidity_decay_pct_per_second": float(cfg.get("inplay_liquidity_decay_pct_per_second", 8.0) or 0)},
                            }
                            inplay_sim = simulate_equal_return(legs2, Scenario("monitor-inplay-handoff", reference_bankroll, inplay_rules["reference_cap_pct"], inplay_rules["max_event_exposure_pct"]))
                            found_item["monitor_timing_execution"] = await run_monitor_timing_measurement(
                                opportunity_id=int(found_item["id"]), original_legs=legs2, original_simulation=inplay_sim if inplay_sim.get("executable") else sim2, adapters=adapters,
                                event_start=mm2.start_time, bankroll=reference_bankroll, max_bankroll_pct=inplay_rules["reference_cap_pct"],
                                max_event_exposure_pct=inplay_rules["max_event_exposure_pct"], min_roi=inplay_rules["min_roi"], min_profit=inplay_rules["min_profit"],
                                pre_match_only=False, reference_checkpoint_ms=int(cfg.get("monitor_timing_reference_checkpoint_ms", 250) or 250),
                                execution_checkpoint_ms=int(cfg.get("monitor_execution_checkpoint_ms", 500) or 500), hedge_checkpoint_ms=int(cfg.get("monitor_hedge_checkpoint_ms", 1000) or 1000),
                                event_key=mm2.event_key, market_name=mm2.display_market, hedge_reserve_pct=inplay_rules["hedge_reserve_pct"],
                                plan_ttl_ms=int(cfg.get("execution_plan_ttl_ms", 1500) or 1500), max_slippage_pct=inplay_rules["max_slippage_pct"],
                                max_unhedged_exposure=inplay_rules["max_unhedged_exposure"], balance_tolerance=float(cfg.get("execution_balance_tolerance", 0.10) or 0.0),
                                research_only=False, monitor_stream="in_play", delay_model_by_exchange=delay2,
                                scaled_entry_enabled=bool(scaled_entry_engine.get("enabled", False)),
                                scaled_entry_max_tranches=scaled_entry_engine.get("max_tranches", 3),
                                scaled_entry_tranche_size_mode=str(scaled_entry_engine.get("tranche_size_mode", "base") or "base"),
                                scaled_entry_tranche_size=float(scaled_entry_engine.get("tranche_size", 0.0) or 0.0),
                                scaled_entry_max_total_stake=float(scaled_entry_engine.get("max_total_stake", 100.0) or 0.0),
                                scaled_entry_min_net_edge=float(scaled_entry_engine.get("min_net_edge", 1.0) or 0.0),
                                scaled_entry_min_depth_multiplier=float(scaled_entry_engine.get("min_depth_multiplier", 1.25) or 1.0),
                                scaled_entry_recheck_delay_ms=int(scaled_entry_engine.get("recheck_delay_ms", 100) or 0),
                                scaled_entry_global_bankroll_pct=configured_bankroll_pct)
                    else:
                        found_item["qualification_status"] = "in_play_research"
                        reason = f"In-play research only — fresh exchange state returned {first_failure.replace('_', ' ').title()}"
                        self.db.set_opportunity_qualification(found_item["id"], "in_play_research", reason, scan_id=scan_id)
            except Exception as monitor_timing_exc:
                found_item["monitor_timing_execution"] = {"mode": "sim", "live_order_placement": False, "error": str(monitor_timing_exc)}
        # Alerts/tracks are SIM Monitor evidence. LIVE-data simulation has its own
        # isolated persistence and must not mutate SIM recommendation history.
        if application_mode != "live":
            for item in found:
                if item.get("qualification_status") != "qualified":
                    continue
                try:
                    self._maybe_alert(str(item.pop("_alert_track_key", "")), item.get("event") or "", item.get("market") or "", item.get("quality") or {}, bool(item.pop("_alert_delayed", False)), cfg)
                except Exception:
                    item.pop("_alert_track_key", None); item.pop("_alert_delayed", None)
            for item in found:
                item.pop("_alert_track_key", None); item.pop("_alert_delayed", None)
            if statuses and all(bool(x.get("ok")) for x in statuses):
                self.db.close_tracks_not_seen(scan_id, seen_track_keys)
        qualified_statuses = {"qualified", "in_play_qualified", "racing_qualified"}
        if application_mode == "live":
            qualified_count = sum(1 for item in found if bool(((item.get("live_decision") or {}).get("qualification") or {}).get("strategy_risk_pass")))
            executed_count = 0
        else:
            qualified_count = sum(1 for item in found if item.get("qualification_status") in qualified_statuses)
            executed_count = sum(1 for item in found if item.get("qualification_status") in qualified_statuses and bool((item.get("monitor_timing_execution") or {}).get("monitor_opened")))
        stage = dict(stage_timings or {})
        stage["evaluation_ms"] = int((time.perf_counter() - eval_started) * 1000)
        duration_ms = int(max(0, int(stage.get("targeted_fetch_ms") or 0)) + max(0, int(stage.get("snapshot_write_ms") or 0)) + max(0, int(stage.get("evaluation_ms") or 0)))
        self.db.finish_scan(scan_id, fetched_count, len(matches), len(found), statuses=statuses,
            processed_candidates=processed_candidates, positive_opportunities=positive_opportunities,
            qualified_count=qualified_count, executed_count=executed_count, duration_ms=duration_ms,
            stage_timings=stage, cache_entries=cache_entries, stale_rejections=stale_rejections)
        qualification_breakdown = self.db.qualification_breakdown_for_scan(scan_id)
        return {"ok": True, "kind": "price", "application_mode": application_mode, "decision_evidence": bool(live_decision_enabled), "markets": fetched_count, "matches": len(matches), "found": found, "statuses": statuses,
            "pipeline": {"fetched": fetched_count, "matched": len(matches), "processed": processed_candidates,
                "opportunities": positive_opportunities, "qualified": qualified_count, "executed": executed_count,
                "in_play_research": int(qualification_breakdown.get("in_play_research", 0) or 0),
                "in_play_qualified": int(qualification_breakdown.get("in_play_monitor", 0) or qualification_breakdown.get("in_play_qualified", 0) or 0),
                "racing_qualified": int(qualification_breakdown.get("racing_qualified", 0) or 0),
                "qualification_breakdown": qualification_breakdown,
                "duration_ms": duration_ms, "stale_rejections": stale_rejections}, "stage_timings": stage}

    async def price_scan_once_async(self, job_id: int | None = None, *, force: bool = False, data_context_mode: str | None = None) -> dict:
        cfg = self._settings()
        scan_mode = canonical_mode_value(data_context_mode or self._application_data_mode())
        adapters = self._adapters(scan_mode)
        cache_rows = self.db.active_market_cache(due_at=None if force else utc_now_iso(), limit=int(cfg.get("price_scan_cache_limit", 1000) or 1000))
        if not cache_rows:
            return {"ok": True, "kind": "price", "skipped": True, "markets": 0, "matches": 0, "found": [], "statuses": [],
                "pipeline": {"fetched": 0, "matched": 0, "processed": 0, "opportunities": 0, "qualified": 0, "executed": 0, "duration_ms": 0},
                "message": "No matched markets are due for a price refresh."}
        scan_id = self.db.start_scan(job_id=job_id, scan_kind="price")
        started = time.perf_counter()
        if len(adapters) < 2:
            controls = {x["provider_id"]: x for x in self.db.venue_controls()}
            flag = "live_feed_enabled" if scan_mode == "live" else "sim_feed_enabled"
            disabled = [spec.venue.venue_name for spec in self.provider_runtime.providers.all()
                        if not bool((controls.get(spec.provider_id) or {}).get(flag, False))]
            msg = (f"{scan_mode.upper()} feed disabled by operator: " + ", ".join(disabled)) if disabled else f"At least two enabled {scan_mode.upper()} market-data providers are required for arbitrage matching"
            statuses = [{"exchange": name, "ok": False, "enabled": False, "state": "disabled", "message": f"{scan_mode.upper()} feed disabled by operator"} for name in disabled]
            self.db.finish_scan(scan_id, statuses=statuses, error=msg, duration_ms=int((time.perf_counter() - started) * 1000), cache_entries=len(cache_rows))
            return {"ok": False, "message": msg, "statuses": statuses, "kind": "price"}
        statuses: list[dict] = []
        stage: dict = {}
        try:
            matches, statuses, stage, stale_rejections = await self._refresh_cached_matches(cache_rows, adapters, cfg)
            # fetched = targeted venue market states, which is distinct from slow-discovery market count.
            fetched_count = sum(int(x.get("requested") or x.get("markets") or 0) for x in statuses)
            result = await self._evaluate_matches_async(scan_id=scan_id, matches=matches, adapters=adapters, statuses=statuses, cfg=cfg,
                fetched_count=fetched_count, stage_timings=stage, cache_entries=len(cache_rows), stale_rejections=stale_rejections, job_id=job_id,
                application_mode=data_context_mode)
            result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.db.rollback_if_needed()
            self.db.finish_scan(scan_id, statuses=statuses, error=str(exc), duration_ms=duration_ms, stage_timings=stage)
            return {"ok": False, "kind": "price", "message": str(exc), "statuses": statuses,
                    "pipeline": {"duration_ms": duration_ms}, "stage_timings": stage}

    def price_scan_once(self, job_id: int | None = None, *, force: bool = False, data_context_mode: str | None = None) -> dict:
        return asyncio.run(self.price_scan_once_async(job_id=job_id, force=force, data_context_mode=data_context_mode))

    async def scan_once_async(self, job_id: int | None = None, *, data_context_mode: str | None = None) -> dict:
        """Manual scan: refresh discovery first, then run one forced price scan.

        ``data_context_mode`` snapshots the operator context at invocation so a
        later UI mode change cannot redirect an in-flight manual scan into the
        other economic/evidence sink. It never unlocks LIVE execution.
        """
        frozen_context = canonical_mode_value(data_context_mode or self._application_data_mode())
        discovery = await self.discover_once_async(job_id=job_id, data_context_mode=frozen_context)
        price = await self.price_scan_once_async(job_id=job_id, force=True, data_context_mode=frozen_context)
        price["discovery"] = discovery
        return price

    def scan_once(self, job_id: int | None = None, *, data_context_mode: str | None = None) -> dict:
        return asyncio.run(self.scan_once_async(job_id=job_id, data_context_mode=data_context_mode))

    async def test_connections_async(self) -> list[dict]:
        adapters = self._adapters()
        if not adapters:
            return []
        statuses = list(await asyncio.gather(*(a.health() for a in adapters)))
        self._persist_refreshed_sessions(adapters)
        return statuses

    def test_connections(self) -> list[dict]:
        return asyncio.run(self.test_connections_async())

    @staticmethod
    def _resolve_settlement_winner(result: dict, opp: dict, source: dict, *, fuzzy_threshold: float = 0.94, fuzzy_margin: float = 0.03) -> dict:
        """Map a provider result to a stored position outcome without guessing."""
        raw_winner = str((result or {}).get("winner") or "").strip()
        winner_id = str((result or {}).get("winner_id") or "").strip()
        try:
            legs = json.loads(opp.get("legs_json") or "[]")
        except Exception:
            legs = []
        stored = [{
            "selection": str(l.get("selection") or ""),
            "selection_id": str(l.get("selection_id") or ""),
            "canonical_selection_id": str(l.get("canonical_selection_id") or ""),
            "canonical_selection_key": str(l.get("canonical_selection_key") or ""),
            "exchange": str(l.get("exchange") or ""),
            "venue_id": str(l.get("venue_id") or ""),
        } for l in legs]
        def success(row, method, confidence, canonical=None):
            venue = str(row.get("venue_id") or row.get("exchange") or "")
            return {"ok": True, "winner": row.get("selection"), "mapping_method": method, "mapping_confidence": float(confidence),
                    "provider_winner_id": winner_id or None, "raw_provider_winner": raw_winner or None,
                    "canonical_winner": canonical or row.get("canonical_selection_key") or row.get("canonical_selection_id") or row.get("selection"),
                    "winning_exchange": venue, "stored_selections": stored}
        if winner_id:
            matches = [r for r in stored if winner_id in {r.get("selection_id"), r.get("canonical_selection_id")} and winner_id]
            if len(matches) == 1:
                return success(matches[0], "provider_selection_id", 1.0)
        source_keys = source.get("runner_keys") or {}
        canonical = str(source_keys.get(winner_id) or "").strip() if winner_id else ""
        if canonical:
            matches = [r for r in stored if canonical in {r.get("canonical_selection_key"), r.get("canonical_selection_id")} and canonical]
            if len(matches) == 1:
                return success(matches[0], "canonical_selection", 1.0, canonical)
        raw_norm = norm_text(raw_winner)
        if raw_norm:
            matches = [r for r in stored if norm_text(r.get("selection")) == raw_norm]
            if len(matches) == 1:
                return success(matches[0], "normalised_name_exact", 1.0)
        alias_norm = norm_selection(raw_winner)
        if alias_norm:
            matches = [r for r in stored if norm_selection(r.get("selection")) == alias_norm]
            if len(matches) == 1:
                return success(matches[0], "controlled_alias", 0.99)
        scored = sorted(
            ((SequenceMatcher(None, norm_text(r.get("selection")), raw_norm).ratio(), r) for r in stored if norm_text(r.get("selection")) and raw_norm),
            key=lambda x: x[0], reverse=True,
        )
        if scored:
            best_score, best_row = scored[0]
            second = scored[1][0] if len(scored) > 1 else 0.0
            if best_score >= float(fuzzy_threshold) and (best_score - second) >= float(fuzzy_margin):
                return success(best_row, "fuzzy_high_confidence", best_score)
        return {
            "ok": False, "code": "SETTLEMENT_MAPPING_ERROR", "raw_provider_winner": raw_winner or None,
            "provider_winner_id": winner_id or None, "canonical_winner": canonical or None, "stored_selections": stored,
            "mapping_method": "unresolved", "mapping_confidence": (scored[0][0] if scored else 0.0),
            "reason": "Provider winner could not be mapped to exactly one stored selection with sufficient confidence",
        }

    async def settle_once_async(self) -> dict:
        cfg = self._settings()
        bf = BetfairDelayedAdapter(app_key=self.secrets.get("betfair_app_key"),
                                   session_token=self.secrets.get("betfair_session_token"),
                                   commission_pct=float(cfg.get("betfair_commission_pct", 2.0)),
                                   enabled_sports=enabled_sports_from_config(cfg), live_lookback_hours=int(cfg.get("live_lookback_hours", 8)))
        if not bf.app_key or not bf.session_token:
            return {"ok": False, "message": "Betfair delayed credentials not configured", "settled": 0}
        settled, errors = 0, []
        for opp in self.db.unresolved_opportunities(limit=100):
            try:
                sources = json.loads(opp.get("source_markets_json") or "[]")
                source = next((s for s in sources if str(s.get("exchange", "")).lower().startswith("betfair")), None)
                if not source:
                    continue
                result = await bf.market_result(str(source["market_id"]), source.get("runner_names") or {})
                if not result:
                    continue
                mapping = self._resolve_settlement_winner(result, opp, source)
                if not mapping.get("ok"):
                    self.db.record_settlement_audit(
                        int(opp["id"]), status="SETTLEMENT_MAPPING_ERROR",
                        raw_provider_winner=mapping.get("raw_provider_winner"), provider_winner_id=mapping.get("provider_winner_id"),
                        canonical_winner=mapping.get("canonical_winner"), stored_selections=mapping.get("stored_selections"),
                        mapping_method=mapping.get("mapping_method"), mapping_confidence=mapping.get("mapping_confidence"),
                        reconciliation_status="NOT_SETTLED", details={"reason": mapping.get("reason"), "source_market_id": source.get("market_id")},
                    )
                    errors.append({"opportunity_id": opp.get("id"), "error": "SETTLEMENT_MAPPING_ERROR", "detail": mapping.get("reason")})
                    continue
                winner = str(mapping["winner"])
                canonical = self.db.settle_canonical_lifecycle(
                    int(opp["id"]), winner,
                    notes=f"Settled from Betfair delayed market {source['market_id']} via {mapping.get('mapping_method')}",
                )
                monitor_result = canonical.get("monitor") if isinstance(canonical, dict) else None
                if not canonical.get("ok"):
                    detail = monitor_result or canonical
                    self.db.record_settlement_audit(
                        int(opp["id"]), status="SETTLEMENT_RECONCILIATION_ERROR",
                        raw_provider_winner=mapping.get("raw_provider_winner"), provider_winner_id=mapping.get("provider_winner_id"),
                        canonical_winner=winner, stored_selections=mapping.get("stored_selections"), mapping_method=mapping.get("mapping_method"),
                        mapping_confidence=mapping.get("mapping_confidence"), winning_exchange=mapping.get("winning_exchange"),
                        settlement_contributions=(monitor_result or {}).get("stored_net_by_exchange") or {},
                        reconciliation_status="ERROR", reconciliation_delta=(monitor_result or {}).get("reconciliation_delta"), details=detail,
                    )
                    errors.append({"opportunity_id": opp.get("id"), "error": "SETTLEMENT_RECONCILIATION_ERROR", "detail": detail})
                    continue
                contributions = (monitor_result or {}).get("by_exchange") or {}
                total = (monitor_result or {}).get("realized_pnl")
                delta = (monitor_result or {}).get("reconciliation_delta", 0.0)
                self.db.record_settlement_audit(
                    int(opp["id"]), status="SETTLED", raw_provider_winner=mapping.get("raw_provider_winner"),
                    provider_winner_id=mapping.get("provider_winner_id"), canonical_winner=winner,
                    stored_selections=mapping.get("stored_selections"), mapping_method=mapping.get("mapping_method"),
                    mapping_confidence=mapping.get("mapping_confidence"), winning_exchange=mapping.get("winning_exchange"),
                    settlement_contributions=contributions, total_realized_pnl=total,
                    reconciliation_status=(monitor_result or {}).get("reconciliation_status") or "OK", reconciliation_delta=delta,
                    details={
                        "source_market_id": source.get("market_id"),
                        "gross_by_exchange": (monitor_result or {}).get("gross_by_exchange") or {},
                        "commission_by_exchange": (monitor_result or {}).get("commission_by_exchange") or {},
                        "net_by_exchange": (monitor_result or {}).get("model_net_by_exchange") or contributions,
                    },
                )
                settled += 1
            except Exception as e:
                self.db.rollback_if_needed()
                errors.append({"opportunity_id": opp.get("id"), "error": str(e)})
        return {"ok": not errors, "settled": settled, "errors": errors}

    def settle_once(self) -> dict:
        return asyncio.run(self.settle_once_async())

    async def keepalive_betfair_async(self) -> dict:
        cfg = self._settings()
        bf = BetfairDelayedAdapter(app_key=self.secrets.get("betfair_app_key"),
                                   session_token=self.secrets.get("betfair_session_token"),
                                   commission_pct=float(cfg.get("betfair_commission_pct", 2.0)),
                                   enabled_sports=enabled_sports_from_config(cfg), live_lookback_hours=int(cfg.get("live_lookback_hours", 8)))
        if not bf.app_key or not bf.session_token:
            return {"ok": False, "message": "Betfair delayed credentials not configured"}
        try:
            data = await bf.keep_alive()
            return {"ok": True, "message": "Betfair session refreshed", "response": data}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def keepalive_betfair(self) -> dict:
        return asyncio.run(self.keepalive_betfair_async())
