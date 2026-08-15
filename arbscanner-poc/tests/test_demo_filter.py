from pathlib import Path

from arbscanner.db import DB


def add_opportunity(db: DB, name: str, is_demo: bool):
    oid = db.add_opportunity(
        f"key-{name}", name, None, "Match Odds", 1.0, 0.5,
        [{"exchange": "A", "selection": "Home", "odds": 2.0, "liquidity": 100.0, "commission_pct": 0.0},
         {"exchange": "B", "selection": "Away", "odds": 2.1, "liquidity": 100.0, "commission_pct": 0.0}],
        [], 1.0, (f"demo-{name}" if is_demo else f"real-{name}"), is_demo=is_demo,
    )
    db.add_scenario_run(oid, "£100", 100.0, 50.0, 1.0, 2.0, "bankroll", [], {})
    return oid


def test_dashboard_can_hide_demo_rows(tmp_path: Path):
    db = DB(tmp_path / "demo.sqlite3")
    add_opportunity(db, "Real Fixture", False)
    add_opportunity(db, "Demo Fixture", True)
    hidden = db.dashboard(include_demo=False)
    shown = db.dashboard(include_demo=True)
    assert hidden["opportunities"] == 1
    assert hidden["scenario_runs"] == 1
    assert hidden["demo_count"] == 1
    assert all(not r["is_demo"] for r in hidden["recent"])
    assert shown["opportunities"] == 2
    assert shown["scenario_runs"] == 2


def test_clear_demo_data_preserves_real_rows(tmp_path: Path):
    db = DB(tmp_path / "demo.sqlite3")
    real_id = add_opportunity(db, "Real Fixture", False)
    add_opportunity(db, "Demo Fixture", True)
    assert db.clear_demo_data() == 1
    rows = db.opportunity_rows(include_demo=True)
    assert [r["id"] for r in rows] == [real_id]
    assert db.dashboard(include_demo=True)["demo_count"] == 0


def test_migration_tags_legacy_demo_signature(tmp_path: Path):
    import sqlite3
    path = tmp_path / "legacy.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE opportunities (
      id INTEGER PRIMARY KEY AUTOINCREMENT, detected_at TEXT NOT NULL, event_key TEXT NOT NULL,
      event_name TEXT, event_start TEXT, market_name TEXT NOT NULL, edge_pct REAL NOT NULL,
      expected_roi_pct REAL NOT NULL, legs_json TEXT NOT NULL, source_markets_json TEXT,
      match_score REAL DEFAULT 0, signature TEXT, status TEXT NOT NULL DEFAULT 'paper'
    );
    INSERT INTO opportunities(detected_at,event_key,event_name,market_name,edge_pct,expected_roi_pct,legs_json,signature)
    VALUES('2026-01-01T00:00:00Z','demo northbridge v riverside 1','Northbridge v Riverside','Match Odds',1,1,'[]','demo-legacy');
    """)
    con.commit(); con.close()
    db = DB(path)
    rows = db.opportunity_rows(include_demo=True)
    assert rows[0]["is_demo"] == 1
    assert db.dashboard(include_demo=False)["opportunities"] == 0
