from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping

from .engine import simulate_equal_return
from .models import Leg, Scenario
from .execution import (
    HedgeQuote,
    OrderSide,
    PaperExecutionCoordinator,
    build_execution_plan,
    capital_required_by_exchange_from_fills,
    exchange_key,
    exchange_outcome_pnls_from_fills,
    fit_simulation_to_wallets,
    order_capital_required,
    position_snapshot,
    scale_simulation,
)


DEFAULT_CHECKPOINTS_MS = (100, 250, 500, 1000)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scaled_entry_max_tranches(value: Any) -> int | None:
    """Return a positive tranche ceiling, or None for risk-limited unlimited mode."""
    if isinstance(value, str) and value.strip().lower() == "unlimited":
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 3


def _scale_to_target_deployed(simulation: Mapping[str, Any], target_deployed: float, *, total_bankroll: float) -> dict[str, Any]:
    """Scale a linear BACK simulation to an exact tranche target.

    ``execution.scale_simulation`` intentionally only scales down for wallet
    fitting. Scaled-entry execution also needs to express a configured tranche larger than the
    base tranche before fresh depth is checked, so the upward branch keeps the
    same economics and scales all linear monetary fields explicitly.
    """
    current = max(0.0, float(simulation.get("deployed") or 0.0))
    target = max(0.0, float(target_deployed or 0.0))
    if current <= 0.0 or target <= 0.0:
        return scale_simulation(simulation, 0.0, total_bankroll=total_bankroll)
    factor = target / current
    if factor <= 1.0:
        return scale_simulation(simulation, factor, total_bankroll=total_bankroll)
    out = dict(simulation)
    for key in ("deployed", "expected_profit", "gross_profit", "commission_cost", "net_pnl_spread"):
        if key in out and out.get(key) is not None:
            out[key] = round(float(out.get(key) or 0.0) * factor, 8)
    out["stakes"] = [{**row, "stake": round(float(row.get("stake") or 0.0) * factor, 8)} for row in (simulation.get("stakes") or [])]
    for key in ("outcome_pnls", "gross_outcome_pnls", "commission_by_outcome"):
        if key in simulation:
            out[key] = {str(k): round(float(v or 0.0) * factor, 8) for k, v in (simulation.get(key) or {}).items()}
    for key in ("commission_by_outcome_exchange", "gross_by_outcome_exchange", "net_by_outcome_exchange"):
        if key in simulation:
            out[key] = {
                str(outcome): {str(exchange): round(float(value or 0.0) * factor, 8) for exchange, value in (values or {}).items()}
                for outcome, values in (simulation.get(key) or {}).items()
            }
    if total_bankroll and total_bankroll > 0:
        out["bankroll_roi_pct"] = round((float(out.get("expected_profit") or 0.0) / total_bankroll) * 100.0, 6)
        out["capital_used_pct"] = round((float(out.get("deployed") or 0.0) / total_bankroll) * 100.0, 6)
    out["wallet_scale_factor"] = round(factor, 8)
    return out


def _subtract_paper_consumed_depth(
    states: Mapping[tuple[str, str], Mapping[str, Any]],
    consumed: Mapping[tuple[str, str, str, float], float],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Conservatively remove paper-consumed top-price depth from fresh exchange reads.

    Monitor fills do not alter the real exchange book, so a fresh API read can still
    advertise the same top-level quantity. SUPERBET must not reuse that quantity.
    We only subtract when the refreshed top price is the same price previously used;
    a changed price is treated as a genuinely new executable quote.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for key, raw in states.items():
        state = dict(raw or {})
        quotes = {}
        for selection_id, raw_quote in (state.get("quotes") or {}).items():
            q = dict(raw_quote or {})
            try:
                odds = round(float(q.get("odds") or 0.0), 8)
                liquidity = max(0.0, float(q.get("liquidity") or 0.0))
            except (TypeError, ValueError):
                odds, liquidity = 0.0, 0.0
            used = max(0.0, float(consumed.get((str(key[0]), str(key[1]), str(selection_id), odds), 0.0) or 0.0))
            if used > 0.0:
                q["paper_consumed_depth"] = round(used, 8)
                q["liquidity"] = max(0.0, liquidity - used)
            quotes[str(selection_id)] = q
        state["quotes"] = quotes
        out[(str(key[0]), str(key[1]))] = state
    return out


def _depth_multiplier_ok(observation: Mapping[str, Any], multiplier: float) -> tuple[bool, str | None]:
    multiplier = max(1.0, float(multiplier or 1.0))
    for row in observation.get("quotes") or []:
        planned = max(0.0, float(row.get("planned_stake") or 0.0))
        liquidity = max(0.0, float(row.get("liquidity") or 0.0))
        if planned > 0.0 and liquidity + 1e-9 < planned * multiplier:
            return False, str(row.get("selection") or row.get("selection_id") or "runner")
    return True, None


def _planned_stakes(simulation: Mapping[str, Any]) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    for row in simulation.get("stakes") or []:
        key = (
            str(row.get("exchange") or ""),
            str(row.get("market_id") or ""),
            str(row.get("selection_id") or row.get("selection") or ""),
        )
        out[key] = float(row.get("stake") or 0.0)
    return out


def _failure_reason(
    *,
    venue_error: bool,
    suspended: bool,
    event_started: bool,
    start_status_unconfirmed: bool,
    missing_quote: bool,
    full_stake_available: bool,
    simulation: Mapping[str, Any],
    min_roi: float,
    min_profit: float,
) -> str | None:
    if venue_error:
        return "VENUE_ERROR"
    if suspended:
        return "MARKET_SUSPENDED"
    if event_started:
        return "EVENT_STARTED"
    if start_status_unconfirmed:
        return "START_STATUS_UNCONFIRMED"
    if missing_quote:
        return "QUOTE_UNAVAILABLE"
    if not full_stake_available:
        return "INSUFFICIENT_LIQUIDITY"
    if not simulation.get("executable"):
        reason = str(simulation.get("reason") or "").lower()
        if "arbitrage" in reason or "commission" in reason:
            return "PRICE_MOVED"
        return "NOT_EXECUTABLE"
    roi = float(simulation.get("expected_roi_pct") or 0.0)
    profit = float(simulation.get("expected_profit") or 0.0)
    if roi + 1e-12 < float(min_roi) or profit + 1e-12 < float(min_profit):
        return "BELOW_THRESHOLD"
    return None


def evaluate_observation(
    original_legs: list[Leg],
    original_simulation: Mapping[str, Any],
    market_states: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    bankroll: float,
    max_bankroll_pct: float,
    max_event_exposure_pct: float,
    min_roi: float,
    min_profit: float,
    pre_match_only: bool = True,
    scheduled_start_passed: bool = False,
) -> dict[str, Any]:
    """Evaluate fresh quote state against the original intended monitor_timing plan.

    The fresh simulation is allowed to rebalance stakes at the new prices, but the
    observation only counts as fully executable when the original planned stake is
    still available on every selected leg. This avoids calling a £1 remnant a
    surviving £25 opportunity.
    """
    planned = _planned_stakes(original_simulation)
    fresh_legs: list[Leg] = []
    quote_rows: list[dict[str, Any]] = []
    venue_rows: list[dict[str, Any]] = []
    missing_quote = False
    suspended = False
    event_started = False
    start_status_unconfirmed = False
    venue_error = False
    authoritative_in_play_flags: list[bool] = []
    in_play_exchanges: list[str] = []
    coverage_fractions: list[float] = []

    for key, state in market_states.items():
        captured_at = state.get("captured_at") or _utc_now()
        quote_age_seconds = None
        try:
            captured_dt = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
            if captured_dt.tzinfo is None:
                captured_dt = captured_dt.replace(tzinfo=timezone.utc)
            quote_age_seconds = max(0.0, (datetime.now(timezone.utc) - captured_dt.astimezone(timezone.utc)).total_seconds())
        except Exception:
            pass
        venue_rows.append({
            "exchange": key[0],
            "market_id": key[1],
            "ok": bool(state.get("ok", True)),
            "status": state.get("status"),
            "in_play": state.get("in_play"),
            "latency_ms": int(state.get("latency_ms") or 0),
            "captured_at": captured_at,
            "quote_age_seconds": None if quote_age_seconds is None else round(quote_age_seconds, 4),
            "error": state.get("error"),
        })
        if not state.get("ok", True):
            venue_error = True
        status = str(state.get("status") or "OPEN").upper()
        if status not in {"OPEN", "ACTIVE"}:
            suspended = True
        if state.get("in_play") is True:
            authoritative_in_play_flags.append(True)
            in_play_exchanges.append(str(key[0]))
        elif state.get("in_play") is False:
            authoritative_in_play_flags.append(False)
        if pre_match_only and state.get("in_play") is True:
            event_started = True

    # Scheduled start is only a fallback when no exchange has given us an
    # authoritative in-play state. An explicit False from either venue confirms
    # that the market is still pre-match even if the advertised start time has
    # passed (important for delayed starts such as tennis).
    if (
        pre_match_only
        and scheduled_start_passed
        and not event_started
        and not authoritative_in_play_flags
    ):
        start_status_unconfirmed = True

    for leg in original_legs:
        state = market_states.get((str(leg.exchange), str(leg.market_id or ""))) or {}
        quote_map = state.get("quotes") or {}
        q = quote_map.get(str(leg.selection_id or ""))
        if not q:
            missing_quote = True
            continue
        try:
            odds = float(q.get("odds") or 0.0)
            liquidity = max(0.0, float(q.get("liquidity") or 0.0))
        except (TypeError, ValueError):
            missing_quote = True
            continue
        if odds <= 1.0:
            missing_quote = True
            continue
        fresh = Leg(**{**asdict(leg),
                       "odds": odds,
                       "liquidity": liquidity,
                       "captured_at": state.get("captured_at") or _utc_now(),
                       "source_latency_ms": int(state.get("latency_ms") or 0),
                       "in_play": state.get("in_play"),
                       "market_status": state.get("status")})
        fresh_legs.append(fresh)
        pkey = (str(leg.exchange), str(leg.market_id or ""), str(leg.selection_id or leg.selection or ""))
        requested = max(0.0, float(planned.get(pkey, 0.0)))
        fraction = 1.0 if requested <= 0 else min(1.0, liquidity / requested)
        coverage_fractions.append(fraction)
        quote_rows.append({
            "exchange": leg.exchange,
            "market_id": leg.market_id,
            "selection": leg.selection,
            "selection_id": leg.selection_id,
            "initial_odds": round(float(leg.odds), 6),
            "odds": round(odds, 6),
            "liquidity": round(liquidity, 4),
            "planned_stake": round(requested, 4),
            "stake_coverage_pct": round(fraction * 100.0, 2),
        })

    if len(fresh_legs) == len(original_legs) and not venue_error and not suspended and not event_started and not start_status_unconfirmed:
        simulation = simulate_equal_return(
            fresh_legs,
            Scenario("monitor_timing-timed", float(bankroll), float(max_bankroll_pct), float(max_event_exposure_pct)),
        )
    else:
        simulation = {"executable": False, "reason": "Fresh market state incomplete"}

    executable_fraction = min(coverage_fractions, default=0.0)
    full_stake_available = bool(coverage_fractions) and executable_fraction >= 0.999999 and len(fresh_legs) == len(original_legs)
    reason = _failure_reason(
        venue_error=venue_error,
        suspended=suspended,
        event_started=event_started,
        start_status_unconfirmed=start_status_unconfirmed,
        missing_quote=missing_quote,
        full_stake_available=full_stake_available,
        simulation=simulation,
        min_roi=min_roi,
        min_profit=min_profit,
    )
    if reason == "EVENT_STARTED" and in_play_exchanges:
        names = {str(x).lower() for x in in_play_exchanges}
        has_bf = any("betfair" in x for x in names)
        has_mb = any("matchbook" in x for x in names)
        if has_bf and has_mb:
            reason = "BOTH_IN_PLAY"
        elif has_bf:
            reason = "BETFAIR_IN_PLAY"
        elif has_mb:
            reason = "MATCHBOOK_IN_PLAY"
    still_profitable = bool(simulation.get("executable")) and float(simulation.get("expected_roi_pct") or 0.0) + 1e-12 >= float(min_roi) and float(simulation.get("expected_profit") or 0.0) + 1e-12 >= float(min_profit)
    still_executable = reason is None and full_stake_available and still_profitable
    return {
        "quotes": quote_rows,
        "venues": venue_rows,
        "simulation": dict(simulation),
        "deployed": round(float(simulation.get("deployed") or 0.0), 4),
        "expected_profit": round(float(simulation.get("expected_profit") or 0.0), 4),
        "expected_roi_pct": round(float(simulation.get("expected_roi_pct") or 0.0), 6),
        "executable_fraction": round(executable_fraction, 6),
        "full_stake_available": full_stake_available,
        "still_profitable": still_profitable,
        "still_executable": still_executable,
        "failure_reason": reason,
        "in_play_exchanges": in_play_exchanges,
        "start_status": (
            "IN_PLAY" if event_started else
            "UNCONFIRMED" if start_status_unconfirmed else
            "PRE_MATCH_CONFIRMED" if (pre_match_only and False in authoritative_in_play_flags) else
            "UNKNOWN"
        ),
    }


def _observation_at_or_after(observations: list[Mapping[str, Any]], target_ms: int) -> Mapping[str, Any] | None:
    later = [x for x in observations if int(x.get("offset_ms") or 0) >= int(target_ms)]
    if later:
        return min(later, key=lambda x: int(x.get("offset_ms") or 0))
    if observations:
        return max(observations, key=lambda x: int(x.get("offset_ms") or 0))
    return None


def _quote_map(observation: Mapping[str, Any] | None) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (str(q.get("exchange") or ""), str(q.get("selection") or "")): q
        for q in ((observation or {}).get("quotes") or [])
    }


def model_execution_inputs(plan, execution_observation: Mapping[str, Any] | None,
                           hedge_observation: Mapping[str, Any] | None,
                           delay_model_by_exchange: Mapping[str, Mapping[str, float]] | None = None) -> tuple[dict[int, float], dict[int, float], dict[str, HedgeQuote], list[dict[str, Any]]]:
    """Derive deterministic paper fills from measured post-detection quotes.

    There are deliberately no invented fill probabilities here. A normal leg is
    considered matchable only to the displayed depth seen at the execution
    checkpoint and only if the price remains within the configured slippage
    tolerance. Emergency hedge quotes use the later measured checkpoint.
    """
    execution_quotes = _quote_map(execution_observation)
    hedge_quotes_raw = _quote_map(hedge_observation)
    delay_model_by_exchange = dict(delay_model_by_exchange or {})

    def delayed_quote(exchange: str, odds: float, liquidity: float) -> tuple[float, float, dict[str, float]]:
        model = delay_model_by_exchange.get(exchange_key(exchange)) or delay_model_by_exchange.get(str(exchange)) or {}
        delay_ms = max(0.0, float(model.get("delay_ms") or 0.0))
        seconds = delay_ms / 1000.0
        adverse_pct = max(0.0, float(model.get("adverse_odds_pct_per_second") or 0.0)) * seconds
        liquidity_decay_pct = min(100.0, max(0.0, float(model.get("liquidity_decay_pct_per_second") or 0.0)) * seconds)
        adjusted_odds = max(1.01, float(odds) * (1.0 - adverse_pct / 100.0))
        adjusted_liquidity = max(0.0, float(liquidity) * (1.0 - liquidity_decay_pct / 100.0))
        return adjusted_odds, adjusted_liquidity, {
            "delay_ms": round(delay_ms, 2),
            "adverse_odds_pct": round(adverse_pct, 6),
            "liquidity_decay_pct": round(liquidity_decay_pct, 6),
        }

    fill_fractions: dict[int, float] = {}
    fill_odds: dict[int, float] = {}
    decisions: list[dict[str, Any]] = []

    for leg in plan.legs:
        q = execution_quotes.get((str(leg.exchange), str(leg.selection)))
        fraction = 0.0
        odds = float(leg.requested_odds)
        raw_quote_odds = None
        raw_liquidity = None
        delay_meta = {"delay_ms": 0.0, "adverse_odds_pct": 0.0, "liquidity_decay_pct": 0.0}
        reason = "QUOTE_UNAVAILABLE"
        if q:
            try:
                quote_odds = float(q.get("odds") or 0.0)
                liquidity = max(0.0, float(q.get("liquidity") or 0.0))
            except (TypeError, ValueError):
                quote_odds, liquidity = 0.0, 0.0
            raw_quote_odds, raw_liquidity = quote_odds, liquidity
            quote_odds, liquidity, delay_meta = delayed_quote(leg.exchange, quote_odds, liquidity)
            minimum_accepted_odds = float(leg.requested_odds) * (1.0 - max(0.0, float(leg.max_slippage_pct)) / 100.0)
            if quote_odds > 1.0 and quote_odds + 1e-12 >= minimum_accepted_odds:
                odds = quote_odds
                fraction = min(1.0, liquidity / max(1e-12, float(leg.requested_stake)))
                reason = "FULL" if fraction >= 0.999999 else ("PARTIAL_LIQUIDITY" if fraction > 0 else "NO_LIQUIDITY")
            elif quote_odds > 1.0:
                reason = "PRICE_OUTSIDE_SLIPPAGE"
        fill_fractions[int(leg.index)] = round(max(0.0, min(1.0, fraction)), 8)
        fill_odds[int(leg.index)] = round(max(1.01, odds), 8)
        decisions.append({
            "leg_index": int(leg.index),
            "exchange": leg.exchange,
            "selection": leg.selection,
            "requested_odds": round(float(leg.requested_odds), 6),
            "observed_odds": round(float(odds), 6),
            "raw_observed_odds": None if raw_quote_odds is None else round(float(raw_quote_odds), 6),
            "raw_observed_liquidity": None if raw_liquidity is None else round(float(raw_liquidity), 4),
            "delay_model": delay_meta,
            "requested_stake": round(float(leg.requested_stake), 4),
            "fill_fraction": round(float(fill_fractions[int(leg.index)]), 6),
            "reason": reason,
        })

    hedge_quotes: dict[str, HedgeQuote] = {}
    for leg in plan.legs:
        q = hedge_quotes_raw.get((str(leg.exchange), str(leg.selection)))
        if not q:
            continue
        try:
            odds = float(q.get("odds") or 0.0)
        except (TypeError, ValueError):
            odds = 0.0
        if odds <= 1.0:
            continue
        try:
            hedge_liquidity = max(0.0, float(q.get("liquidity") or 0.0))
        except (TypeError, ValueError):
            hedge_liquidity = 0.0
        odds, _unused_liquidity, _delay_meta = delayed_quote(leg.exchange, odds, hedge_liquidity)
        hedge_quotes[str(leg.selection)] = HedgeQuote(
            exchange=leg.exchange,
            selection=leg.selection,
            odds=odds,
            commission_pct=float(leg.commission_pct),
            side=OrderSide.BACK,
        )
    return fill_fractions, fill_odds, hedge_quotes, decisions


class MonitorTimingObserver:
    """Measure quote survival using fresh targeted market reads. Never places orders."""

    def __init__(self, db, checkpoints_ms: tuple[int, ...] = DEFAULT_CHECKPOINTS_MS):
        self.db = db
        self.checkpoints_ms = tuple(sorted({int(x) for x in checkpoints_ms if int(x) > 0}))

    async def observe(
        self,
        *,
        opportunity_id: int,
        original_legs: list[Leg],
        original_simulation: Mapping[str, Any],
        adapters: list[Any],
        event_start: str | None,
        bankroll: float,
        max_bankroll_pct: float,
        max_event_exposure_pct: float,
        min_roi: float,
        min_profit: float,
        pre_match_only: bool,
        reference_checkpoint_ms: int = 250,
        execution_checkpoint_ms: int = 500,
        hedge_checkpoint_ms: int = 1000,
        event_key: str | None = None,
        market_name: str | None = None,
        hedge_reserve_pct: float = 20.0,
        plan_ttl_ms: int = 1500,
        max_slippage_pct: float = 0.50,
        max_unhedged_exposure: float = 25.0,
        balance_tolerance: float = 0.10,
        research_only: bool = False,
        monitor_stream: str = "pre_match",
        delay_model_by_exchange: Mapping[str, Mapping[str, float]] | None = None,
        handoff_in_play: bool = False,
        scaled_entry_enabled: bool = False,
        scaled_entry_max_tranches: Any = 3,
        scaled_entry_tranche_size_mode: str = "base",
        scaled_entry_tranche_size: float = 0.0,
        scaled_entry_max_total_stake: float = 0.0,
        scaled_entry_min_net_edge: float = 1.0,
        scaled_entry_min_depth_multiplier: float = 1.0,
        scaled_entry_recheck_delay_ms: int = 100,
        scaled_entry_global_bankroll_pct: float | None = None,
    ) -> dict[str, Any]:
        started_at = _utc_now()
        planned_deployed = float(original_simulation.get("deployed") or 0.0)
        planned_profit = float(original_simulation.get("expected_profit") or 0.0)
        planned_roi = float(original_simulation.get("expected_roi_pct") or 0.0)
        qualification_snapshot = {
            "captured_at": started_at,
            "monitor_stream": str(monitor_stream or "pre_match"),
            "live_order_placement": False,
            "legs": [asdict(leg) for leg in original_legs],
            "stakes": list(original_simulation.get("stakes") or []),
            "outcome_pnls": dict(original_simulation.get("outcome_pnls") or {}),
            "gross_outcome_pnls": dict(original_simulation.get("gross_outcome_pnls") or {}),
            "commission_by_outcome": dict(original_simulation.get("commission_by_outcome") or {}),
            "deployed": planned_deployed,
            "expected_profit": planned_profit,
            "expected_roi_pct": planned_roi,
            "limited_by": original_simulation.get("limited_by"),
            "limiting_leg": original_simulation.get("limiting_leg"),
        }
        run_id = self.db.start_monitor_timing_run(
            opportunity_id,
            started_at=started_at,
            initial_deployed=planned_deployed,
            initial_profit=planned_profit,
            initial_roi_pct=planned_roi,
            planned_stakes=original_simulation.get("stakes") or [],
            reference_checkpoint_ms=reference_checkpoint_ms,
            research_only=research_only,
            stream=monitor_stream,
        )
        # T0 records the quote state that triggered the opportunity. Fresh reads start
        # at the configured checkpoints.
        self.db.add_monitor_timing_observation(
            run_id,
            offset_ms=0,
            elapsed_ms=0,
            observed_at=started_at,
            fetch_latency_ms=0,
            deployed=planned_deployed,
            expected_profit=planned_profit,
            expected_roi_pct=planned_roi,
            executable_fraction=1.0,
            full_stake_available=True,
            still_profitable=True,
            still_executable=True,
            failure_reason=None,
            quotes=[{**asdict(l), "planned_stake": next((float(x.get("stake") or 0.0) for x in (original_simulation.get("stakes") or []) if str(x.get("exchange")) == str(l.exchange) and str(x.get("selection_id") or x.get("selection")) == str(l.selection_id or l.selection)), 0.0)} for l in original_legs],
            venues=[],
        )

        adapter_by_name = {str(a.name): a for a in adapters}
        market_keys = sorted({(str(l.exchange), str(l.market_id or ""), str(l.event_id or "")) for l in original_legs})
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        observations: list[dict[str, Any]] = []

        async def fetch_states_once() -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
            request_started = loop.time()

            async def fetch_one(exchange: str, market_id: str, event_id: str):
                adapter = adapter_by_name.get(exchange)
                if adapter is None:
                    return (exchange, market_id), {"ok": False, "exchange": exchange, "market_id": market_id, "error": "Adapter unavailable", "latency_ms": 0, "quotes": {}}
                try:
                    state = await adapter.fetch_market_state(event_id=event_id, market_id=market_id)
                    return (exchange, market_id), state
                except Exception as exc:
                    return (exchange, market_id), {"ok": False, "exchange": exchange, "market_id": market_id, "error": str(exc), "latency_ms": 0, "quotes": {}}

            fetched = await asyncio.gather(*(fetch_one(ex, mid, eid) for ex, mid, eid in market_keys))
            return ({key: dict(state or {}) for key, state in fetched}, int((loop.time() - request_started) * 1000))

        def scheduled_start_is_past() -> bool:
            if not pre_match_only or not event_start:
                return False
            try:
                start_dt = datetime.fromisoformat(str(event_start).replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                return datetime.now(timezone.utc) >= start_dt.astimezone(timezone.utc)
            except Exception:
                return False

        for target_ms in self.checkpoints_ms:
            wait = (target_ms / 1000.0) - (loop.time() - t0)
            if wait > 0:
                await asyncio.sleep(wait)
            states, fetch_latency_ms = await fetch_states_once()
            elapsed_ms = int((loop.time() - t0) * 1000)

            # The scheduled start time is only a safety fallback. Exchange in-play
            # flags are authoritative: an explicit False keeps a delayed-start market
            # eligible; an explicit True rejects it. If the clock has passed the
            # advertised start and no venue provides any in-play state, we reject as
            # START_STATUS_UNCONFIRMED rather than guessing that the event is live.
            scheduled_start_passed = scheduled_start_is_past()

            result = evaluate_observation(
                original_legs,
                original_simulation,
                states,
                bankroll=bankroll,
                max_bankroll_pct=max_bankroll_pct,
                max_event_exposure_pct=max_event_exposure_pct,
                min_roi=min_roi,
                min_profit=min_profit,
                pre_match_only=pre_match_only,
                scheduled_start_passed=scheduled_start_passed,
            )
            row = {
                "offset_ms": target_ms,
                "elapsed_ms": elapsed_ms,
                "observed_at": _utc_now(),
                "fetch_latency_ms": fetch_latency_ms,
                **result,
            }
            observations.append(row)
            self.db.add_monitor_timing_observation(
                run_id,
                offset_ms=target_ms,
                elapsed_ms=elapsed_ms,
                observed_at=row["observed_at"],
                fetch_latency_ms=fetch_latency_ms,
                deployed=result["deployed"],
                expected_profit=result["expected_profit"],
                expected_roi_pct=result["expected_roi_pct"],
                executable_fraction=result["executable_fraction"],
                full_stake_available=result["full_stake_available"],
                still_profitable=result["still_profitable"],
                still_executable=result["still_executable"],
                failure_reason=result["failure_reason"],
                quotes=result["quotes"],
                venues=result["venues"],
            )

        by_offset = {int(x["offset_ms"]): x for x in observations}
        reference = by_offset.get(int(reference_checkpoint_ms))
        if reference is None and observations:
            reference = min(observations, key=lambda x: abs(int(x["offset_ms"]) - int(reference_checkpoint_ms)))
        captured_profit = float(reference.get("expected_profit") or 0.0) if reference and reference.get("still_executable") else 0.0
        captured_roi = float(reference.get("expected_roi_pct") or 0.0) if reference and reference.get("still_executable") else 0.0
        monitor_simulation = None
        monitor_opened = False
        monitor_reason = None
        monitor_execution_id = None
        monitor_execution_result = None
        execution_observation = _observation_at_or_after(
            observations,
            max(int(execution_checkpoint_ms), int(reference_checkpoint_ms) + 1),
        )
        execution_offset_ms = int((execution_observation or {}).get("offset_ms") or 0)
        hedge_observation = _observation_at_or_after(
            observations,
            max(int(hedge_checkpoint_ms), execution_offset_ms + 1),
        )
        hedge_offset_ms = int((hedge_observation or {}).get("offset_ms") or 0)

        scaled_entry_summary = {
            "enabled": bool(scaled_entry_enabled),
            "is_scaled_entry": False,
            "tranche_count": 0,
            "max_tranches": "unlimited" if _scaled_entry_max_tranches(scaled_entry_max_tranches) is None else _scaled_entry_max_tranches(scaled_entry_max_tranches),
            "tranche_size_mode": str(scaled_entry_tranche_size_mode or "base"),
            "configured_tranche_size": round(max(0.0, float(scaled_entry_tranche_size or 0.0)), 4),
            "max_total_stake": round(max(0.0, float(scaled_entry_max_total_stake or 0.0)), 4),
            "minimum_net_edge_pct": round(max(0.0, float(scaled_entry_min_net_edge or 0.0)), 6),
            "minimum_depth_multiplier": round(max(1.0, float(scaled_entry_min_depth_multiplier or 1.0)), 4),
            "stop_reason": "not_started",
            "tranches": [],
        }

        if (not research_only) and reference and reference.get("still_executable"):
            if self.db.monitor_has_open_market(event_key, market_name, monitor_stream):
                monitor_reason = "market_already_open"
                scaled_entry_summary["stop_reason"] = "market_already_open"
            else:
                wallet_snapshot = self.db.monitor_wallet_snapshot(hedge_reserve_pct, monitor_stream)
                free = {k: float(v.get("free_for_normal") or 0.0) for k, v in wallet_snapshot.items()}
                total_equity = sum(float(v.get("equity") or 0.0) for v in wallet_snapshot.values())
                monitor_simulation, limiting_exchange = fit_simulation_to_wallets(
                    reference.get("simulation") or {},
                    free,
                    total_bankroll=total_equity or bankroll,
                )
                if (
                    not monitor_simulation.get("executable")
                    or float(monitor_simulation.get("expected_profit") or 0.0) + 1e-12 < float(min_profit)
                    or float(monitor_simulation.get("expected_roi_pct") or 0.0) + 1e-12 < float(min_roi)
                ):
                    monitor_reason = (
                        f"exchange_balance:{limiting_exchange}"
                        if limiting_exchange
                        else "below_threshold_after_balance_limit"
                    )
                    scaled_entry_summary["stop_reason"] = "base_not_executable"
                else:
                    coordinator = PaperExecutionCoordinator(balance_tolerance=float(balance_tolerance))

                    def execute_tranche(
                        tranche_index: int,
                        tranche_simulation: Mapping[str, Any],
                        exec_observation: Mapping[str, Any] | None,
                        hedge_observation_row: Mapping[str, Any] | None,
                        cumulative_capital: Mapping[str, float],
                    ) -> dict[str, Any]:
                        reference_quotes = _quote_map(exec_observation)
                        fresh_ref_legs: list[Leg] = []
                        for leg in original_legs:
                            q = reference_quotes.get((str(leg.exchange), str(leg.selection))) or {}
                            fresh_ref_legs.append(
                                Leg(**{
                                    **asdict(leg),
                                    "odds": float(q.get("odds") or leg.odds),
                                    "liquidity": float(q.get("liquidity") or leg.liquidity),
                                })
                            )
                        plan = build_execution_plan(
                            fresh_ref_legs,
                            tranche_simulation,
                            opportunity_id=opportunity_id,
                            event_name=event_key or "",
                            market_name=market_name or "",
                            ttl_ms=int(plan_ttl_ms),
                            max_slippage_pct=float(max_slippage_pct),
                            max_unhedged_exposure=float(max_unhedged_exposure),
                            hedge_reserve_pct=float(hedge_reserve_pct),
                        )
                        fill_fractions, fill_odds, hedge_quotes, fill_decisions = model_execution_inputs(
                            plan,
                            exec_observation,
                            hedge_observation_row,
                            delay_model_by_exchange=delay_model_by_exchange,
                        )
                        initial_capital: dict[str, float] = {}
                        for leg in plan.legs:
                            fraction = max(0.0, min(1.0, float(fill_fractions.get(leg.index, 0.0))))
                            stake = float(leg.requested_stake) * fraction
                            if stake <= 0.0:
                                continue
                            odds = float(fill_odds.get(leg.index, leg.requested_odds))
                            key = exchange_key(leg.exchange)
                            initial_capital[key] = initial_capital.get(key, 0.0) + order_capital_required(leg.side, odds, stake)
                        hedge_capacity = {
                            key: max(
                                0.0,
                                float(wallet_snapshot.get(key, {}).get("available") or 0.0)
                                - float(cumulative_capital.get(key, 0.0) or 0.0)
                                - initial_capital.get(key, 0.0),
                            )
                            for key in wallet_snapshot
                        }
                        result = coordinator.execute(
                            plan,
                            fill_fractions=fill_fractions,
                            fill_odds=fill_odds,
                            hedge_quotes=hedge_quotes,
                            hedge_capital_by_exchange=hedge_capacity,
                            auto_hedge=True,
                        )
                        total_capital = capital_required_by_exchange_from_fills(result.fills)
                        normal_capital = capital_required_by_exchange_from_fills([x for x in result.fills if not x.is_hedge])
                        return {
                            "index": tranche_index,
                            "plan": plan,
                            "simulation": dict(tranche_simulation),
                            "result": result,
                            "total_capital": total_capital,
                            "normal_capital": normal_capital,
                            "fill_decisions": fill_decisions,
                            "hedge_capacity": {k: round(v, 4) for k, v in hedge_capacity.items()},
                            "fresh_snapshot": {
                                "observed_at": (exec_observation or {}).get("observed_at"),
                                "offset_ms": (exec_observation or {}).get("offset_ms"),
                                "quotes": list((exec_observation or {}).get("quotes") or []),
                                "venues": list((exec_observation or {}).get("venues") or []),
                                "expected_profit": (exec_observation or {}).get("expected_profit"),
                                "expected_roi_pct": (exec_observation or {}).get("expected_roi_pct"),
                                "executable_fraction": (exec_observation or {}).get("executable_fraction"),
                            },
                            "hedge_snapshot": {
                                "observed_at": (hedge_observation_row or {}).get("observed_at"),
                                "offset_ms": (hedge_observation_row or {}).get("offset_ms"),
                                "quotes": list((hedge_observation_row or {}).get("quotes") or []),
                                "venues": list((hedge_observation_row or {}).get("venues") or []),
                            },
                        }

                    base = execute_tranche(1, monitor_simulation, execution_observation, hedge_observation, {})
                    base_result = base["result"]
                    if not base_result.fills:
                        monitor_reason = "all_legs_failed"
                        scaled_entry_summary["stop_reason"] = "execution_failure"
                        monitor_execution_result = base_result.as_dict()
                        monitor_execution_id = self.db.add_execution_run(
                            opportunity_id,
                            mode="sim",
                            execution_type="modeled_inplay_monitor" if monitor_stream == "in_play" else ("modeled_racing_monitor" if monitor_stream == "racing" else "modeled_monitor"),
                            state="MONITOR_FAILED",
                            deployed=0.0,
                            expected_profit=float(monitor_simulation.get("expected_profit") or 0.0),
                            captured_profit=0.0,
                            max_unhedged_exposure=float(base_result.before_hedge.exposure_spread),
                            details={
                                "monitor_timing_run_id": run_id,
                                "reference_checkpoint_ms": int(reference_checkpoint_ms),
                                "execution_checkpoint_ms": execution_offset_ms,
                                "hedge_checkpoint_ms": hedge_offset_ms,
                                "fill_decisions": base["fill_decisions"],
                                "execution_result": monitor_execution_result,
                                "execution_model": "inplay_delay_adjusted_checkpoints" if monitor_stream == "in_play" else "measured_checkpoints",
                                "monitor_stream": monitor_stream,
                                "delay_model_by_exchange": dict(delay_model_by_exchange or {}),
                                "timed_rechecks": True,
                                "live_order_placement": False,
                                "qualification_snapshot": qualification_snapshot,
                                "scaled_entry": scaled_entry_summary,
                                "superbet": scaled_entry_summary,  # legacy read compatibility
                                "monitor_reason": monitor_reason,
                            },
                            is_real=False,
                            started_at=started_at,
                            finished_at=_utc_now(),
                        )
                    else:
                        tranches: list[dict[str, Any]] = [base]
                        all_fills = list(base_result.fills)
                        all_hedges = list(base_result.hedge_instructions)
                        all_events = [{**event, "tranche": 1} for event in base_result.events]
                        cumulative_total = dict(base["total_capital"])
                        cumulative_normal = dict(base["normal_capital"])
                        consumed_depth: dict[tuple[str, str, str, float], float] = {}

                        def record_consumed(tranche: Mapping[str, Any]) -> None:
                            plan = tranche["plan"]
                            for fill in tranche["result"].fills:
                                matching_leg = None
                                if fill.leg_index is not None:
                                    matching_leg = next((leg for leg in plan.legs if int(leg.index) == int(fill.leg_index)), None)
                                if matching_leg is None:
                                    matching_leg = next((leg for leg in plan.legs if str(leg.selection) == str(fill.selection) and str(leg.exchange) == str(fill.exchange)), None)
                                if matching_leg is None:
                                    continue
                                key = (
                                    str(matching_leg.exchange),
                                    str(matching_leg.market_id or ""),
                                    str(matching_leg.selection_id or ""),
                                    round(float(fill.odds), 8),
                                )
                                consumed_depth[key] = consumed_depth.get(key, 0.0) + max(0.0, float(fill.stake or 0.0))

                        record_consumed(base)
                        base_normal_stake = sum(float(v or 0.0) for v in base["normal_capital"].values())
                        mode = str(scaled_entry_tranche_size_mode or "base").strip().lower()
                        configured_size = max(0.0, float(scaled_entry_tranche_size or 0.0))
                        desired_extra_stake = base_normal_stake if mode != "fixed" or configured_size <= 0.0 else configured_size
                        tranche_template = _scale_to_target_deployed(monitor_simulation, desired_extra_stake, total_bankroll=total_equity or bankroll)
                        scaled_entry_summary["base_tranche_stake"] = round(base_normal_stake, 4)
                        scaled_entry_summary["effective_extra_tranche_stake"] = round(desired_extra_stake, 4)
                        max_tranches = _scaled_entry_max_tranches(scaled_entry_max_tranches)
                        stop_reason = "disabled" if not scaled_entry_enabled else None

                        base_locked = base_result.state.value in {"COMPLETE", "HEDGED"} and bool(base_result.after_hedge.balanced)
                        if scaled_entry_enabled and not base_locked:
                            stop_reason = "previous_tranche_unlocked"

                        while scaled_entry_enabled and base_locked and stop_reason is None:
                            next_index = len(tranches) + 1
                            if max_tranches is not None and next_index > max_tranches:
                                stop_reason = "max_tranches"
                                break
                            current_normal = sum(float(v or 0.0) for v in cumulative_normal.values())
                            max_total = max(0.0, float(scaled_entry_max_total_stake or 0.0))
                            if max_total > 0.0 and current_normal + desired_extra_stake > max_total + 1e-9:
                                stop_reason = "max_total_stake"
                                break
                            if desired_extra_stake <= 0.0:
                                stop_reason = "tranche_size_zero"
                                break
                            cumulative_deployed = sum(float(v or 0.0) for v in cumulative_total.values())
                            superbet_bankroll_pct = max_bankroll_pct if scaled_entry_global_bankroll_pct is None else scaled_entry_global_bankroll_pct
                            bankroll_cap = max(0.0, float(total_equity or bankroll)) * max(0.0, float(superbet_bankroll_pct or 0.0)) / 100.0
                            event_cap = max(0.0, float(total_equity or bankroll)) * max(0.0, float(max_event_exposure_pct or 0.0)) / 100.0
                            if bankroll_cap > 0.0 and cumulative_deployed + desired_extra_stake > bankroll_cap + 1e-9:
                                stop_reason = "bankroll_limit"
                                break
                            if event_cap > 0.0 and cumulative_deployed + desired_extra_stake > event_cap + 1e-9:
                                stop_reason = "exposure_limit"
                                break

                            delay_s = max(0.0, float(scaled_entry_recheck_delay_ms or 0.0)) / 1000.0
                            if delay_s:
                                await asyncio.sleep(delay_s)
                            fresh_states, fresh_latency = await fetch_states_once()
                            fresh_states = _subtract_paper_consumed_depth(fresh_states, consumed_depth)
                            tranche_cap_pct = max(
                                max(0.0, float(max_bankroll_pct or 0.0)),
                                (100.0 * desired_extra_stake / max(1e-12, float(bankroll))) if desired_extra_stake > 0.0 else 0.0,
                            )
                            if scaled_entry_global_bankroll_pct is not None:
                                tranche_cap_pct = min(tranche_cap_pct, max(0.0, float(scaled_entry_global_bankroll_pct or 0.0)))
                            fresh_eval = evaluate_observation(
                                original_legs,
                                tranche_template,
                                fresh_states,
                                bankroll=bankroll,
                                max_bankroll_pct=tranche_cap_pct,
                                max_event_exposure_pct=max_event_exposure_pct,
                                min_roi=max(float(min_roi), max(0.0, float(scaled_entry_min_net_edge or 0.0))),
                                min_profit=min_profit,
                                pre_match_only=pre_match_only,
                                scheduled_start_passed=scheduled_start_is_past(),
                            )
                            fresh_eval = {
                                "offset_ms": int((loop.time() - t0) * 1000),
                                "elapsed_ms": int((loop.time() - t0) * 1000),
                                "observed_at": _utc_now(),
                                "fetch_latency_ms": fresh_latency,
                                **fresh_eval,
                            }
                            depth_ok, depth_runner = _depth_multiplier_ok(fresh_eval, scaled_entry_min_depth_multiplier)
                            if not fresh_eval.get("still_executable"):
                                reason = str(fresh_eval.get("failure_reason") or "not_executable").upper()
                                stop_reason = {
                                    "INSUFFICIENT_LIQUIDITY": "insufficient_depth",
                                    "BELOW_THRESHOLD": "edge_below_minimum",
                                    "PRICE_MOVED": "price_moved",
                                    "MARKET_SUSPENDED": "market_suspended",
                                    "EVENT_STARTED": "event_started",
                                    "START_STATUS_UNCONFIRMED": "feed_stale",
                                    "QUOTE_UNAVAILABLE": "feed_stale",
                                    "VENUE_ERROR": "feed_error",
                                }.get(reason, reason.lower())
                                break
                            if not depth_ok:
                                stop_reason = "insufficient_depth"
                                scaled_entry_summary["depth_limited_runner"] = depth_runner
                                break
                            fresh_sim = dict(fresh_eval.get("simulation") or {})
                            if float(fresh_sim.get("deployed") or 0.0) + 1e-8 < desired_extra_stake:
                                stop_reason = "insufficient_depth"
                                break
                            fresh_sim = _scale_to_target_deployed(fresh_sim, desired_extra_stake, total_bankroll=total_equity or bankroll)
                            if float(fresh_sim.get("expected_roi_pct") or 0.0) + 1e-12 < max(float(min_roi), max(0.0, float(scaled_entry_min_net_edge or 0.0))):
                                stop_reason = "edge_below_minimum"
                                break
                            remaining_free = {
                                key: max(0.0, float(wallet_snapshot.get(key, {}).get("free_for_normal") or 0.0) - float(cumulative_total.get(key, 0.0) or 0.0))
                                for key in wallet_snapshot
                            }
                            wallet_fit, limiting = fit_simulation_to_wallets(fresh_sim, remaining_free, total_bankroll=total_equity or bankroll)
                            if limiting or float(wallet_fit.get("wallet_scale_factor") or 1.0) < 0.999999 or float(wallet_fit.get("deployed") or 0.0) + 1e-8 < desired_extra_stake:
                                stop_reason = "bankroll_limit"
                                if limiting:
                                    scaled_entry_summary["limiting_exchange"] = limiting
                                break

                            fresh_tranche = execute_tranche(next_index, wallet_fit, fresh_eval, fresh_eval, cumulative_total)
                            tranches.append(fresh_tranche)
                            all_fills.extend(fresh_tranche["result"].fills)
                            all_hedges.extend(fresh_tranche["result"].hedge_instructions)
                            all_events.extend([{**event, "tranche": next_index} for event in fresh_tranche["result"].events])
                            for key, value in fresh_tranche["total_capital"].items():
                                cumulative_total[key] = cumulative_total.get(key, 0.0) + float(value or 0.0)
                            for key, value in fresh_tranche["normal_capital"].items():
                                cumulative_normal[key] = cumulative_normal.get(key, 0.0) + float(value or 0.0)
                            record_consumed(fresh_tranche)
                            result = fresh_tranche["result"]
                            if result.state.value not in {"COMPLETE", "HEDGED"} or not result.after_hedge.balanced:
                                stop_reason = "previous_tranche_unlocked"
                                break

                        if stop_reason is None:
                            stop_reason = "risk_limit"

                        aggregate_after = position_snapshot(
                            tranches[0]["plan"].outcomes,
                            all_fills,
                            float(balance_tolerance),
                        )
                        expected_total = sum(float(x["simulation"].get("expected_profit") or 0.0) for x in tranches)
                        actual_deployed = sum(float(v or 0.0) for v in cumulative_total.values())
                        normal_deployed = sum(float(v or 0.0) for v in cumulative_normal.values())
                        captured_total = float(aggregate_after.worst_case_pnl)
                        tranche_payload = []
                        for x in tranches:
                            result = x["result"]
                            tranche_outcomes = exchange_outcome_pnls_from_fills(x["plan"].outcomes, result.fills)
                            tranche_payload.append({
                                "index": int(x["index"]),
                                "started_at": result.events[0].get("at") if result.events else _utc_now(),
                                "state": result.state.value,
                                "deployed": round(sum(float(v or 0.0) for v in x["total_capital"].values()), 4),
                                "normal_stake": round(sum(float(v or 0.0) for v in x["normal_capital"].values()), 4),
                                "expected_profit": round(float(x["simulation"].get("expected_profit") or 0.0), 4),
                                "expected_roi_pct": round(float(x["simulation"].get("expected_roi_pct") or 0.0), 6),
                                "locked_profit": round(float(result.after_hedge.worst_case_pnl), 4) if result.after_hedge.balanced else None,
                                "fill_rate_pct": round(100.0 * min(1.0, sum(float(fill.stake or 0.0) for fill in result.fills if not fill.is_hedge) / max(1e-12, sum(float(leg.requested_stake or 0.0) for leg in x["plan"].legs))), 2),
                                "fresh_snapshot": x.get("fresh_snapshot") or {},
                                "hedge_snapshot": x.get("hedge_snapshot") or {},
                                "fills": [fill.as_dict() for fill in result.fills],
                                "outcome_exchange_pnls": tranche_outcomes,
                            })
                        scaled_entry_summary.update({
                            "is_scaled_entry": len(tranches) >= 2,
                            "is_superbet": len(tranches) >= 2,  # legacy field compatibility
                            "tranche_count": len(tranches),
                            "total_stake": round(normal_deployed, 4),
                            "total_deployed": round(actual_deployed, 4),
                            "additional_stake": round(max(0.0, normal_deployed - base_normal_stake), 4),
                            "incremental_expected_profit": round(sum(float(x["simulation"].get("expected_profit") or 0.0) for x in tranches[1:]), 4),
                            "stop_reason": stop_reason,
                            "tranches": tranche_payload,
                        })
                        if len(tranches) == 1:
                            aggregate_state = base_result.state.value
                        elif not aggregate_after.balanced:
                            aggregate_state = "PANIC"
                        elif any(t["result"].hedge_instructions for t in tranches):
                            aggregate_state = "HEDGED"
                        else:
                            aggregate_state = "COMPLETE"
                        monitor_execution_result = {
                            "plan_id": tranches[0]["plan"].id,
                            "state": aggregate_state,
                            "fills": [fill.as_dict() for fill in all_fills],
                            "hedge_instructions": [h.as_dict() for h in all_hedges],
                            "before_hedge": base_result.before_hedge.as_dict(),
                            "after_hedge": aggregate_after.as_dict(),
                            "events": all_events,
                            "theoretical_profit": round(expected_total, 4),
                            "captured_profit": round(captured_total, 4),
                            "execution_leakage": round(expected_total - captured_total, 4),
                        }
                        outcome_exchange = exchange_outcome_pnls_from_fills(tranches[0]["plan"].outcomes, all_fills)
                        execution_details = {
                            "monitor_timing_run_id": run_id,
                            "reference_checkpoint_ms": int(reference_checkpoint_ms),
                            "execution_checkpoint_ms": execution_offset_ms,
                            "hedge_checkpoint_ms": hedge_offset_ms,
                            "wallet_limited": bool(monitor_simulation.get("limiting_exchange")),
                            "capital_required_by_exchange": {k: round(v, 8) for k, v in cumulative_total.items()},
                            "normal_capital_by_exchange": {k: round(v, 8) for k, v in cumulative_normal.items()},
                            "fill_decisions": [d for x in tranches for d in x["fill_decisions"]],
                            "execution_result": monitor_execution_result,
                            "execution_model": "inplay_delay_adjusted_checkpoints" if monitor_stream == "in_play" else "measured_checkpoints",
                            "monitor_stream": monitor_stream,
                            "delay_model_by_exchange": dict(delay_model_by_exchange or {}),
                            "timed_rechecks": True,
                            "live_order_placement": False,
                            "qualification_snapshot": qualification_snapshot,
                            "scaled_entry": scaled_entry_summary,
                            "superbet": scaled_entry_summary,  # legacy read compatibility
                        }
                        monitor_execution_id = self.db.add_execution_run(
                            opportunity_id,
                            mode="sim",
                            execution_type="modeled_inplay_monitor" if monitor_stream == "in_play" else ("modeled_racing_monitor" if monitor_stream == "racing" else "modeled_monitor"),
                            state="MONITOR_PENDING",
                            deployed=actual_deployed,
                            expected_profit=expected_total,
                            captured_profit=captured_total,
                            max_unhedged_exposure=float(base_result.before_hedge.exposure_spread),
                            details=execution_details,
                            is_real=False,
                            started_at=started_at,
                            finished_at=_utc_now(),
                        )
                        position_stakes = [
                            {
                                "exchange": fill.exchange,
                                "selection": fill.selection,
                                "odds": float(fill.odds),
                                "stake": float(fill.stake),
                                "is_hedge": bool(fill.is_hedge),
                                "tranche": next((int(ev.get("tranche")) for ev in all_events if ev.get("state") in {"LEG_FILLED", "LEG_PARTIAL"} and ev.get("leg_index") == fill.leg_index and str(ev.get("selection") or fill.selection) == str(fill.selection)), 1),
                            }
                            for fill in all_fills
                        ]
                        position_simulation = {
                            **dict(monitor_simulation),
                            "deployed": round(actual_deployed, 8),
                            "expected_profit": round(expected_total, 8),
                            "stakes": position_stakes,
                            "execution_model": "inplay_delay_adjusted_checkpoints" if monitor_stream == "in_play" else "measured_checkpoints",
                            "monitor_stream": monitor_stream,
                            "execution_state": aggregate_state,
                            "fills": [fill.as_dict() for fill in all_fills],
                            "hedges": [h.as_dict() for h in all_hedges],
                            "before_hedge": base_result.before_hedge.as_dict(),
                            "after_hedge": aggregate_after.as_dict(),
                            "qualification_snapshot": qualification_snapshot,
                            "scaled_entry": scaled_entry_summary,
                            "superbet": scaled_entry_summary,  # legacy read compatibility
                        }
                        monitor_opened, monitor_reason = self.db.open_monitor_position(
                            opportunity_id=opportunity_id,
                            execution_run_id=monitor_execution_id,
                            event_key=event_key,
                            market_name=market_name,
                            deployed=actual_deployed,
                            expected_profit=expected_total,
                            stakes_by_exchange=cumulative_total,
                            normal_stakes_by_exchange=cumulative_normal,
                            outcome_exchange_pnls=outcome_exchange,
                            simulation=position_simulation,
                            hedge_reserve_pct=hedge_reserve_pct,
                            stream=monitor_stream,
                        )
                        if not monitor_opened:
                            self.db.update_execution_run_state(
                                monitor_execution_id,
                                "MONITOR_SKIPPED",
                                captured_profit=0.0,
                                details_patch={"monitor_reason": monitor_reason},
                            )
                        else:
                            state_label = "MONITOR_OPEN" if aggregate_after.balanced else "MONITOR_OPEN_EXPOSED"
                            self.db.update_execution_run_state(
                                monitor_execution_id,
                                state_label,
                                captured_profit=captured_total,
                                details_patch={
                                    "monitor_position_opened": True,
                                    "execution_state": aggregate_state,
                                    "scaled_entry": scaled_entry_summary,
                                "superbet": scaled_entry_summary,  # legacy read compatibility
                                },
                            )

        survived_through = 0
        first_failure = None
        for row in observations:
            if row.get("still_executable") and first_failure is None:
                survived_through = int(row["offset_ms"])
            elif first_failure is None:
                first_failure = row.get("failure_reason") or "NOT_EXECUTABLE"

        finished_at = _utc_now()
        self.db.finish_monitor_timing_run(
            run_id,
            finished_at=finished_at,
            status="COMPLETE",
            survived_through_ms=survived_through,
            first_failure_reason=first_failure,
            reference_profit=captured_profit,
            reference_roi_pct=captured_roi,
            reference_executable=bool(reference and reference.get("still_executable")),
        )
        latencies = [int(x.get("fetch_latency_ms") or 0) for x in observations]
        if research_only:
            return {
                "run_id": run_id,
                "opportunity_id": opportunity_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "initial_deployed": round(planned_deployed, 4),
                "initial_profit": round(planned_profit, 4),
                "initial_roi_pct": round(planned_roi, 6),
                "reference_checkpoint_ms": int(reference_checkpoint_ms),
                "execution_checkpoint_ms": execution_offset_ms,
                "hedge_checkpoint_ms": hedge_offset_ms,
                "reference_profit": round(captured_profit, 4),
                "reference_roi_pct": round(captured_roi, 6),
                "reference_executable": bool(reference and reference.get("still_executable")),
                "survived_through_ms": survived_through,
                "first_failure_reason": first_failure,
                "median_fetch_latency_ms": round(statistics.median(latencies), 2) if latencies else 0.0,
                "observations": observations,
                "monitor_execution_id": None,
                "monitor_opened": False,
                "monitor_reason": "research_only",
                "monitor_simulation": None,
                "monitor_execution_result": None,
                "research_only": True,
                "live_order_placement": False,
            }

        in_play_failures = {"EVENT_STARTED", "BETFAIR_IN_PLAY", "MATCHBOOK_IN_PLAY", "BOTH_IN_PLAY"}
        handed_off = bool(handoff_in_play and monitor_stream == "pre_match" and str(first_failure or "").upper() in in_play_failures)
        if handed_off:
            # This is not a failed pre-match transaction. The scanner immediately
            # reroutes the same opportunity into the separate in-play Monitor stream.
            # Suppressing a pre-match execution row avoids double counting one signal
            # as both a pre-match miss and an in-play attempt.
            monitor_reason = "in_play_handoff"
        elif monitor_execution_id is None:
            monitor_execution_id = self.db.add_execution_run(
                opportunity_id, mode="sim", execution_type="modeled_inplay_monitor" if monitor_stream == "in_play" else ("modeled_racing_monitor" if monitor_stream == "racing" else "modeled_monitor"),
                state="MONITOR_MISSED" if not captured_profit else "MONITOR_SKIPPED",
                deployed=planned_deployed, expected_profit=planned_profit, captured_profit=0.0,
                max_unhedged_exposure=0.0,
                details={"monitor_timing_run_id": run_id, "reference_checkpoint_ms": int(reference_checkpoint_ms), "execution_checkpoint_ms": execution_offset_ms, "hedge_checkpoint_ms": hedge_offset_ms, "survived_through_ms": survived_through, "first_failure_reason": first_failure, "observations": observations, "execution_model": "inplay_delay_adjusted_checkpoints" if monitor_stream == "in_play" else "measured_checkpoints", "monitor_stream": monitor_stream, "delay_model_by_exchange": dict(delay_model_by_exchange or {}), "timed_rechecks": True, "live_order_placement": False, "monitor_reason": monitor_reason, "qualification_snapshot": qualification_snapshot},
                is_real=False, started_at=started_at, finished_at=finished_at,
            )
        else:
            self.db.update_execution_run_state(monitor_execution_id, None, details_patch={"survived_through_ms": survived_through, "first_failure_reason": first_failure, "observations": observations})
        return {
            "run_id": run_id,
            "opportunity_id": opportunity_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "initial_deployed": round(planned_deployed, 4),
            "initial_profit": round(planned_profit, 4),
            "initial_roi_pct": round(planned_roi, 6),
            "reference_checkpoint_ms": int(reference_checkpoint_ms),
            "execution_checkpoint_ms": execution_offset_ms,
            "hedge_checkpoint_ms": hedge_offset_ms,
            "reference_profit": round(captured_profit, 4),
            "reference_roi_pct": round(captured_roi, 6),
            "reference_executable": bool(reference and reference.get("still_executable")),
            "survived_through_ms": survived_through,
            "first_failure_reason": first_failure,
            "median_fetch_latency_ms": round(statistics.median(latencies), 2) if latencies else 0.0,
            "observations": observations,
            "monitor_execution_id": monitor_execution_id,
            "monitor_opened": monitor_opened,
            "monitor_reason": monitor_reason,
            "monitor_simulation": monitor_simulation,
            "monitor_execution_result": monitor_execution_result,
            "research_only": False,
            "monitor_stream": monitor_stream,
            "delay_model_by_exchange": dict(delay_model_by_exchange or {}),
            "handed_off_to_in_play": handed_off,
        }
