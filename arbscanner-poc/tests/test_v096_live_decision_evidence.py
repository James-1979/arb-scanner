from __future__ import annotations

import inspect
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, DEFAULT_CONFIG
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Leg, MarketMatch, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def make_scanner(tmp_path: Path):
    db = DB(tmp_path / "v096.sqlite3")
    secrets = SecretStore(tmp_path / "secrets.json")
    return db, Scanner(db, secrets)


def legs(feed_a="live", feed_b="live", age_a=20, age_b=30):
    now = datetime.now(timezone.utc).isoformat()
    return [
        Leg("Venue A", "Home", 2.2, 1000, provider_id="venue_a", venue_id="venue_a", feed_entitlement=feed_a,
            captured_at=now, quote_age_ms=age_a, market_status="OPEN", timestamp_quality="LOCAL_RECEIPT"),
        Leg("Venue B", "Away", 2.2, 1000, provider_id="venue_b", venue_id="venue_b", feed_entitlement=feed_b,
            captured_at=now, quote_age_ms=age_b, market_status="OPEN", timestamp_quality="LOCAL_RECEIPT"),
    ]


def match():
    return MarketMatch(
        event_key="event-1", market_key="match-odds", display_event="A v B", display_market="Match Odds",
        start_time=None, markets=[], match_score=0.99, market_type="match odds", strategy="1x2", sport="Football",
        in_play=False, status="OPEN", section="sports", canonical_event_id="ce:event-1", canonical_market_id="cm:event-1:match-odds",
    )


def evidence(scanner: Scanner, *, feeds=("live", "live"), ages=(20, 30), revision="rev-1"):
    selected = legs(feeds[0], feeds[1], ages[0], ages[1])
    timing = scanner._timing_evidence(selected)
    cfg = {**DEFAULT_CONFIG, "live_decision_max_quote_age_seconds": 1.0, "live_decision_max_receipt_spread_ms": 1000,
           "live_decision_min_mapping_confidence": 0.72}
    return scanner._build_live_decision_evidence(
        mm=match(), status="recommended", reason="qualified", selected_legs=selected, diagnostic_legs=selected,
        profile={}, timing_evidence=timing, cfg=cfg, theoretical=9.0909, net_roi=10.0, max_executable_stake=1000.0,
        limiting_provider=None, limiting_selection=None, limiting_side=None, liquidity_capable=True,
        reference_bankroll=500.0, reference_cap_pct=10.0, max_event_exposure_pct=100.0,
        decision_started=time.perf_counter(), book_revision=revision,
    )


def test_release_identity_and_frontend_contract():
    assert __version__ == "0.9.36"
    assert "ArbScanner PoC 0.9.36" in HTML
    assert "function liveDecisionRead" in HTML
    assert "LIVE heatmap uses isolated decision rollups; no SIM execution/P&amp;L rows are used." in HTML
    assert "function syncBackendDataContextMode" in HTML
    assert "set_data_context_mode" in HTML


def test_data_context_live_does_not_unlock_economic_mode(tmp_path):
    api = API(tmp_path / "api.sqlite3")
    result = api.set_data_context_mode({"mode": "live"})
    assert result["ok"] is True
    assert result["data_context_mode"] == "live"
    assert result["economic_execution_mode"] == "sim"
    assert result["live_execution_allowed"] is False
    assert result["orders_write_capability"] is False
    state = api.get_state()
    assert state["settings"]["mode"] == "sim"
    assert state["settings"]["data_context_mode"] == "live"


def test_backend_data_context_rejects_late_stale_mode_write(tmp_path):
    api = API(tmp_path / "mode-race.sqlite3")
    first = api.set_data_context_mode({"mode": "live", "generation": 200})
    stale = api.set_data_context_mode({"mode": "sim", "generation": 100})
    assert first["data_context_mode"] == "live"
    assert stale["stale_request"] is True
    assert stale["data_context_mode"] == "live"
    final = api.set_data_context_mode({"mode": "sim", "generation": 300})
    assert final["data_context_mode"] == "sim"
    assert api.db.get_setting("data_context_mode") == "sim"


def test_execution_grade_known_arb_uses_existing_simulator(tmp_path):
    db, scanner = make_scanner(tmp_path)
    e = evidence(scanner)
    assert e["application_mode"] == "live"
    assert e["decision_type"] == "simulated"
    assert e["evidence_quality"] == "EXECUTION_GRADE"
    assert e["state"] == "SIM_FULL_FILL"
    assert e["expected_simulated_profit"] > 0
    assert e["qualification"]["feed_quality_pass"] is True
    assert e["simulation"]["staking_method"] == "commission_aware_net_equal_return"


def test_delayed_leg_stays_observational_and_stale_leg_rejected(tmp_path):
    db, scanner = make_scanner(tmp_path)
    delayed = evidence(scanner, feeds=("delayed", "live"), revision="rev-delayed")
    assert delayed["evidence_quality"] == "OBSERVATIONAL"
    assert delayed["reason_code"] == "DELAYED_DATA"
    stale = evidence(scanner, feeds=("live", "live"), ages=(2500, 20), revision="rev-stale")
    assert stale["evidence_quality"] == "OBSERVATIONAL"
    assert stale["reason_code"] == "STALE_QUOTE"


def test_revision_dedup_is_bounded_and_profit_counted_once(tmp_path):
    db, scanner = make_scanner(tmp_path)
    e = evidence(scanner)
    first = db.record_live_decision(e)
    second = db.record_live_decision(e)
    assert first["created"] is True
    assert second["duplicate_revision"] is True
    latest = db.live_decision_latest_rows()
    assert len(latest) == 1
    assert latest[0]["observation_count"] == 2
    summary = db.live_decision_summary()["summary"]
    assert summary["observed"] == 1
    assert summary["simulated_fills"] == 1
    assert summary["expected_profit_sum"] == round(e["expected_simulated_profit"], 4)


def test_new_revision_is_new_material_decision(tmp_path):
    db, scanner = make_scanner(tmp_path)
    db.record_live_decision(evidence(scanner, revision="rev-1"))
    db.record_live_decision(evidence(scanner, revision="rev-2"))
    assert len(db.live_decision_latest_rows()) == 1
    events = db.live_decision_events_between()
    assert len(events) == 2
    assert {x["book_revision"] for x in events} == {"rev-1", "rev-2"}


def test_live_decision_storage_does_not_touch_sim_or_live_finance(tmp_path):
    api = API(tmp_path / "isolation.sqlite3")
    before_sim = api.account_overview({"mode": "sim", "capture": False})
    before_live_counts = api.db.live_persistence_counts()
    e = evidence(api.scanner)
    for i in range(5):
        e2 = dict(e); e2["book_revision"] = f"iso-{i}"; e2["decision_id"] = ""; api.db.record_live_decision(e2)
    after_sim = api.account_overview({"mode": "sim", "capture": False})
    after_live_counts = api.db.live_persistence_counts()
    before_accounts = {k: (v.get("available"), v.get("reserved"), v.get("equity"), v.get("realized_pnl")) for k, v in before_sim["accounts"].items()}
    after_accounts = {k: (v.get("available"), v.get("reserved"), v.get("equity"), v.get("realized_pnl")) for k, v in after_sim["accounts"].items()}
    assert before_accounts == after_accounts
    assert before_live_counts == after_live_counts
    assert api.db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0
    assert api.db.conn.execute("SELECT COUNT(*) FROM monitor_positions").fetchone()[0] == 0


def test_live_decision_api_is_read_only_and_separated(tmp_path):
    api = API(tmp_path / "read.sqlite3")
    api.db.record_live_decision(evidence(api.scanner))
    r = api.live_decision_evidence({"domain": "sports"})
    assert r["ok"] is True
    assert r["application_mode"] == "live"
    assert r["decision_type"] == "simulated"
    assert r["orders_write_capability"] is False
    assert r["live_execution_allowed"] is False
    assert r["real_orders_sent"] == 0
    assert r["summary"]["execution_grade"] == 1


def test_simulator_has_no_execution_provider_or_order_method_dependency():
    src = inspect.getsource(Scanner._build_live_decision_evidence)
    assert "self.provider_runtime" not in src
    assert "place_order" not in src
    assert "cancel_order" not in src
    assert "replace_order" not in src
    assert "update_order" not in src


def test_zero_execution_grade_is_valid(tmp_path):
    db, scanner = make_scanner(tmp_path)
    for i in range(3):
        db.record_live_decision(evidence(scanner, feeds=("delayed", "live"), revision=f"d-{i}"))
    summary = db.live_decision_summary()["summary"]
    assert summary["observed"] == 3
    assert summary["execution_grade"] == 0
    assert summary["qualified"] == 3



def test_scanner_live_context_routes_to_decision_sink_not_sim_opportunity(tmp_path):
    db, scanner = make_scanner(tmp_path)
    db.set_setting("data_context_mode", "live")
    now = datetime.now(timezone.utc).isoformat()
    def q(exchange, provider, market_id, sid, selection, odds):
        return Quote(exchange=exchange, provider_id=provider, venue_id=provider, event_id=f"{provider}-e", market_id=market_id,
                     event_name="Alpha v Beta", market_name="Match Winner", selection_id=sid, selection=selection,
                     odds=odds, liquidity=500.0, captured_at=now, quote_age_ms=20, commission_pct=0.0,
                     market_type="match winner", strategy="two-way", sport="Tennis", in_play=False, market_status="OPEN",
                     feed_entitlement="live", timestamp_quality="LOCAL_RECEIPT")
    a = ExchangeMarket("Venue A", "a-e", "a-m", "Alpha v Beta", "Match Winner", now,
                       [q("Venue A","venue_a","a-m","1","Alpha",2.2), q("Venue A","venue_a","a-m","2","Beta",1.8)],
                       status="OPEN", market_type="match winner", strategy="two-way", sport="Tennis", in_play=False)
    b = ExchangeMarket("Venue B", "b-e", "b-m", "Alpha v Beta", "Match Winner", now,
                       [q("Venue B","venue_b","b-m","1","Alpha",1.8), q("Venue B","venue_b","b-m","2","Beta",2.2)],
                       status="OPEN", market_type="match winner", strategy="two-way", sport="Tennis", in_play=False)
    mm = MarketMatch("alpha-beta","match-winner","Alpha v Beta","Match Winner",now,[a,b],0.99,
                     market_type="match winner",strategy="two-way",sport="Tennis",in_play=False,status="OPEN",
                     canonical_event_id="ce:alpha-beta",canonical_market_id="cm:alpha-beta:mw")
    cfg = {**DEFAULT_CONFIG, "minimum_net_roi_pct": 1.0, "minimum_profit": 0.0, "minimum_quality_band": "Tiny",
           "minimum_liquidity": 2.0, "live_decision_evidence_enabled": True,
           "live_decision_max_quote_age_seconds": 5.0, "live_decision_max_receipt_spread_ms": 1000}
    scan_id = db.start_scan(scan_kind="price")
    result = asyncio.run(scanner._evaluate_matches_async(scan_id=scan_id,matches=[mm],adapters=[],statuses=[],cfg=cfg,
                                                         fetched_count=2,stage_timings={},cache_entries=2,stale_rejections=0))
    assert result["application_mode"] == "live"
    assert result["decision_evidence"] is True
    assert db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM monitor_positions").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM live_decision_latest").fetchone()[0] == 1
    row = db.live_decision_latest_rows()[0]
    assert row["decision_type"] == "simulated"
    assert row["evidence_quality"] == "EXECUTION_GRADE"

def test_frontend_mode_change_syncs_backend_without_blocking_shell():
    body = HTML[HTML.index("function setGlobalDataMode"):HTML.index("// Override account rendering")]
    assert "void syncBackendDataContextMode(next)" in body
    assert "await syncBackendDataContextMode" not in body
    assert "queueMicrotask(()=>orchestrateRouteLoad(activePageId()))" in body
