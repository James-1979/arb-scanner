from __future__ import annotations
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .db import DB
from .engine import simulate_equal_return
from .execution import (
    PaperExecutionCoordinator,
    build_execution_plan,
    capital_by_exchange,
    capital_required_by_exchange_from_fills,
    exchange_key,
    exchange_outcome_pnls,
    exchange_outcome_pnls_from_fills,
    fit_simulation_to_wallets,
    order_capital_required,
    scale_simulation,
)
from .models import Leg, Scenario
from .normalization import parse_time
from .quality import assess_data_quality, quality_profile
from .monitor_timing import model_execution_inputs


QUALITY_BANDS = {"Tiny": 0, "Thin": 1, "Usable": 2, "Strong": 3, "Excellent": 4}


def _quality_rank(value: str | None) -> int:
    raw = str(value or "Tiny").strip().lower()
    for label, rank in QUALITY_BANDS.items():
        if label.lower() == raw:
            return rank
    return 0


def _quality_passes(value: str | None, minimum: str | None) -> bool:
    floor = str(minimum or "all").strip()
    if not floor or floor.lower() == "all":
        return True
    return _quality_rank(value) >= _quality_rank(floor)


def _row_quality(row: dict, reference_bankroll: float, stale_after_seconds: float) -> tuple[str, float]:
    """Recompute the same opportunity quality used by the History view from stored capture evidence."""
    try:
        legs = _legs(row)
        sim = simulate_equal_return(legs, Scenario("replay-quality", max(1.0, reference_bankroll), 100.0, 100.0))
        dq = assess_data_quality(
            legs, float(row.get("match_score") or 0.0), row.get("detected_at"), stale_after_seconds
        )
        profile = quality_profile(sim, float(row.get("match_score") or 0.0), reference_bankroll, data_quality=dq)
        return str(profile.get("quality_band") or "Tiny"), float(profile.get("quality_score") or 0.0)
    except Exception:
        return "Tiny", 0.0


@dataclass
class OpenPosition:
    release_at: datetime
    deployed: float
    pnl: float | None


@dataclass
class AnalyticalPosition:
    release_at: datetime
    deployed: float
    pnl: float
    event_index: int


class ReplayHistoryLimitExceeded(ValueError):
    """Raised rather than silently returning a partial long-range scenario."""


def _dt(value: str | None, fallback: datetime) -> datetime:
    return parse_time(value) or fallback


def _legs(row: dict) -> list[Leg]:
    prepared = row.get("_prepared_legs") if isinstance(row, dict) else None
    if prepared is not None:
        return list(prepared)
    payload = json.loads(row.get("legs_json") or "[]")
    allowed = Leg.__dataclass_fields__
    return [Leg(**{k: v for k, v in item.items() if k in allowed}) for item in payload]


def _pnl_for_outcome(sim: dict, outcome: str | None) -> float | None:
    if not outcome:
        return None
    pnls = sim.get("outcome_pnls") or {}
    pnl = pnls.get(outcome)
    if pnl is not None:
        return float(pnl)
    norm = str(outcome).strip().lower()
    for key, value in pnls.items():
        if str(key).strip().lower() == norm:
            return float(value)
    return None

def _estimated_release_time(row: dict, detected: datetime) -> tuple[datetime, str]:
    """Estimate when capital would normally be released if the result poller was late.

    v0.7.0 stored when ArbScanner *observed* settlement, not the exchange's exact
    market-close timestamp.  For analytics we can optionally use a conservative
    sport-duration estimate, capped by the observed settlement time.
    """
    observed = parse_time(row.get("settled_at"))
    start = parse_time(row.get("event_start"))
    if not start:
        return (max(detected, observed) if observed else detected), "observed_settlement"
    sport = str(row.get("sport") or "Unknown").strip().lower()
    hours = {
        "football": 3.0, "soccer": 3.0, "basketball": 4.0, "american football": 5.0,
        "baseball": 5.0, "ice hockey": 4.0, "cricket": 10.0, "tennis": 5.0,
        "rugby union": 3.5, "rugby league": 3.5, "volleyball": 4.0, "handball": 3.0,
        "australian rules": 4.0, "field hockey": 3.0,
    }.get(sport, 4.0)
    estimated = start + timedelta(hours=hours)
    if observed and observed <= estimated:
        return max(detected, observed), "observed_settlement"
    return max(detected, estimated), "estimated_event_close"


def replay_history(db: DB, starting_capital: float, max_event_exposure_pct: float = 100.0, include_demo: bool = False) -> dict:
    """Legacy replay used by the existing capital-scenario table.

    Kept stable for backwards compatibility.  The richer Results & Analytics UI
    uses :func:`replay_analysis`, which intentionally works from settled history
    only and exposes per-event capital movements and threshold diagnostics.
    """
    rows = db.opportunity_rows(limit=250001, include_demo=include_demo)
    if len(rows) > 250000:
        raise ReplayHistoryLimitExceeded(
            "Replay history exceeds the 250,000-opportunity safety ceiling; narrow the selected period."
        )
    if not rows:
        return {
            "starting_capital": starting_capital,
            "ending_cash": starting_capital,
            "ending_equity": starting_capital,
            "realized_profit": 0.0,
            "realized_roi_pct": 0.0,
            "expected_open_profit": 0.0,
            "deployed_total": 0.0,
            "recommendations_taken": 0,
            "liquidity_limited": 0,
            "capital_limited": 0,
            "series": [],
        }
    rows.sort(key=lambda r: r["detected_at"])
    cash = float(starting_capital)
    open_positions: list[OpenPosition] = []
    deployed_total = 0.0
    realized_profit = 0.0
    taken = 0
    liq_limited = 0
    cap_limited = 0
    series = []

    def release(up_to: datetime):
        nonlocal cash, realized_profit, open_positions
        keep = []
        for p in open_positions:
            if p.release_at <= up_to and p.pnl is not None:
                cash += p.deployed + p.pnl
                realized_profit += p.pnl
            else:
                keep.append(p)
        open_positions = keep

    for row in rows:
        detected = _dt(row.get("detected_at"), datetime.now(timezone.utc))
        release(detected)
        if cash <= 0:
            continue
        legs = _legs(row)
        if len(legs) < 2:
            continue
        sim = simulate_equal_return(legs, Scenario("replay", cash, 100.0, max_event_exposure_pct))
        if not sim.get("executable"):
            continue
        deployed = float(sim["deployed"])
        if deployed > cash + 1e-6:
            continue
        cash -= deployed
        taken += 1
        deployed_total += deployed
        liq_limited += int(sim.get("limited_by") == "liquidity")
        cap_limited += int(sim.get("limited_by") == "bankroll")
        outcome = row.get("outcome")
        pnl = _pnl_for_outcome(sim, outcome)
        start = _dt(row.get("event_start"), detected + timedelta(hours=3))
        settled = _dt(row.get("settled_at"), start + timedelta(hours=3)) if outcome else start + timedelta(hours=3)
        release_at = max(detected, settled)
        open_positions.append(OpenPosition(release_at, deployed, pnl))
        series.append({
            "time": row.get("detected_at"),
            "event": row.get("event_name") or row.get("event_key"),
            "cash_after_stake": round(cash, 2),
            "deployed": round(deployed, 2),
            "projected_profit": round(float(sim.get("expected_profit", 0.0)), 4),
            "realized_pnl": None if pnl is None else round(float(pnl), 4),
            "limited_by": sim.get("limited_by"),
        })

    release(datetime.max.replace(tzinfo=timezone.utc))
    expected_open_profit = 0.0
    open_equity = sum(p.deployed for p in open_positions)
    ending_equity = cash + open_equity + expected_open_profit
    return {
        "starting_capital": round(starting_capital, 2),
        "ending_cash": round(cash, 2),
        "ending_equity": round(ending_equity, 2),
        "realized_profit": round(realized_profit, 4),
        "realized_roi_pct": round((realized_profit / starting_capital) * 100.0, 6) if starting_capital > 0 else 0.0,
        "expected_open_profit": round(expected_open_profit, 4),
        "deployed_total": round(deployed_total, 2),
        "recommendations_taken": taken,
        "liquidity_limited": liq_limited,
        "capital_limited": cap_limited,
        "series": series,
    }


def prepare_replay_history(
    db: DB, *, sport: str | None = None, sports: list[str] | None = None, strategy: str | None = None, days: int | None = None,
    include_demo: bool = False, date_from: datetime | None = None, date_to: datetime | None = None,
    exchange: str | None = None, exchanges: list[str] | None = None, market: str | None = None, search: str | None = None,
    engine_instance_id: str | None = None, engine_instance_ids: list[str] | None = None,
    execution_mode: str | None = None, minimum_quality_band: str | None = None,
    time_basis: str = "detected_at", require_monitor_evidence: bool = False,
) -> dict:
    """Prepare a bounded settled historical cohort once for a family of replays.

    0.9.14 pushes the period and execution-mode cohort into SQLite.  A sentinel
    row above 250,000 causes an explicit failure instead of a misleading partial
    simulation.
    """
    started = time.perf_counter()
    exchange_filter = str(exchange or "").strip().lower()
    exchange_filters = {exchange_key(str(x or "")) for x in (exchanges or []) if str(x or "").strip() and str(x or "").strip().lower() != "all"}
    exchange_filters.discard("unknown")
    quality_floor = str(minimum_quality_band or "all").strip() or "all"
    basis = str(time_basis or "detected_at").strip().lower()
    if basis not in {"detected_at", "settled_at"}:
        basis = "detected_at"
    effective_from = date_from
    if effective_from is None and days:
        effective_from = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))

    source_rows = db.replay_opportunity_rows(
        include_demo=include_demo,
        date_from=effective_from.isoformat() if effective_from else None,
        date_to=date_to.isoformat() if date_to else None,
        sport=sport, sports=sports, strategy=strategy, engine_instance_id=engine_instance_id, engine_instance_ids=engine_instance_ids,
        market=market, search=search, execution_mode=execution_mode,
        time_basis=basis, limit=250001,
    )
    if len(source_rows) > 250000:
        raise ReplayHistoryLimitExceeded(
            "Scenario / Replay history exceeds the 250,000-opportunity safety ceiling; narrow the selected period."
        )

    config = db.get_setting("config", {}) or {}
    quality_reference_bankroll = max(1.0, float(config.get("quality_reference_bankroll", 500.0) or 500.0))
    quality_stale_seconds = max(1.0, float(config.get("stale_quote_seconds", 90.0) or 90.0))
    prepared_rows: list[dict] = []
    for raw in source_rows:
        row = dict(raw)
        try:
            legs = _legs(row)
        except Exception:
            legs = []
        row["_prepared_legs"] = legs
        if exchange_filters or (exchange_filter and exchange_filter != "all"):
            keys = {exchange_key(str(x.exchange or "")) for x in legs}
            wanted = exchange_filters or {exchange_key(exchange_filter)}
            if not (keys & wanted):
                continue
        qualification = str(row.get("qualification_status") or "qualified")
        section = str(row.get("section") or "sports").strip().lower()
        sport_name = str(row.get("sport") or "").strip().lower()
        row_stream = "racing" if section == "racing" or sport_name == "greyhounds" else ("in_play" if qualification == "in_play_qualified" else "pre_match")
        quality_band, quality_score = _row_quality(row, quality_reference_bankroll, quality_stale_seconds)
        row["_quality_band"] = quality_band
        row["_quality_score"] = quality_score
        row["_quality_pass"] = _quality_passes(quality_band, quality_floor)
        row["_row_stream"] = row_stream
        prepared_rows.append(row)

    monitor_candidates = [
        int(row.get("id") or 0) for row in prepared_rows
        if row.get("_quality_pass") and int(row.get("id") or 0) > 0
    ]
    monitor_runs = db.monitor_timing_runs_for_opportunities(monitor_candidates) if require_monitor_evidence else {}
    prepare_ms = (time.perf_counter() - started) * 1000.0
    return {
        "rows": prepared_rows,
        "open_ignored": 0,
        "config": config,
        "monitor_runs": monitor_runs,
        "minimum_quality_band": quality_floor,
        "time_basis": basis,
        "diagnostics": {
            "opportunities_scanned": len(source_rows),
            "opportunities_selected": len(prepared_rows),
            "monitor_runs_loaded": len(monitor_runs),
            "observations_loaded": sum(len((run or {}).get("observations") or []) for run in monitor_runs.values()),
            "scenario_prepare_ms": round(prepare_ms, 3),
            "history_query_bounded": True,
            "history_safety_ceiling": 250000,
        },
    }


def replay_analysis(
    db: DB,
    starting_capital: float,
    max_event_exposure_pct: float = 100.0,
    min_profit: float = 0.0,
    min_deployed_roi_pct: float = 0.0,
    sport: str | None = None,
    sports: list[str] | None = None,
    strategy: str | None = None,
    days: int | None = None,
    include_demo: bool = False,
    release_policy: str = "estimated_close",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    exchange: str | None = None,
    exchanges: list[str] | None = None,
    market: str | None = None,
    search: str | None = None,
    engine_instance_id: str | None = None,
    engine_instance_ids: list[str] | None = None,
    execution_mode: str | None = None,
    exchange_balances: dict[str, float] | None = None,
    require_monitor_evidence: bool = False,
    monitor_stream: str | None = None,
    monitor_streams: list[str] | None = None,
    minimum_quality_band: str | None = None,
    time_basis: str = "detected_at",
    prepared_history: dict | None = None,
    max_stake: float | None = None,
    hedge_reserve_pct: float | None = None,
) -> dict:
    """Replay *settled* paper opportunities as a configurable historical scenario.

    The calculation uses the captured odds, liquidity and commission assumptions
    saved with each opportunity, then applies the actual stored market outcome.
    Capital is tied up from detection until release.  Existing v0.7.0 data stores
    result-observation time rather than exact exchange market-close time, so the
    release policy can use either that conservative observation or a sport-duration
    estimate capped by the observed settlement.
    """
    if exchange_balances is not None:
        return _replay_analysis_wallets(
            db=db, starting_capital=starting_capital, exchange_balances=exchange_balances,
            max_event_exposure_pct=max_event_exposure_pct, min_profit=min_profit,
            min_deployed_roi_pct=min_deployed_roi_pct, sport=sport, sports=sports, strategy=strategy, days=days,
            include_demo=include_demo, release_policy=release_policy, date_from=date_from, date_to=date_to,
            exchange=exchange, exchanges=exchanges, market=market, search=search, engine_instance_id=engine_instance_id,
            engine_instance_ids=engine_instance_ids, execution_mode=execution_mode,
            require_monitor_evidence=require_monitor_evidence, monitor_stream=monitor_stream, monitor_streams=monitor_streams,
            minimum_quality_band=minimum_quality_band, time_basis=time_basis, prepared_history=prepared_history,
            max_stake=max_stake, hedge_reserve_pct=hedge_reserve_pct,
        )

    starting_capital = max(0.01, float(starting_capital))
    max_event_exposure_pct = min(100.0, max(0.0, float(max_event_exposure_pct)))
    min_profit = max(0.0, float(min_profit))
    min_deployed_roi_pct = max(0.0, float(min_deployed_roi_pct))
    sport_filter = str(sport or "").strip()
    strategy_filter = str(strategy or "").strip()
    release_policy = str(release_policy or "estimated_close").strip().lower()
    if release_policy not in {"estimated_close", "observed"}:
        release_policy = "estimated_close"
    exchange_filter = str(exchange or "").strip().lower()
    market_filter = str(market or "").strip().lower()
    search_filter = str(search or "").strip().lower()
    execution_filter = str(execution_mode or "").strip().lower()
    stream_filter = str(monitor_stream or "combined").strip().lower()
    selected_streams = {str(x or "").strip().lower() for x in (monitor_streams or []) if str(x or "").strip()}
    selected_streams = {x for x in selected_streams if x in {"pre_match", "in_play", "racing"}}
    if not selected_streams and stream_filter not in {"combined", "all"}:
        selected_streams = {stream_filter}
    quality_floor = str(minimum_quality_band or "all").strip() or "all"
    time_basis = str(time_basis or "detected_at").strip().lower()
    if time_basis not in {"detected_at", "settled_at"}:
        time_basis = "detected_at"
    if stream_filter not in {"pre_match", "in_play", "racing", "combined", "all"}:
        stream_filter = "combined"
    if prepared_history is None:
        prepared_history = prepare_replay_history(
            db, sport=sport, sports=sports, strategy=strategy, days=days, include_demo=include_demo,
            date_from=date_from, date_to=date_to, exchange=exchange, exchanges=exchanges, market=market, search=search,
            engine_instance_id=engine_instance_id, engine_instance_ids=engine_instance_ids,
            execution_mode=execution_mode, minimum_quality_band=minimum_quality_band,
            time_basis=time_basis, require_monitor_evidence=require_monitor_evidence,
        )
    prepared_rows = list(prepared_history.get("rows") or [])
    stream_candidates = [
        row for row in prepared_rows
        if not selected_streams or str(row.get("_row_stream") or "pre_match") in selected_streams
    ]
    quality_filtered = sum(1 for row in stream_candidates if not _quality_passes(row.get("_quality_band"), quality_floor))
    rows = [row for row in stream_candidates if _quality_passes(row.get("_quality_band"), quality_floor)]
    open_ignored = int(prepared_history.get("open_ignored") or 0)
    rows.sort(key=lambda r: (r.get("settled_at") if time_basis == "settled_at" else r.get("detected_at")) or "")

    cash = starting_capital
    open_positions: list[AnalyticalPosition] = []
    events: list[dict] = []
    series: list[dict] = []
    realized_profit = 0.0
    deployed_total = 0.0
    peak_concurrent_deployed = 0.0
    counts = {
        "settled_available": len(rows) + quality_filtered,
        "taken": 0,
        "skipped_min_profit": 0,
        "skipped_min_roi": 0,
        "skipped_quality": quality_filtered,
        "skipped_no_capital": 0,
        "skipped_non_executable": 0,
        "skipped_result_mapping": 0,
        "open_ignored": open_ignored,
        "release_estimated": 0,
        "release_observed": 0,
    }

    first_time = _dt(rows[0].get("detected_at"), datetime.now(timezone.utc)) if rows else datetime.now(timezone.utc)
    series.append({
        "time": first_time.isoformat(),
        "bankroll": round(starting_capital, 4),
        "cash_available": round(starting_capital, 4),
        "deployed_open": 0.0,
        "kind": "start",
    })

    def release(up_to: datetime):
        nonlocal cash, realized_profit, open_positions
        due = sorted((p for p in open_positions if p.release_at <= up_to), key=lambda p: p.release_at)
        future = [p for p in open_positions if p.release_at > up_to]
        for idx, pos in enumerate(due):
            cash += pos.deployed + pos.pnl
            realized_profit += pos.pnl
            remaining_principal = sum(p.deployed for p in future) + sum(p.deployed for p in due[idx + 1:])
            equity = cash + remaining_principal
            event = events[pos.event_index]
            event["capital_after_result"] = round(equity, 4)
            event["cash_after_result"] = round(cash, 4)
            series.append({
                "time": pos.release_at.isoformat(),
                "bankroll": round(equity, 4),
                "cash_available": round(cash, 4),
                "deployed_open": round(remaining_principal, 4),
                "kind": "settlement",
                "event": event.get("event_name"),
                "pnl": round(pos.pnl, 4),
            })
        open_positions = future

    for row in rows:
        detected = _dt(row.get("detected_at"), first_time)
        release(detected)
        if cash <= 0.0:
            counts["skipped_no_capital"] += 1
            continue
        try:
            legs = _legs(row)
        except Exception:
            counts["skipped_non_executable"] += 1
            continue
        if len(legs) < 2:
            counts["skipped_non_executable"] += 1
            continue

        sim = simulate_equal_return(legs, Scenario("analysis", cash, 100.0, max_event_exposure_pct))
        if not sim.get("executable"):
            counts["skipped_non_executable"] += 1
            continue
        scenario_max_stake = None if max_stake is None else max(0.0, float(max_stake))
        if scenario_max_stake and scenario_max_stake > 0:
            largest_stake = max((float(x.get("stake") or 0.0) for x in (sim.get("stakes") or [])), default=0.0)
            if largest_stake > scenario_max_stake + 1e-9:
                factor = scenario_max_stake / largest_stake
                sim = scale_simulation(sim, factor, total_bankroll=cash)
                sim["scenario_stake_scale_factor"] = round(factor, 8)
                sim["limited_by"] = "max_stake"
                counts["stake_capped"] = int(counts.get("stake_capped") or 0) + 1
        expected_profit = float(sim.get("expected_profit") or 0.0)
        deployed_roi = float(sim.get("expected_roi_pct") or 0.0)
        if expected_profit + 1e-9 < min_profit:
            counts["skipped_min_profit"] += 1
            continue
        if deployed_roi + 1e-9 < min_deployed_roi_pct:
            counts["skipped_min_roi"] += 1
            continue

        deployed = float(sim.get("deployed") or 0.0)
        if deployed <= 0.0 or deployed > cash + 1e-6:
            counts["skipped_no_capital"] += 1
            continue
        pnl = _pnl_for_outcome(sim, row.get("outcome"))
        if pnl is None:
            counts["skipped_result_mapping"] += 1
            continue

        open_principal_before = sum(p.deployed for p in open_positions)
        equity_before = cash + open_principal_before
        cash_before = cash
        cash -= deployed
        observed_settled = _dt(row.get("settled_at"), detected)
        if release_policy == "observed":
            release_at, release_basis = max(detected, observed_settled), "observed_settlement"
        else:
            release_at, release_basis = _estimated_release_time(row, detected)
        if release_basis == "estimated_event_close":
            counts["release_estimated"] += 1
        else:
            counts["release_observed"] += 1
        event = {
            "id": int(row.get("id") or 0),
            "detected_at": row.get("detected_at"),
            "settled_at": row.get("settled_at"),
            "release_at": release_at.isoformat(),
            "release_basis": release_basis,
            "sport": row.get("sport") or (legs[0].sport if legs else "Unknown"),
            "quality_band": row.get("_quality_band") or "Tiny",
            "quality_score": row.get("_quality_score"),
            "strategy": row.get("strategy") or "1x2",
            "event_name": row.get("event_name") or row.get("event_key"),
            "market_name": row.get("market_name"),
            "outcome": row.get("outcome"),
            "capital_before": round(equity_before, 4),
            "cash_available_before": round(cash_before, 4),
            "deployed": round(deployed, 4),
            "expected_profit": round(expected_profit, 4),
            "expected_roi_pct": round(deployed_roi, 6),
            "realized_pnl": round(float(pnl), 4),
            "deployed_roi_pct": round((float(pnl) / deployed) * 100.0, 6) if deployed > 0 else 0.0,
            "bankroll_roi_pct_at_entry": round((float(pnl) / equity_before) * 100.0, 6) if equity_before > 0 else 0.0,
            "limited_by": sim.get("limited_by"),
            "capital_after_result": None,
            "cash_after_result": None,
        }
        event_index = len(events)
        events.append(event)
        open_positions.append(AnalyticalPosition(release_at, deployed, float(pnl), event_index))
        counts["taken"] += 1
        deployed_total += deployed
        concurrent_deployed = sum(p.deployed for p in open_positions)
        peak_concurrent_deployed = max(peak_concurrent_deployed, concurrent_deployed)
        series.append({
            "time": detected.isoformat(),
            "bankroll": round(equity_before, 4),
            "cash_available": round(cash, 4),
            "deployed_open": round(concurrent_deployed, 4),
            "kind": "entry",
            "event": event.get("event_name"),
        })

    release(datetime.max.replace(tzinfo=timezone.utc))
    ending_capital = cash + sum(p.deployed for p in open_positions)
    values = [float(p.get("bankroll") or starting_capital) for p in series]
    peak = starting_capital
    max_drawdown_pct = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak - value) / peak) * 100.0)

    by_sport: dict[str, dict] = {}
    for event in events:
        name = str(event.get("sport") or "Unknown")
        bucket = by_sport.setdefault(name, {"sport": name, "events": 0, "deployed": 0.0, "profit": 0.0, "positive": 0})
        bucket["events"] += 1
        bucket["deployed"] += float(event.get("deployed") or 0.0)
        bucket["profit"] += float(event.get("realized_pnl") or 0.0)
        bucket["positive"] += int(float(event.get("realized_pnl") or 0.0) > 0)
    sport_rows = []
    for bucket in by_sport.values():
        deployed = float(bucket["deployed"] or 0.0)
        events_count = int(bucket["events"] or 0)
        sport_rows.append({
            "sport": bucket["sport"],
            "events": events_count,
            "positive": int(bucket["positive"] or 0),
            "deployed": round(deployed, 2),
            "profit": round(float(bucket["profit"] or 0.0), 4),
            "profit_per_event": round(float(bucket["profit"] or 0.0) / events_count, 4) if events_count else 0.0,
            "return_on_deployed_pct": round((float(bucket["profit"] or 0.0) / deployed) * 100.0, 6) if deployed > 0 else 0.0,
        })
    sport_rows.sort(key=lambda x: (x["profit"], x["events"]), reverse=True)

    events_for_ui = sorted(events, key=lambda x: x.get("settled_at") or x.get("detected_at") or "", reverse=True)
    realized_roi = (realized_profit / starting_capital) * 100.0 if starting_capital > 0 else 0.0
    return {
        "filters": {
            "starting_capital": round(starting_capital, 2),
            "minimum_profit": round(min_profit, 4),
            "minimum_deployed_roi_pct": round(min_deployed_roi_pct, 6),
            "max_event_exposure_pct": round(max_event_exposure_pct, 4),
            "max_stake": None if max_stake is None else max(0.0, float(max_stake)),
            "hedge_reserve_pct": hedge_reserve_pct,
            "sport": sport_filter or "all",
            "sports": list(sports or []),
            "strategy": strategy_filter or "all",
            "engine_instance_id": str(engine_instance_id or "all"),
            "engine_instance_ids": list(engine_instance_ids or []),
            "days": days,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "exchange": exchange_filter or "all",
            "exchanges": list(exchanges or []),
            "market": market_filter or "all",
            "search": search_filter,
            "execution_mode": execution_filter or "all",
            "monitor_stream": stream_filter,
            "monitor_streams": sorted(selected_streams),
            "minimum_quality_band": quality_floor,
            "release_policy": release_policy,
        },
        "starting_capital": round(starting_capital, 2),
        "ending_capital": round(ending_capital, 4),
        "realized_profit": round(realized_profit, 4),
        "realized_roi_pct": round(realized_roi, 6),
        "total_deployed": round(deployed_total, 2),
        "return_on_deployed_pct": round((realized_profit / deployed_total) * 100.0, 6) if deployed_total > 0 else 0.0,
        "average_profit": round(realized_profit / counts["taken"], 4) if counts["taken"] else 0.0,
        "positive_results": sum(1 for e in events if float(e.get("realized_pnl") or 0.0) > 0),
        "negative_results": sum(1 for e in events if float(e.get("realized_pnl") or 0.0) < 0),
        "peak_concurrent_deployed": round(peak_concurrent_deployed, 2),
        "peak_capital_tied_pct": round((peak_concurrent_deployed / starting_capital) * 100.0, 4) if starting_capital > 0 else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "counts": counts,
        "series": sorted(series, key=lambda x: x.get("time") or ""),
        "events": events_for_ui,
        "by_sport": sport_rows,
    }



def _replay_analysis_wallets(
    *, db: DB, starting_capital: float, exchange_balances: dict[str, float],
    max_event_exposure_pct: float = 100.0, min_profit: float = 0.0,
    min_deployed_roi_pct: float = 0.0, sport: str | None = None, sports: list[str] | None = None, strategy: str | None = None,
    days: int | None = None, include_demo: bool = False, release_policy: str = "estimated_close",
    date_from: datetime | None = None, date_to: datetime | None = None, exchange: str | None = None, exchanges: list[str] | None = None,
    market: str | None = None, search: str | None = None, engine_instance_id: str | None = None, engine_instance_ids: list[str] | None = None,
    execution_mode: str | None = None, require_monitor_evidence: bool = False, monitor_stream: str | None = None,
    monitor_streams: list[str] | None = None, minimum_quality_band: str | None = None,
    time_basis: str = "detected_at", prepared_history: dict | None = None,
    max_stake: float | None = None, hedge_reserve_pct: float | None = None,
) -> dict:
    """Replay settled history with independent virtual wallets per exchange."""
    wallets = {exchange_key(k): max(0.0, float(v or 0.0)) for k, v in (exchange_balances or {}).items()}
    wallets = {k: v for k, v in wallets.items() if k != "unknown"}
    if not wallets:
        # 0.9.0 never invents a venue allocation or redistributes capital when a
        # new provider is enabled. Wallet replay callers must supply allocations.
        total = max(0.01, float(starting_capital or 0.01))
        wallets = {"unallocated": total}
    starting_wallets = dict(wallets)
    starting_capital = max(0.01, sum(wallets.values()))
    max_event_exposure_pct = min(100.0, max(0.0, float(max_event_exposure_pct)))
    min_profit = max(0.0, float(min_profit))
    min_deployed_roi_pct = max(0.0, float(min_deployed_roi_pct))
    sport_filter = str(sport or "").strip()
    strategy_filter = str(strategy or "").strip()
    release_policy = str(release_policy or "estimated_close").strip().lower()
    if release_policy not in {"estimated_close", "observed"}:
        release_policy = "estimated_close"
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days))) if days and date_from is None else None
    exchange_filter = str(exchange or "").strip().lower()
    market_filter = str(market or "").strip().lower()
    search_filter = str(search or "").strip().lower()
    execution_filter = str(execution_mode or "").strip().lower()
    stream_filter = str(monitor_stream or "combined").strip().lower()
    selected_streams = {str(x or "").strip().lower() for x in (monitor_streams or []) if str(x or "").strip()}
    selected_streams = {x for x in selected_streams if x in {"pre_match", "in_play", "racing"}}
    if not selected_streams and stream_filter not in {"combined", "all"}:
        selected_streams = {stream_filter}
    quality_floor = str(minimum_quality_band or "all").strip() or "all"
    time_basis = str(time_basis or "detected_at").strip().lower()
    if time_basis not in {"detected_at", "settled_at"}:
        time_basis = "detected_at"
    if stream_filter not in {"pre_match", "in_play", "racing", "combined", "all"}:
        stream_filter = "combined"
    if prepared_history is None:
        prepared_history = prepare_replay_history(
            db, sport=sport, sports=sports, strategy=strategy, days=days, include_demo=include_demo,
            date_from=date_from, date_to=date_to, exchange=exchange, exchanges=exchanges, market=market, search=search,
            engine_instance_id=engine_instance_id, engine_instance_ids=engine_instance_ids,
            execution_mode=execution_mode, minimum_quality_band=minimum_quality_band,
            time_basis=time_basis, require_monitor_evidence=require_monitor_evidence,
        )
    prepared_rows = list(prepared_history.get("rows") or [])
    stream_candidates = [
        row for row in prepared_rows
        if not ((stream_filter == "pre_match" and str(row.get("_row_stream") or "pre_match") != "pre_match")
               or (stream_filter == "in_play" and str(row.get("_row_stream") or "pre_match") != "in_play")
               or (stream_filter == "racing" and str(row.get("_row_stream") or "pre_match") != "racing"))
    ]
    quality_filtered = sum(1 for row in stream_candidates if not _quality_passes(row.get("_quality_band"), quality_floor))
    rows = [row for row in stream_candidates if _quality_passes(row.get("_quality_band"), quality_floor)]
    open_ignored = int(prepared_history.get("open_ignored") or 0)
    rows.sort(key=lambda r: (r.get("settled_at") if time_basis == "settled_at" else r.get("detected_at")) or "")
    open_positions: list[dict] = []
    events: list[dict] = []
    series: list[dict] = []
    realized_profit = 0.0
    deployed_total = 0.0
    peak_concurrent_deployed = 0.0
    counts = {
        "settled_available": len(rows) + quality_filtered, "taken": 0, "skipped_min_profit": 0, "skipped_min_roi": 0,
        "skipped_quality": quality_filtered,
        "skipped_no_capital": 0, "skipped_exchange_balance": 0, "exchange_balance_limited": 0,
        "skipped_no_monitor_evidence": 0, "skipped_monitor_miss": 0,
        "skipped_non_executable": 0, "skipped_result_mapping": 0, "open_ignored": open_ignored,
        "release_estimated": 0, "release_observed": 0,
    }
    first_time = _dt(rows[0].get("detected_at"), datetime.now(timezone.utc)) if rows else datetime.now(timezone.utc)

    def reserved_total():
        return sum(sum(float(v or 0.0) for v in p["stakes"].values()) for p in open_positions)

    def reserved_by_exchange():
        out: dict[str, float] = {}
        for position in open_positions:
            for exchange_name, amount in (position.get("stakes") or {}).items():
                out[exchange_name] = out.get(exchange_name, 0.0) + float(amount or 0.0)
        return out

    def equity_total():
        return sum(wallets.values()) + reserved_total()

    config = (prepared_history.get("config") if prepared_history is not None else None) or db.get_setting("config", {}) or {}
    pre_match_hedge_reserve_pct = min(100.0, max(0.0, float(config.get("pre_match_execution_hedge_reserve_pct", config.get("execution_hedge_reserve_pct", 20.0)) or 0.0)))
    inplay_hedge_reserve_pct = min(100.0, max(0.0, float(config.get("inplay_execution_hedge_reserve_pct", config.get("execution_hedge_reserve_pct", 20.0)) or 0.0)))
    racing_hedge_reserve_pct = min(100.0, max(0.0, float(config.get("racing_execution_hedge_reserve_pct", config.get("execution_hedge_reserve_pct", 20.0)) or 0.0)))
    if hedge_reserve_pct is not None:
        scenario_reserve = min(100.0, max(0.0, float(hedge_reserve_pct)))
        pre_match_hedge_reserve_pct = inplay_hedge_reserve_pct = racing_hedge_reserve_pct = scenario_reserve
    scenario_max_stake = None if max_stake is None else max(0.0, float(max_stake))
    reference_checkpoint_ms = int(config.get("monitor_timing_reference_checkpoint_ms", 250) or 250)
    execution_checkpoint_ms = int(config.get("monitor_execution_checkpoint_ms", 500) or 500)
    hedge_checkpoint_ms = int(config.get("monitor_hedge_checkpoint_ms", 1000) or 1000)
    balance_tolerance = float(config.get("execution_balance_tolerance", 0.10) or 0.0)
    max_slippage_pct = float(config.get("pre_match_execution_max_slippage_pct", config.get("execution_max_slippage_pct", 0.50)) or 0.0)
    inplay_max_slippage_pct = float(config.get("inplay_execution_max_slippage_pct", config.get("execution_max_slippage_pct", 0.50)) or 0.0)
    racing_max_slippage_pct = float(config.get("racing_execution_max_slippage_pct", config.get("execution_max_slippage_pct", 0.50)) or 0.0)
    inplay_delay_model = {
        "betfair": {
            "delay_ms": float(config.get("inplay_betfair_delay_ms", 5000) or 0),
            "adverse_odds_pct_per_second": float(config.get("inplay_adverse_odds_pct_per_second", 0.20) or 0),
            "liquidity_decay_pct_per_second": float(config.get("inplay_liquidity_decay_pct_per_second", 8.0) or 0),
        },
        "matchbook": {
            "delay_ms": float(config.get("inplay_matchbook_delay_ms", 1000) or 0),
            "adverse_odds_pct_per_second": float(config.get("inplay_adverse_odds_pct_per_second", 0.20) or 0),
            "liquidity_decay_pct_per_second": float(config.get("inplay_liquidity_decay_pct_per_second", 8.0) or 0),
        },
    }
    pre_match_max_unhedged_exposure = float(config.get("pre_match_execution_max_unhedged_exposure", config.get("execution_max_unhedged_exposure", 25.0)) or 0.0)
    inplay_max_unhedged_exposure = float(config.get("inplay_execution_max_unhedged_exposure", config.get("execution_max_unhedged_exposure", 25.0)) or 0.0)
    racing_max_unhedged_exposure = float(config.get("racing_execution_max_unhedged_exposure", config.get("execution_max_unhedged_exposure", 25.0)) or 0.0)
    plan_ttl_ms = int(config.get("execution_plan_ttl_ms", 1500) or 1500)

    series.append({"time": first_time.isoformat(), "bankroll": round(starting_capital, 4), "cash_available": round(sum(wallets.values()), 4), "deployed_open": 0.0, "kind": "start"})

    def release(up_to: datetime):
        nonlocal realized_profit, open_positions
        due = sorted((p for p in open_positions if p["release_at"] <= up_to), key=lambda p: p["release_at"])
        future = [p for p in open_positions if p["release_at"] > up_to]
        for pos in due:
            for ex, principal in pos["stakes"].items():
                wallets[ex] = wallets.get(ex, 0.0) + float(principal or 0.0) + float(pos["pnl_by_exchange"].get(ex, 0.0) or 0.0)
            realized_profit += float(pos["pnl"] or 0.0)
            event = events[pos["event_index"]]
            event["capital_after_result"] = round(sum(wallets.values()) + sum(sum(float(v or 0.0) for v in p["stakes"].values()) for p in future), 4)
            event["cash_after_result"] = round(sum(wallets.values()), 4)
            event["exchange_balances_after_result"] = {k: round(v, 4) for k, v in sorted(wallets.items())}
            series.append({"time": pos["release_at"].isoformat(), "bankroll": event["capital_after_result"], "cash_available": round(sum(wallets.values()),4), "deployed_open": round(sum(sum(float(v or 0.0) for v in p["stakes"].values()) for p in future),4), "kind":"settlement", "event":event.get("event_name"), "pnl":round(float(pos["pnl"] or 0.0),4)})
        open_positions = future

    for row in rows:
        row_stream = str(row.get("_row_stream") or ("in_play" if str(row.get("qualification_status") or "qualified") == "in_play_qualified" else "pre_match"))
        row_hedge_reserve_pct = racing_hedge_reserve_pct if row_stream == "racing" else (inplay_hedge_reserve_pct if row_stream == "in_play" else pre_match_hedge_reserve_pct)
        row_max_unhedged_exposure = racing_max_unhedged_exposure if row_stream == "racing" else (inplay_max_unhedged_exposure if row_stream == "in_play" else pre_match_max_unhedged_exposure)
        detected = _dt(row.get("detected_at"), first_time)
        release(detected)
        total_available = sum(wallets.values())
        if total_available <= 0:
            counts["skipped_no_capital"] += 1
            continue
        try: legs = _legs(row)
        except Exception:
            counts["skipped_non_executable"] += 1; continue
        if len(legs) < 2:
            counts["skipped_non_executable"] += 1; continue
        monitor_run = None
        reference_observation = None
        execution_observation = None
        hedge_observation = None
        if require_monitor_evidence:
            if prepared_history is not None:
                monitor_run = (prepared_history.get("monitor_runs") or {}).get((int(row.get("id") or 0), row_stream))
            else:
                monitor_run = db.monitor_timing_run_for_opportunity(int(row.get("id") or 0), stream=row_stream)
            if not monitor_run:
                counts["skipped_no_monitor_evidence"] += 1
                continue
            ref_ms = int(monitor_run.get("reference_checkpoint_ms") or reference_checkpoint_ms)
            observations = list(monitor_run.get("observations") or [])
            reference_observation = next(
                (x for x in observations if int(x.get("offset_ms") or 0) == ref_ms),
                None,
            )
            if not reference_observation:
                counts["skipped_no_monitor_evidence"] += 1
                continue
            if not reference_observation.get("still_executable"):
                counts["skipped_monitor_miss"] += 1
                continue
            quotes = {
                (str(q.get("exchange") or ""), str(q.get("selection") or "")): q
                for q in (reference_observation.get("quotes") or [])
            }
            refreshed = []
            for leg in legs:
                q = quotes.get((str(leg.exchange), str(leg.selection)))
                if not q:
                    refreshed = []
                    break
                refreshed.append(
                    Leg(**{
                        **leg.__dict__,
                        "odds": float(q.get("odds") or leg.odds),
                        "liquidity": float(q.get("liquidity") or leg.liquidity),
                    })
                )
            if len(refreshed) != len(legs):
                counts["skipped_no_monitor_evidence"] += 1
                continue
            legs = refreshed

            def obs_at_or_after(target_ms: int):
                later = [x for x in observations if int(x.get("offset_ms") or 0) >= int(target_ms)]
                if later:
                    return min(later, key=lambda x: int(x.get("offset_ms") or 0))
                return max(observations, key=lambda x: int(x.get("offset_ms") or 0)) if observations else None

            execution_observation = obs_at_or_after(max(execution_checkpoint_ms, ref_ms + 1))
            execution_offset = int((execution_observation or {}).get("offset_ms") or 0)
            hedge_observation = obs_at_or_after(max(hedge_checkpoint_ms, execution_offset + 1))

        base = simulate_equal_return(legs, Scenario("analysis", total_available, 100.0, max_event_exposure_pct))
        if not base.get("executable"):
            counts["skipped_non_executable"] += 1
            continue
        if scenario_max_stake and scenario_max_stake > 0:
            largest_stake = max((float(x.get("stake") or 0.0) for x in (base.get("stakes") or [])), default=0.0)
            if largest_stake > scenario_max_stake + 1e-9:
                factor = scenario_max_stake / largest_stake
                base = scale_simulation(base, factor, total_bankroll=equity_total())
                base.pop("wallet_scale_factor", None)
                base["scenario_stake_scale_factor"] = round(factor, 8)
                base["limited_by"] = "max_stake"
                counts["stake_capped"] = int(counts.get("stake_capped") or 0) + 1

        reserved_exchange = reserved_by_exchange()
        free_normal = {}
        for ex, available in wallets.items():
            equity = float(available or 0.0) + float(reserved_exchange.get(ex, 0.0) or 0.0)
            free_normal[ex] = max(0.0, float(available or 0.0) - equity * row_hedge_reserve_pct / 100.0)
        sim, limiting_exchange = fit_simulation_to_wallets(base, free_normal, total_bankroll=equity_total())
        if float(sim.get("wallet_scale_factor", 1.0) or 0.0) < 0.999999:
            counts["exchange_balance_limited"] += 1
        if not sim.get("executable") or float(sim.get("deployed") or 0.0) <= 0:
            counts["skipped_exchange_balance"] += 1
            continue
        expected_profit = float(sim.get("expected_profit") or 0.0)
        deployed_roi = float(sim.get("expected_roi_pct") or 0.0)
        if expected_profit + 1e-9 < min_profit:
            if limiting_exchange:
                counts["skipped_exchange_balance"] += 1
            else:
                counts["skipped_min_profit"] += 1
            continue
        if deployed_roi + 1e-9 < min_deployed_roi_pct:
            counts["skipped_min_roi"] += 1
            continue

        execution_state = None
        modeled_worst_case_pnl = None
        modeled_best_case_pnl = None
        locked_profit = None
        if require_monitor_evidence:
            plan = build_execution_plan(
                legs,
                sim,
                opportunity_id=int(row.get("id") or 0),
                event_name=row.get("event_name") or row.get("event_key") or "",
                market_name=row.get("market_name") or "",
                ttl_ms=plan_ttl_ms,
                max_slippage_pct=racing_max_slippage_pct if row_stream == "racing" else (inplay_max_slippage_pct if row_stream == "in_play" else max_slippage_pct),
                max_unhedged_exposure=row_max_unhedged_exposure,
                hedge_reserve_pct=row_hedge_reserve_pct,
            )
            fill_fractions, fill_odds, hedge_quotes, _ = model_execution_inputs(
                plan,
                execution_observation,
                hedge_observation,
                delay_model_by_exchange=inplay_delay_model if row_stream == "in_play" else None,
            )
            initial_capital: dict[str, float] = {}
            for plan_leg in plan.legs:
                fraction = max(0.0, min(1.0, float(fill_fractions.get(plan_leg.index, 0.0))))
                stake = float(plan_leg.requested_stake) * fraction
                if stake <= 0:
                    continue
                odds = float(fill_odds.get(plan_leg.index, plan_leg.requested_odds))
                ex = exchange_key(plan_leg.exchange)
                initial_capital[ex] = initial_capital.get(ex, 0.0) + order_capital_required(plan_leg.side, odds, stake)
            hedge_capacity = {
                ex: max(0.0, float(wallets.get(ex, 0.0)) - initial_capital.get(ex, 0.0))
                for ex in wallets
            }
            execution = PaperExecutionCoordinator(balance_tolerance=balance_tolerance).execute(
                plan,
                fill_fractions=fill_fractions,
                fill_odds=fill_odds,
                hedge_quotes=hedge_quotes,
                hedge_capital_by_exchange=hedge_capacity,
                auto_hedge=True,
            )
            if not execution.fills:
                counts["skipped_monitor_miss"] += 1
                continue
            stakes_by_exchange = capital_required_by_exchange_from_fills(execution.fills)
            if any(float(need or 0.0) > float(wallets.get(ex, 0.0) or 0.0) + 1e-8 for ex, need in stakes_by_exchange.items()):
                counts["skipped_exchange_balance"] += 1
                continue
            outcome_exchange = exchange_outcome_pnls_from_fills(plan.outcomes, execution.fills)
            execution_state = execution.state.value
            modeled_worst_case_pnl = float(execution.captured_profit)
            modeled_best_case_pnl = float(execution.after_hedge.best_case_pnl)
            if bool(execution.after_hedge.balanced) and execution.state.value in {"COMPLETE", "HEDGED"}:
                locked_profit = modeled_worst_case_pnl
        else:
            stakes_by_exchange = capital_by_exchange(sim)
            if any(float(need or 0.0) > wallets.get(ex, 0.0) + 1e-8 for ex, need in stakes_by_exchange.items()):
                counts["skipped_exchange_balance"] += 1
                continue
            outcome_exchange = exchange_outcome_pnls(legs, sim)

        target = next(
            (v for k, v in outcome_exchange.items() if str(k).strip().lower() == str(row.get("outcome") or "").strip().lower()),
            None,
        )
        if target is None:
            counts["skipped_result_mapping"] += 1
            continue
        pnl = sum(float(v or 0.0) for v in target.values())
        actual_deployed = sum(float(v or 0.0) for v in stakes_by_exchange.values())
        equity_before = equity_total()
        cash_before = sum(wallets.values())
        wallet_before = dict(wallets)
        for ex, need in stakes_by_exchange.items():
            wallets[ex] = wallets.get(ex, 0.0) - float(need or 0.0)
        observed_settled = _dt(row.get("settled_at"), detected)
        if release_policy == "observed": release_at, release_basis = max(detected, observed_settled), "observed_settlement"
        else: release_at, release_basis = _estimated_release_time(row, detected)
        counts["release_estimated" if release_basis=="estimated_event_close" else "release_observed"] += 1
        event = {
            "id": int(row.get("id") or 0), "monitor_stream": row_stream, "detected_at": row.get("detected_at"), "settled_at": row.get("settled_at"),
            "release_at": release_at.isoformat(), "release_basis": release_basis, "sport": row.get("sport") or (legs[0].sport if legs else "Unknown"),
            "quality_band": row.get("_quality_band") or "Tiny", "quality_score": row.get("_quality_score"),
            "strategy": row.get("strategy") or "1x2", "engine_instance_id": row.get("engine_instance_id"), "engine_type": row.get("engine_type"), "engine_version": row.get("engine_version"), "engine_config_version": row.get("engine_config_version"), "event_name": row.get("event_name") or row.get("event_key"), "market_name": row.get("market_name"),
            "outcome": row.get("outcome"), "capital_before": round(equity_before,4), "cash_available_before": round(cash_before,4),
            "exchange_balances_before": {k:round(v,4) for k,v in sorted(wallet_before.items())}, "exchange_stakes": {k:round(v,4) for k,v in sorted(stakes_by_exchange.items())},
            "deployed": round(actual_deployed,4), "expected_profit": round(expected_profit,4), "expected_roi_pct": round(deployed_roi,6),
            "realized_pnl": round(pnl,4), "deployed_roi_pct": round((pnl/actual_deployed)*100.0,6) if actual_deployed > 0 else 0.0,
            "bankroll_roi_pct_at_entry": round((pnl/equity_before)*100.0,6) if equity_before>0 else 0.0,
            "limited_by": sim.get("limited_by"), "limiting_exchange": limiting_exchange,
            "execution_state": execution_state,
            "modeled_worst_case_pnl": None if modeled_worst_case_pnl is None else round(modeled_worst_case_pnl,4),
            "modeled_best_case_pnl": None if modeled_best_case_pnl is None else round(modeled_best_case_pnl,4),
            "locked_profit": None if locked_profit is None else round(locked_profit,4),
            "locked_return_pct": None if locked_profit is None or actual_deployed <= 0 else round((locked_profit/actual_deployed)*100.0,6),
            "capital_after_result": None, "cash_after_result": None,
        }
        event_index = len(events); events.append(event)
        open_positions.append({"release_at":release_at,"stakes":stakes_by_exchange,"pnl_by_exchange":target,"pnl":pnl,"event_index":event_index})
        counts["taken"] += 1
        deployed_total += actual_deployed
        peak_concurrent_deployed = max(peak_concurrent_deployed, reserved_total())
        series.append({"time":detected.isoformat(),"bankroll":round(equity_before,4),"cash_available":round(sum(wallets.values()),4),"deployed_open":round(reserved_total(),4),"kind":"entry","event":event.get("event_name")})

    release(datetime.max.replace(tzinfo=timezone.utc))
    ending_capital = sum(wallets.values()) + reserved_total()
    values = [float(x.get("bankroll") or starting_capital) for x in series]
    peak = starting_capital; max_drawdown_pct = 0.0
    for value in values:
        peak=max(peak,value)
        if peak>0: max_drawdown_pct=max(max_drawdown_pct,((peak-value)/peak)*100.0)
    by_sport = {}
    for event in events:
        name=str(event.get("sport") or "Unknown"); b=by_sport.setdefault(name,{"sport":name,"events":0,"deployed":0.0,"profit":0.0,"positive":0})
        b["events"]+=1; b["deployed"]+=float(event.get("deployed") or 0.0); b["profit"]+=float(event.get("realized_pnl") or 0.0); b["positive"]+=int(float(event.get("realized_pnl") or 0.0)>0)
    sport_rows=[]
    for b in by_sport.values():
        dep=float(b["deployed"] or 0.0); n=int(b["events"] or 0)
        sport_rows.append({"sport":b["sport"],"events":n,"positive":int(b["positive"]),"deployed":round(dep,2),"profit":round(float(b["profit"]),4),"profit_per_event":round(float(b["profit"])/n,4) if n else 0.0,"return_on_deployed_pct":round((float(b["profit"])/dep)*100.0,6) if dep>0 else 0.0})
    sport_rows.sort(key=lambda x:(x["profit"],x["events"]), reverse=True)
    realized_roi=(realized_profit/starting_capital)*100.0 if starting_capital>0 else 0.0
    locked_events = [e for e in events if e.get("locked_profit") is not None]
    locked_profit_total = sum(float(e.get("locked_profit") or 0.0) for e in locked_events)
    locked_deployed_total = sum(float(e.get("deployed") or 0.0) for e in locked_events)
    locked_return_on_deployed = (locked_profit_total/locked_deployed_total)*100.0 if locked_deployed_total>0 else 0.0
    return {
        "filters":{"starting_capital":round(starting_capital,2),"exchange_balances":{k:round(v,2) for k,v in starting_wallets.items()},"venue_balances":{k:round(v,2) for k,v in starting_wallets.items()},"minimum_profit":round(min_profit,4),"minimum_deployed_roi_pct":round(min_deployed_roi_pct,6),"max_event_exposure_pct":round(max_event_exposure_pct,4),"max_stake":scenario_max_stake,"hedge_reserve_pct":hedge_reserve_pct,"sport":sport_filter or "all","sports":list(sports or []),"strategy":strategy_filter or "all","engine_instance_id":str(engine_instance_id or "all"),"engine_instance_ids":list(engine_instance_ids or []),"days":days,"date_from":date_from.isoformat() if date_from else None,"date_to":date_to.isoformat() if date_to else None,"exchange":exchange_filter or "all","exchanges":list(exchanges or []),"market":market_filter or "all","search":search_filter,"execution_mode":execution_filter or "all","monitor_stream":stream_filter,"monitor_streams":sorted(selected_streams),"minimum_quality_band":quality_floor,"release_policy":release_policy,"require_monitor_evidence":bool(require_monitor_evidence),"time_basis":time_basis},
        "starting_capital":round(starting_capital,2),"ending_capital":round(ending_capital,4),"realized_profit":round(realized_profit,4),"realized_roi_pct":round(realized_roi,6),
        "total_deployed":round(deployed_total,2),"return_on_deployed_pct":round((realized_profit/deployed_total)*100.0,6) if deployed_total>0 else 0.0,"average_profit":round(realized_profit/counts["taken"],4) if counts["taken"] else 0.0,
        "locked_profit":round(locked_profit_total,4),"locked_deployed":round(locked_deployed_total,4),"locked_return_on_deployed_pct":round(locked_return_on_deployed,6),
        "positive_results":sum(1 for e in events if float(e.get("realized_pnl") or 0.0)>0),"negative_results":sum(1 for e in events if float(e.get("realized_pnl") or 0.0)<0),
        "peak_concurrent_deployed":round(peak_concurrent_deployed,2),"peak_capital_tied_pct":round((peak_concurrent_deployed/starting_capital)*100.0,4) if starting_capital>0 else 0.0,"max_drawdown_pct":round(max_drawdown_pct,6),
        "counts":counts,"series":sorted(series,key=lambda x:x.get("time") or ""),"events":sorted(events,key=lambda x:x.get("settled_at") or x.get("detected_at") or "", reverse=True),"by_sport":sport_rows,
        "venue_balances":{"starting":{k:round(v,4) for k,v in starting_wallets.items()},"ending":{k:round(v,4) for k,v in wallets.items()}},
        "exchange_balances":{"starting":{k:round(v,4) for k,v in starting_wallets.items()},"ending":{k:round(v,4) for k,v in wallets.items()}},
    }

def replay_scenarios(db: DB, capitals: list[float], max_event_exposure_pct: float = 100.0, include_demo: bool = False) -> list[dict]:
    return [replay_history(db, float(c), max_event_exposure_pct, include_demo=include_demo) for c in capitals if float(c) > 0]
