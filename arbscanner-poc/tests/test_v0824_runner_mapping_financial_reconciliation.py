from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import arbscanner.api as api_module
from arbscanner.api import API, _racing_book_analysis_from_sources
from arbscanner.db import DB

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _source(exchange: str, rows: list[tuple[int | None, str, float]], *, raw_sides=False):
    out = []
    for idx, (trap, name, odds) in enumerate(rows, start=1):
        raw = []
        if raw_sides:
            raw = [
                {"side": "back", "odds": odds, "available_amount": 100.0},
                {"side": "lay", "odds": odds + 0.2, "available_amount": 90.0},
            ]
        out.append({
            "selection_id": f"{exchange}-{idx}",
            "selection": name,
            "trap_number": trap,
            "canonical_selection_key": (f"trap:{trap}|{name.lower().replace(' ', '-')}" if trap is not None else f"name:{name.lower().replace(' ', '-')}") ,
            "odds": odds,
            "liquidity": 100.0,
            "commission_pct": 2.0,
            "commission_source": "test",
            "interpreted_source_side": "back" if exchange == "Matchbook" else "availableToBack",
            "raw_prices": raw,
        })
    return {
        "exchange": exchange, "event_id": f"{exchange}-e", "market_id": f"{exchange}-m",
        "status": "OPEN", "in_play": False, "runner_prices": out,
    }


def test_racing_price_reconstruction_uses_one_canonical_outcome_per_runner():
    dogs = ["Baileys Bullet", "Alans Amigo", "Crystal Nancy", "Innfield Melody", "Hawkfield Grant"]
    bf = _source("Betfair delayed", [(i, name, odds) for i, (name, odds) in enumerate(zip(dogs, [3.0, 4.0, 5.0, 6.0, 8.0]), start=1)])
    # Matchbook deliberately has no trap metadata, matching the real screenshot.
    mb = _source("Matchbook", [(None, name.lower(), odds) for name, odds in zip(dogs, [3.2, 3.9, 5.2, 5.8, 8.2])], raw_sides=True)
    result = _racing_book_analysis_from_sources([bf, mb], minimum_liquidity=2.0)
    assert result["valid"] is True
    assert result["runner_mapping_valid"] is True
    assert result["expected_outcomes"] == 5
    assert result["economic_outcomes"] == 5
    assert len(result["runner_prices"]) == 5
    assert len(result["selected_legs"]) == 5
    assert {row["display"].lower() for row in result["runner_prices"]} == {x.lower() for x in dogs}
    # Every canonical outcome must contain both exchange prices, not separate trap/name outcomes.
    assert all("Betfair delayed" in row["prices"] and "Matchbook" in row["prices"] for row in result["runner_prices"])
    assert result["selected_cross_exchange_book_pct"] < 200.0


def test_racing_price_reconstruction_fails_closed_on_non_one_to_one_field():
    bf = _source("Betfair delayed", [(1, "Dog One", 2.5), (2, "Dog Two", 3.5), (3, "Dog Three", 4.5)])
    mb = _source("Matchbook", [(None, "Dog One", 2.6), (None, "Wrong Dog", 3.6), (None, "Dog Three", 4.6)])
    result = _racing_book_analysis_from_sources([bf, mb], minimum_liquidity=2.0)
    assert result["valid"] is False
    assert result["mapping_error"] is True
    assert "RUNNER MAPPING ERROR" in result["reason"]


def _insert_settled(db: DB, signature: str, pnl: float, deployed: float, settled_at: str):
    oid = db.add_opportunity(
        event_key=signature, event_name=signature, event_start=settled_at, market_name="Match Winner",
        edge_pct=1.0, expected_roi_pct=1.0, legs=[], source_markets=[], match_score=1.0,
        signature=signature, strategy="two-way", sport="Football",
    )
    with db.lock:
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,'pre_match','Winner',?,?)""",
            (oid, signature, "Match Winner", settled_at, settled_at, deployed, 1.0, "{}", "{}", "{}", pnl, "{}"),
        )
        db.conn.commit()


def test_settled_summary_quantizes_each_position_before_aggregation(tmp_path):
    db = DB(tmp_path / "precision.sqlite3")
    _insert_settled(db, "a", 0.12344, 10.00004, "2026-08-11T10:00:00+00:00")
    _insert_settled(db, "b", 0.23444, 20.00004, "2026-08-11T11:00:00+00:00")
    rows = db.settled_monitor_positions("2026-08-11T00:00:00+00:00", "2026-08-12T00:00:00+00:00")
    visible_pnl = round(sum(round(float(x["realized_pnl"]), 4) for x in rows), 4)
    visible_deployed = round(sum(round(float(x["deployed"]), 4) for x in rows), 4)
    summary = db.settled_monitor_summary("2026-08-11T00:00:00+00:00", "2026-08-12T00:00:00+00:00")
    assert summary["pnl"] == visible_pnl == 0.3578
    assert summary["deployed"] == visible_deployed == 30.0


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 8, 11, 15, 50, 0, tzinfo=timezone.utc)
        return fixed if tz is None else fixed.astimezone(tz)


def test_dashboard_financial_snapshot_freezes_one_as_of_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "datetime", _FixedDateTime)
    api = API(tmp_path / "snapshot.sqlite3")
    _insert_settled(api.db, "today-a", 1.23444, 10.0, "2026-08-11T09:00:00+00:00")
    _insert_settled(api.db, "today-b", 2.34544, 20.0, "2026-08-11T14:00:00+00:00")
    snap = api.financial_reconciliation_snapshot({"timezone_name": "Europe/London", "timezone_offset_minutes": -60})
    today = snap["today"]
    direct = api.settled_positions({
        "from_utc": today["from_utc"], "to_utc": today["to_utc"], "phase": "all", "sport": "all",
        "include_rows": False,
    })
    assert today["summary"]["pnl"] == direct["summary"]["pnl"] == 3.5798
    assert today["summary"]["settled"] == direct["summary"]["settled"] == 2
    assert snap["today"]["to_utc"] == snap["seven_day"]["to_utc"] == snap["all"]["to_utc"]


def test_frontend_dashboard_uses_shared_financial_snapshot():
    assert "financial_reconciliation_snapshot" in (ROOT / "arbscanner" / "api.py").read_text()
    assert "loadDashboardPerformance(frozenToday=null)" in HTML
    assert "let financial=r.financial||{}" in HTML
    assert "loadDashboardPerformance(today)" in HTML
