from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.engine import best_strategy_legs, strategy_routing_diagnostics
from arbscanner.models import Leg
from arbscanner.scanner import Scanner

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
NOTES = (ROOT / "RELEASE_NOTES.md").read_text()
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text()


def _leg(venue: str, selection: str, odds: float = 3.5, liquidity: float = 1000.0, commission: float = 2.0) -> Leg:
    exchange = "Betfair delayed" if venue == "betfair" else "Matchbook"
    return Leg(
        exchange, selection, odds, liquidity, commission,
        market_id=f"{venue}-m", selection_id=f"{venue}-{selection.lower()}",
        provider_id=venue, venue_id=venue, canonical_selection_key=selection.lower(),
        canonical_selection_id=selection.lower(),
    )


def _route(legs: list[Leg]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((x.selection, x.resolved_venue_id) for x in legs))


def test_v0956_release_identity():
    assert __version__ == "0.9.56"
    assert '<title>ArbScanner PoC 0.9.56</title>' in HTML
    assert 'EXPECTED_VERSION="0.9.56"' in INSTALLER
    assert "## 0.9.56 — Scenarios Layout & Exchange Routing Integrity" in NOTES


def test_equal_two_way_market_does_not_inherit_provider_enumeration_order():
    forward = {
        "Home": [_leg("betfair", "Home", 2.2), _leg("matchbook", "Home", 2.2)],
        "Away": [_leg("betfair", "Away", 2.2), _leg("matchbook", "Away", 2.2)],
    }
    reversed_quotes = {selection: list(reversed(rows)) for selection, rows in forward.items()}
    a = best_strategy_legs(forward)
    b = best_strategy_legs(reversed_quotes)
    assert _route(a) == _route(b)
    assert sum(x.resolved_venue_id == "betfair" for x in a) == 1
    assert sum(x.resolved_venue_id == "matchbook" for x in a) == 1


def test_equal_three_way_routing_is_provider_order_independent_and_audited():
    forward = {s: [_leg("betfair", s), _leg("matchbook", s)] for s in ("Home", "Draw", "Away")}
    reversed_quotes = {s: list(reversed(qs)) for s, qs in forward.items()}

    a = best_strategy_legs(forward)
    b = best_strategy_legs(reversed_quotes)
    assert _route(a) == _route(b)
    assert {x.resolved_venue_id for x in a} == {"betfair", "matchbook"}

    diag = strategy_routing_diagnostics(forward, a)
    assert diag["economic_tie"] is True
    assert diag["reason"] == "venue_neutral_tiebreak"
    assert diag["alternatives"]
    assert sum(diag["selected_legs_per_exchange"].values()) == 3
    assert diag["favourite_exchange"] in {"betfair", "matchbook"}


def test_wallet_balance_tiebreak_moves_equivalent_route_toward_healthier_wallet():
    quotes = {s: [_leg("betfair", s), _leg("matchbook", s)] for s in ("Home", "Draw", "Away")}
    bf_rich = {
        "betfair": {"available": 1000, "reserved": 0, "equity": 1000},
        "matchbook": {"available": 100, "reserved": 0, "equity": 100},
    }
    mb_rich = {
        "betfair": {"available": 100, "reserved": 0, "equity": 100},
        "matchbook": {"available": 1000, "reserved": 0, "equity": 1000},
    }
    route_bf = best_strategy_legs(quotes, venue_wallets=bf_rich)
    route_mb = best_strategy_legs(quotes, venue_wallets=mb_rich)
    assert sum(x.resolved_venue_id == "betfair" for x in route_bf) > sum(x.resolved_venue_id == "betfair" for x in route_mb)
    assert strategy_routing_diagnostics(quotes, route_bf, venue_wallets=bf_rich)["reason"] == "wallet_balance_tiebreak"
    assert strategy_routing_diagnostics(quotes, route_mb, venue_wallets=mb_rich)["reason"] == "wallet_balance_tiebreak"


def test_wallet_balance_never_overrides_materially_better_guaranteed_book():
    # Betfair Home has a materially better price. A wallet preference is allowed to
    # break true economic ties, not throw away guaranteed portfolio profit.
    quotes = {
        "Home": [_leg("betfair", "Home", 3.7), _leg("matchbook", "Home", 3.5)],
        "Draw": [_leg("betfair", "Draw", 3.5), _leg("matchbook", "Draw", 3.5)],
        "Away": [_leg("betfair", "Away", 3.5), _leg("matchbook", "Away", 3.5)],
    }
    wallets = {
        "betfair": {"available": 100, "reserved": 80, "equity": 180},
        "matchbook": {"available": 5000, "reserved": 0, "equity": 5000},
    }
    selected = best_strategy_legs(quotes, venue_wallets=wallets)
    home = next(x for x in selected if x.selection == "Home")
    assert home.resolved_venue_id == "betfair"
    assert home.odds == pytest.approx(3.7)


def test_exact_winner_mapping_and_fail_closed_unknown_mapping():
    opp = {
        "legs_json": json.dumps([
            asdict(_leg("betfair", "Home")),
            asdict(_leg("matchbook", "Away")),
        ])
    }
    source = {"runner_keys": {"101": "home", "202": "away"}}

    exact_id = Scanner._resolve_settlement_winner({"winner": "Home", "winner_id": "betfair-home"}, opp, source)
    assert exact_id["ok"] is True
    assert exact_id["mapping_method"] == "provider_selection_id"
    assert exact_id["winner"] == "Home"

    canonical = Scanner._resolve_settlement_winner({"winner": "Completely Different Label", "winner_id": "202"}, opp, source)
    assert canonical["ok"] is True
    assert canonical["mapping_method"] == "canonical_selection"
    assert canonical["winner"] == "Away"

    failed = Scanner._resolve_settlement_winner({"winner": "Unrelated runner xyz", "winner_id": "999"}, opp, source)
    assert failed["ok"] is False
    assert failed["code"] == "SETTLEMENT_MAPPING_ERROR"


def _open_recon_position(api: API, *, corrupt: bool = False) -> int:
    api.db.reset_monitor_wallets({"betfair": 200.0, "matchbook": 200.0})
    legs = [_leg("betfair", "A", 2.2), _leg("matchbook", "B", 2.2)]
    oid = api.db.add_opportunity(
        "evt-956", "A v B", None, "Match Winner", 1.0, 1.0,
        [asdict(x) for x in legs], [], 1.0, f"sig-956-{int(corrupt)}",
        routing_diagnostics={"economic_tie": True, "reason": "wallet_balance_tiebreak", "favourite_exchange": "betfair", "alternatives": [{"legs": []}]},
    )
    a_bf = 59.0 if corrupt else 58.8
    outcome_pnls = {
        "A": {"betfair": a_bf, "matchbook": -50.0},
        "B": {"betfair": -50.0, "matchbook": 58.8},
    }
    simulation = {
        "fills": [
            {"venue_id": "betfair", "exchange": "Betfair delayed", "selection": "A", "side": "BACK", "stake": 50.0, "odds": 2.2, "commission_pct": 2.0},
            {"venue_id": "matchbook", "exchange": "Matchbook", "selection": "B", "side": "BACK", "stake": 50.0, "odds": 2.2, "commission_pct": 2.0},
        ]
    }
    ok, reason = api.db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="evt-956", market_name="Match Winner",
        deployed=100.0, expected_profit=8.8, stakes_by_exchange={"betfair": 50.0, "matchbook": 50.0},
        outcome_exchange_pnls=outcome_pnls, simulation=simulation, hedge_reserve_pct=0,
    )
    assert ok, reason
    return oid


def test_exchange_contributions_reconcile_to_realized_pnl_after_commission(tmp_path: Path):
    api = API(tmp_path / "reconcile-ok.sqlite3")
    oid = _open_recon_position(api)
    result = api.db.settle_monitor_position(oid, "A")
    assert result["ok"] is True
    assert result["reconciliation_status"] == "OK"
    assert result["reconciliation_delta"] == pytest.approx(0.0, abs=1e-8)
    assert sum(result["by_exchange"].values()) == pytest.approx(result["realized_pnl"], abs=1e-8)
    assert result["gross_by_exchange"]["betfair"] == pytest.approx(60.0)
    assert result["commission_by_exchange"]["betfair"] == pytest.approx(1.2)
    assert result["model_net_by_exchange"]["betfair"] == pytest.approx(58.8)


def test_reconciliation_error_fails_closed_before_wallet_mutation(tmp_path: Path):
    api = API(tmp_path / "reconcile-bad.sqlite3")
    oid = _open_recon_position(api, corrupt=True)
    before = api.db.monitor_wallet_snapshot(0, "pre_match")
    result = api.db.settle_monitor_position(oid, "A")
    after = api.db.monitor_wallet_snapshot(0, "pre_match")
    assert result["ok"] is False
    assert result["reason"] == "settlement_reconciliation_error"
    assert before == after
    status = api.db.conn.execute("SELECT status FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone()[0]
    assert status == "OPEN"


def test_settlement_mapping_audit_persists_error_without_financial_settlement(tmp_path: Path):
    api = API(tmp_path / "mapping-audit.sqlite3")
    oid = api.db.add_opportunity("evt", "A v B", None, "Match Winner", 1, 1, [], [], 1.0, "audit-956")
    audit_id = api.db.record_settlement_audit(
        oid, status="SETTLEMENT_MAPPING_ERROR", raw_provider_winner="Unknown", provider_winner_id="999",
        stored_selections=[{"selection": "A"}, {"selection": "B"}], mapping_method="unresolved",
        mapping_confidence=0.42, reconciliation_status="NOT_SETTLED",
    )
    assert audit_id > 0
    row = api.db.settlement_audits(oid)[0]
    assert row["status"] == "SETTLEMENT_MAPPING_ERROR"
    assert row["raw_provider_winner"] == "Unknown"
    assert json.loads(row["stored_selections_json"])[0]["selection"] == "A"
    assert api.db.conn.execute("SELECT COUNT(*) FROM settlements WHERE opportunity_id=?", (oid,)).fetchone()[0] == 0
    assert api.db.conn.execute("SELECT status FROM opportunities WHERE id=?", (oid,)).fetchone()[0] == "settlement_mapping_error"


def test_routing_aggregate_exposes_held_favourite_winner_and_tie_diagnostics(tmp_path: Path):
    api = API(tmp_path / "routing-diag.sqlite3")
    oid = _open_recon_position(api)
    api.db.record_settlement_audit(oid, status="SETTLED", canonical_winner="A", winning_exchange="betfair", settlement_contributions={"betfair": 58.8, "matchbook": -50}, total_realized_pnl=8.8, reconciliation_status="OK", reconciliation_delta=0)
    diag = api.db.exchange_routing_diagnostics()
    assert diag["positions"] == 1
    assert diag["economic_ties"] == 1
    assert diag["positions_with_equivalent_routes"] == 1
    assert diag["held_outcomes"] == {"betfair": 1, "matchbook": 1}
    assert diag["favourite_outcomes"]["betfair"] == 1
    assert diag["winning_outcomes"]["betfair"] == 1


def test_dashboard_exposes_exchange_contribution_wallet_drift_and_routing_evidence(tmp_path: Path):
    api = API(tmp_path / "dashboard-drift.sqlite3")
    oid = _open_recon_position(api)
    result = api.db.settle_monitor_position(oid, "A")
    assert result["ok"] is True
    api.db.settle(oid, "A")
    api.db.record_settlement_audit(
        oid, status="SETTLED", canonical_winner="A", winning_exchange="betfair",
        settlement_contributions=result["by_exchange"], total_realized_pnl=result["realized_pnl"],
        reconciliation_status="OK", reconciliation_delta=0,
    )
    overview = api.dashboard_overview({})
    bf = overview["venue_metrics"]["betfair"]
    mb = overview["venue_metrics"]["matchbook"]
    assert bf["profit_today_basis"] == "exchange_settlement_contribution"
    assert bf["net_capital_migration"] == pytest.approx(58.8)
    assert mb["net_capital_migration"] == pytest.approx(-50.0)
    assert bf["unexplained_migration"] == pytest.approx(0.0, abs=1e-8)
    assert overview["wallet_drift"]["classification"] in {"BALANCED", "SETTLEMENT_DRIVEN_MIGRATION", "ROUTING_IMBALANCE_REVIEW"}
    assert overview["routing_diagnostics"]["winning_outcomes"]["betfair"] == 1


def test_dashboard_latency_falls_back_to_scan_stage_and_completed_duration(tmp_path: Path):
    api = API(tmp_path / "latency-fallback.sqlite3")
    now = datetime.now(timezone.utc)
    api.db.conn.execute(
        """INSERT INTO scan_runs(started_at,finished_at,markets_seen,matches_seen,status_json,error,processed_candidates,positive_opportunities,qualified_count,executed_count,duration_ms,scan_kind,stage_timings_json,cache_entries,stale_rejections)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ((now-timedelta(milliseconds=650)).isoformat(), now.isoformat(), 20, 5,
         json.dumps([{"exchange":"Betfair","ok":True,"markets":12},{"exchange":"Matchbook","ok":True,"markets":8}]),
         None, 5, 2, 1, 1, 0, "price", json.dumps({"betfair_fetch_ms":123,"matchbook_fetch_ms":247}), 5, 0),
    )
    api.db.conn.execute(
        """INSERT INTO scan_runs(started_at,finished_at,markets_seen,matches_seen,status_json,error,processed_candidates,positive_opportunities,qualified_count,executed_count,duration_ms,scan_kind,stage_timings_json,cache_entries,stale_rejections)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ((now-timedelta(milliseconds=430)).isoformat(), now.isoformat(), 20, 5, "[]", None, 0, 0, 0, 0, 0, "discovery", json.dumps({"catalogue_ms":410}), 5, 0),
    )
    api.db.conn.commit()
    api.service.status = lambda: {"loaded": True}
    ops = api.live_activity_status({"mode":"sim"})["operations"]
    feeds = {x["key"]: x for x in ops["feeds"]}
    assert feeds["betfair"]["selected_mode_latency_ms"] == 123
    assert feeds["matchbook"]["selected_mode_latency_ms"] == 247
    assert ops["price_scanner"]["duration_ms"] >= 600
    assert ops["discovery"]["duration_ms"] >= 400
    assert ops["monitor"]["latency_ms"] == 247


def test_dashboard_and_scenarios_final_ui_ownership_is_explicit():
    assert "Settlement Contribution Today" in HTML
    assert "dashboard-wallet-drift0956" in HTML
    assert 'id="v0956-dashboard-status-integrity-js"' in HTML
    assert "commitDashboardStatus0956" in HTML
    assert "providerLatency0956" in HTML
    assert 'id="v0956-layout-integrity-css"' in HTML
    css = HTML.split('<style id="v0956-layout-integrity-css">', 1)[1].split('</style>', 1)[0]
    assert "height:calc(100vh - 147px)" in css
    assert "max-height:none" in css
    assert "grid-template-columns:205px minmax(0,1fr) 205px" in css
