from __future__ import annotations

from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB

ROOT = Path(__file__).resolve().parents[1]


def _legs():
    return [
        {"exchange": "Betfair delayed", "provider_id": "betfair", "venue_id": "betfair", "selection": "A", "odds": 2.1, "liquidity": 100},
        {"exchange": "Matchbook", "provider_id": "matchbook", "venue_id": "matchbook", "selection": "B", "odds": 2.1, "liquidity": 100},
    ]


def _settled_baseline(db: DB) -> tuple[int, float]:
    db.ensure_default_engines()
    db.reset_monitor_wallets({"betfair": 100.0, "matchbook": 100.0})
    oid = db.add_opportunity(
        "evt-936", "A v B", None, "Match Winner", 2.0, 1.0, _legs(), [], 1.0, "v0936",
        sport="Tennis", section="sports", engine_instance_id="SPORTS_BASELINE_ARB_PRIMARY",
        engine_type="SPORTS_BASELINE_ARB", engine_version="1.0.0", engine_config_version=1,
    )
    db.set_opportunity_qualification(oid, "qualified", "test")
    ok, reason = db.open_monitor_position(
        opportunity_id=oid, execution_run_id=None, event_key="evt-936", market_name="Match Winner", deployed=20.0,
        expected_profit=1.0, stakes_by_exchange={"betfair": 10.0, "matchbook": 10.0},
        outcome_exchange_pnls={"A": {"betfair": 2.0, "matchbook": -0.5}, "B": {"betfair": -0.5, "matchbook": 2.0}},
        simulation={"after_hedge": {"balanced": True}}, stream="pre_match",
    )
    assert ok, reason
    settled = db.settle_monitor_position(oid, "A")
    assert settled["ok"] is True
    return oid, float(settled["realized_pnl"])


def test_0936_new_origin_is_authoritative_and_reconciles_engine_results(tmp_path):
    db = DB(tmp_path / "life.sqlite3")
    oid, pnl = _settled_baseline(db)
    opp = dict(db.conn.execute("SELECT engine_provenance_source FROM opportunities WHERE id=?", (oid,)).fetchone())
    pos = dict(db.conn.execute("SELECT engine_provenance_source FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone())
    assert opp["engine_provenance_source"] == "runtime_origin"
    assert pos["engine_provenance_source"] == "runtime_origin"

    # One immutable engine evaluation for the same scope. This is the source for Processed/Opportunities.
    db.conn.execute(
        """INSERT INTO engine_evaluations(engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("SPORTS_BASELINE_ARB_PRIMARY", "snap-936", "2026-08-14T08:00:00+00:00", "2026-08-14T08:00:00+00:00", "sim", "sports", "Tennis", "A v B", "Match Winner", "Match Winner", "pre_match", "decision-936", 1),
    )
    db.conn.commit()
    row = next(x for x in db.engine_lifecycle_rows(section="sports", mode="sim") if x["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY")
    assert row["processed"] == 1
    assert row["opportunities"] == 1
    assert row["qualified"] == 1
    assert row["executed"] == 1
    assert row["settled"] == 1
    assert row["realised_pnl"] == round(pnl, 4)


def test_0936_legacy_engine_ids_are_not_claimed_as_authoritative(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = DB(path)
    oid, _ = _settled_baseline(db)
    db.conn.execute("UPDATE opportunities SET engine_provenance_source=NULL WHERE id=?", (oid,))
    db.conn.execute("UPDATE monitor_positions SET engine_provenance_source=NULL WHERE opportunity_id=?", (oid,))
    db.conn.commit(); db.conn.close()
    reopened = DB(path)
    opp = reopened.conn.execute("SELECT engine_provenance_source FROM opportunities WHERE id=?", (oid,)).fetchone()
    pos = reopened.conn.execute("SELECT engine_provenance_source FROM monitor_positions WHERE opportunity_id=?", (oid,)).fetchone()
    assert opp["engine_provenance_source"] == "legacy_unverified"
    assert pos["engine_provenance_source"] == "legacy_unverified"
    row = next(x for x in reopened.engine_lifecycle_rows(section="sports", mode="sim") if x["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY")
    assert row["executed"] == 0
    assert row["settled"] == 0
    assert row["realised_pnl"] == 0


def test_0936_settled_results_preserve_origin_and_mode(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "api.sqlite3")
    oid, pnl = _settled_baseline(api.db)
    out = api.settled_positions({"domain": "sports", "mode": "sim", "limit": 100})
    row = next(x for x in out["rows"] if int(x["opportunity_id"]) == oid)
    assert row["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY"
    assert row["engine_nickname"] == "Baseline"
    assert row["engine_provenance_source"] == "runtime_origin"
    assert row["engine_provenance_authoritative"] is True
    assert row["mode"] == "sim"
    assert row["final_pnl"] == round(pnl, 4)


def test_0936_live_engine_lifecycle_never_reads_sim_opportunities(tmp_path):
    db = DB(tmp_path / "live-isolation.sqlite3")
    oid, _ = _settled_baseline(db)
    assert oid > 0
    db.conn.execute(
        """INSERT INTO engine_evaluations(engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("SPORTS_BASELINE_ARB_PRIMARY", "live-snap-936", "2026-08-14T08:10:00+00:00", "2026-08-14T08:10:00+00:00", "live", "sports", "Tennis", "A v B", "Match Winner", "Match Winner", "pre_match", None, 1),
    )
    db.conn.commit()
    row = next(x for x in db.engine_lifecycle_rows(section="sports", mode="live") if x["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY")
    assert row["processed"] == 1
    assert row["opportunities"] == 1
    assert row["qualified"] == 0
    assert row["executed"] == 0
    assert row["settled"] == 0
    assert row["realised_pnl"] == 0


def test_0936_evaluation_ledger_separates_opportunity_from_qualification(tmp_path):
    db = DB(tmp_path / "evaluation-semantics.sqlite3")
    db.ensure_default_engines()
    db.conn.executemany(
        """INSERT INTO engine_evaluations(engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("SPORTS_BASELINE_ARB_PRIMARY", "snap-a", "2026-08-14T08:00:00+00:00", "2026-08-14T08:00:00+00:00", "sim", "sports", "Football", "A v B", "Match Odds", "Match Odds", "pre_match", None, 0),
            ("SPORTS_BASELINE_ARB_PRIMARY", "snap-b", "2026-08-14T08:01:00+00:00", "2026-08-14T08:01:00+00:00", "sim", "sports", "Football", "C v D", "Match Odds", "Match Odds", "pre_match", None, 1),
            ("SPORTS_BASELINE_ARB_PRIMARY", "snap-c", "2026-08-14T08:02:00+00:00", "2026-08-14T08:02:00+00:00", "sim", "sports", "Football", "E v F", "Match Odds", "Match Odds", "pre_match", "decision-c", 1),
        ],
    )
    db.conn.commit()
    row = next(x for x in db.engine_lifecycle_rows(section="sports", mode="sim") if x["engine_instance_id"] == "SPORTS_BASELINE_ARB_PRIMARY")
    assert row["processed"] == 3
    assert row["opportunities"] == 2
    assert row["qualified"] == 1


def test_0936_ui_uses_shared_lifecycle_terms_and_global_mode_only():
    html = (ROOT / "frontend" / "index.html").read_text()
    for token in (
        'id="monitorEngine0917"', 'id="monitorMarket0936"', 'id="monitorEngineChips0936"',
        'id="positionResultsEngineChips0936"', 'id="sportsEnginePeriod0936"',
        'Processed', 'Opportunities', 'Qualified', 'Executed', 'Settled', 'Settled P&amp;L',
        'function openEngineResults0936', 'authoritative settlement ledger', 'ORIGIN STORED',
    ):
        assert token in html
    assert 'id="monitorMode0917"' not in html
    assert 'id="positionResultsMode0917"' not in html
    assert 'SIM + LIVE kept separate' not in html
    assert 'Decisions</th>' not in html


def test_0936_competing_engine_cannot_claim_another_engines_execution_or_pnl(tmp_path, monkeypatch):
    home = tmp_path / "home-competition"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "competition.sqlite3")
    oid, pnl = _settled_baseline(api.db)

    # SuperBet may evaluate and qualify the same market evidence, but it did not
    # originate the executed position. Its lifecycle must therefore stop before
    # Executed/Settled/P&L for this record.
    api.db.conn.execute(
        """INSERT INTO engine_evaluations(engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("SPORTS_SUPERBET_ARB_PRIMARY", "snap-superbet-936", "2026-08-14T08:20:00+00:00", "2026-08-14T08:20:00+00:00", "sim", "sports", "Tennis", "A v B", "Match Winner", "Match Winner", "pre_match", "decision-superbet-936", 1),
    )
    api.db.conn.commit()

    rows = {r["engine_instance_id"]: r for r in api.db.engine_lifecycle_rows(section="sports", mode="sim")}
    baseline = rows["SPORTS_BASELINE_ARB_PRIMARY"]
    superbet = rows["SPORTS_SUPERBET_ARB_PRIMARY"]
    assert baseline["executed"] == 1
    assert baseline["settled"] == 1
    assert baseline["realised_pnl"] == round(pnl, 4)
    assert superbet["processed"] == 1
    assert superbet["opportunities"] == 1
    assert superbet["qualified"] == 1
    assert superbet["executed"] == 0
    assert superbet["settled"] == 0
    assert superbet["realised_pnl"] == 0

    baseline_results = api.settled_positions({"domain": "sports", "mode": "sim", "engine": "Baseline", "limit": 100})
    superbet_results = api.settled_positions({"domain": "sports", "mode": "sim", "engine": "SuperBet", "limit": 100})
    assert any(int(r["opportunity_id"]) == oid for r in baseline_results["rows"])
    assert not any(int(r["opportunity_id"]) == oid for r in superbet_results["rows"])
