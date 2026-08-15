from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import arbscanner.api as api_module
import arbscanner.db as db_module
from arbscanner import __version__
from arbscanner.adapters import MatchbookAdapter
from arbscanner.api import API, _racing_book_analysis_from_sources
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, MarketMatch, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _insert_position(db: DB, *, opened_at: str, settled_at: str | None, pnl: float = 0.0,
                     deployed: float = 100.0, event: str = "Alpha v Beta", sport: str = "Football") -> int:
    oid = db.add_opportunity(
        event_key=event.lower().replace(" ", "-"), event_name=event,
        event_start=opened_at, market_name="Match Winner", edge_pct=2.0,
        expected_roi_pct=1.0, legs=[], source_markets=[], match_score=0.99,
        signature=f"sig-{event}-{opened_at}-{settled_at}", strategy="two-way", sport=sport,
    )
    status = "SETTLED" if settled_at else "OPEN"
    with db.lock:
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                oid, event.lower().replace(" ", "-"), "Match Winner", opened_at, settled_at, status,
                deployed, 1.0, "{}", "{}", "{}", "pre_match",
                "Alpha" if settled_at else None, pnl if settled_at else None, "{}",
            ),
        )
        db.conn.commit()
    return oid


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 8, 11, 15, 10, 0, tzinfo=timezone.utc)
        return fixed if tz is None else fixed.astimezone(tz)


def test_release_metadata_and_frontend_expose_data_integrity_audit():
    assert __version__ == "0.9.36"
    for token in (
        "PoC 0.9.36",
        "MB raw BACK book",
        "MB raw LAY book",
        "Runner price-side audit",
        "Diagnostic only.",
        "settled_positions",
        "viewerTimePayload",
    ):
        assert token in HTML


def test_local_today_uses_iana_timezone_and_bst_boundary(monkeypatch):
    monkeypatch.setattr(api_module, "datetime", _FixedDateTime)
    bounds = API._local_today_bounds({"timezone_name": "Europe/London", "timezone_offset_minutes": -60})
    assert bounds["local_date"] == "2026-08-11"
    # Midnight BST is 23:00 UTC on the previous calendar date.
    assert bounds["from_utc"].isoformat() == "2026-08-10T23:00:00+00:00"
    assert bounds["timezone_name"] == "Europe/London"


def test_today_settled_ledger_uses_settlement_time_not_open_time(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "datetime", _FixedDateTime)
    monkeypatch.setattr(db_module, "datetime", _FixedDateTime)
    api = API(tmp_path / "settlement-time.sqlite3")
    # Opened before local midnight, settled at 00:30 BST: must count today.
    wanted = _insert_position(
        api.db, opened_at="2026-08-10T21:30:00+00:00", settled_at="2026-08-10T23:30:00+00:00", pnl=5.25,
        event="Yesterday Open Today Settle",
    )
    # Settled before local midnight: must not count today.
    _insert_position(
        api.db, opened_at="2026-08-10T18:00:00+00:00", settled_at="2026-08-10T22:59:59+00:00", pnl=7.0,
        event="Yesterday Settle",
    )
    # Opened today but not settled: must not count settled Today P&L.
    _insert_position(
        api.db, opened_at="2026-08-11T09:00:00+00:00", settled_at=None, event="Today Open Still Open",
    )

    result = api.settled_positions({"period": "today", "timezone_name": "Europe/London", "timezone_offset_minutes": -60})
    assert result["ok"] is True
    assert result["from_utc"] == "2026-08-10T23:00:00+00:00"
    assert result["summary"] == {
        "settled": 1, "wins": 1, "losses": 0, "breakeven": 0,
        "pnl": 5.25, "deployed": 100.0, "returned": 105.25, "execution_leakage": 0.0,
        "best_pnl": 5.25, "worst_pnl": 5.25,
    }
    assert [row["opportunity_id"] for row in result["rows"]] == [wanted]

    summary = api.db.settled_monitor_summary(
        from_utc="2026-08-10T23:00:00+00:00", to_utc="2026-08-11T15:10:01+00:00"
    )
    assert summary["pnl"] == result["summary"]["pnl"]
    assert summary["settled"] == result["summary"]["settled"]
    assert summary["wins"] == result["summary"]["wins"]

    fast = api.settled_positions({
        "from_utc": "2026-08-10T23:00:00+00:00", "to_utc": "2026-08-11T15:10:01+00:00",
        "phase": "all", "sport": "all", "include_rows": False,
    })
    assert fast["rows"] == []
    assert fast["summary"]["pnl"] == result["summary"]["pnl"]
    assert fast["summary"]["settled"] == result["summary"]["settled"]

    dashboard = api.dashboard_trends({"days": 7, "timezone_name": "Europe/London", "timezone_offset_minutes": -60})
    assert dashboard["rows"][-1]["date"] == "2026-08-11"
    assert dashboard["rows"][-1]["sports"]["pnl"] == result["summary"]["pnl"]
    assert dashboard["rows"][-1]["sports"]["settled"] == result["summary"]["settled"]

    performance = api.performance_analytics({
        "period": "7d", "scope": "sports", "stream": "all", "basis": "actual",
        "timezone_name": "Europe/London", "timezone_offset_minutes": -60,
    })
    today_perf = next(x for x in performance["rows"] if x["date"] == "2026-08-11")
    assert today_perf["profit"] == result["summary"]["pnl"]
    assert today_perf["settled"] == result["summary"]["settled"]

    pipeline = api.db.scan_pipeline_between("2026-08-10T23:00:00+00:00", "2026-08-11T15:10:01+00:00")
    assert pipeline["streams"]["pre_match"]["settled"] == result["summary"]["settled"]
    assert pipeline["streams"]["pre_match"]["realized_pnl"] == result["summary"]["pnl"]
    assert pipeline["streams"]["pre_match"]["financial_time_basis"] == "settled_at"


def test_frontend_today_views_use_calendar_midnight_and_canonical_settlements():
    assert "callReadBounded('settled_positions'" in HTML
    assert "from=new Date(t.getFullYear(),t.getMonth(),t.getDate()-1)" in HTML
    assert "from=new Date(t.getTime()-86400000)" not in HTML
    dashboard_perf = HTML.split("async function loadDashboardPerformance(frozenToday=null){", 1)[1].split("async function loadDashboardPipeline(){", 1)[0]
    assert "callReadBounded('settled_positions'" in dashboard_perf
    assert "activity_analytics" not in dashboard_perf
    assert "x.settled_at" in dashboard_perf


def test_market_financial_columns_use_settlement_period(tmp_path):
    db = DB(tmp_path / "market-settlement.sqlite3")
    oid = _insert_position(
        db, opened_at="2026-08-10T20:00:00+00:00", settled_at="2026-08-11T08:00:00+00:00",
        pnl=4.0, deployed=80.0, event="Period Crossing",
    )
    exec_id = db.add_execution_run(
        oid, "monitor", "monitor", "MONITOR_SETTLED", started_at="2026-08-10T20:01:00+00:00",
        finished_at="2026-08-11T08:00:00+00:00", details={},
    )
    with db.lock:
        db.conn.execute("UPDATE monitor_positions SET execution_run_id=? WHERE opportunity_id=?", (exec_id, oid))
        scan = db.conn.execute(
            """INSERT INTO scan_runs(started_at,finished_at,markets_seen,matches_seen,status_json,scan_kind)
               VALUES(?,?,?,?,?,?)""",
            ("2026-08-11T07:00:00+00:00", "2026-08-11T07:00:01+00:00", 1, 1, "{}", "price"),
        )
        db.conn.execute(
            """INSERT INTO matched_markets(
                scan_id,observed_at,event_key,event_name,event_start,market_name,match_score,status,
                strategy,sport,section,in_play,net_roi_pct,legs_json,source_markets_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(scan.lastrowid), "2026-08-11T07:00:00+00:00", "period-crossing", "Period Crossing",
                "2026-08-11T12:00:00+00:00", "Match Winner", 0.99, "recommended", "two-way",
                "Football", "sports", 0, 1.2, "[]", "[]",
            ),
        )
        db.conn.commit()

    payload = db.market_analysis_between("2026-08-11T00:00:00+00:00", "2026-08-12T00:00:00+00:00")
    row = next(x for x in payload["rows"] if x["sport"] == "Football")
    assert row["attempts"] == 0  # execution started before the technical activity window
    assert row["executed"] == 0
    assert row["settled"] == 1
    assert row["pnl"] == 4.0
    assert row["deployed"] == 80.0  # financial capital is settlement-period capital
    assert row["returned"] == 84.0
    assert row["execution_started_deployed"] == 0.0


def _side_source(exchange: str, interpreted: list[float], raw_back=None, raw_lay=None):
    rows = []
    for trap, odds in enumerate(interpreted, start=1):
        raw = []
        if raw_back:
            raw.append({"side": "back", "odds": raw_back[trap - 1], "available_amount": 100.0})
        if raw_lay:
            raw.append({"side": "lay", "odds": raw_lay[trap - 1], "available_amount": 100.0})
        rows.append({
            "selection_id": f"{exchange}-{trap}", "selection": f"Dog {trap}", "trap_number": trap,
            "canonical_selection_key": f"trap:{trap}|dog-{trap}", "odds": odds, "liquidity": 100.0,
            "commission_pct": 2.0, "commission_source": "test",
            "interpreted_source_side": "back" if exchange == "Matchbook" else "availableToBack",
            "raw_prices": raw,
        })
    return {"exchange": exchange, "event_id": f"{exchange}-e", "market_id": f"{exchange}-m", "status": "OPEN", "in_play": False, "runner_prices": rows}


def test_racing_side_audit_keeps_both_matchbook_sides_without_flipping_interpretation():
    sources = [
        _side_source("Betfair delayed", [3.0, 3.2, 3.4]),
        _side_source("Matchbook", [1.4, 1.5, 1.6], raw_back=[1.4, 1.5, 1.6], raw_lay=[3.1, 3.3, 3.5]),
    ]
    result = _racing_book_analysis_from_sources(sources, minimum_liquidity=2.0)
    assert result["valid"] is True
    audit = result["matchbook_side_audit"]
    assert audit["current_interpretation"] == "back"
    assert audit["complete"] == {"back": True, "lay": True}
    assert audit["raw_books_pct"]["back"] > 200
    assert audit["raw_books_pct"]["lay"] < 100
    assert audit["best_combined_books_pct"]["lay"] < audit["best_combined_books_pct"]["back"]
    assert audit["suspicious_current_interpretation"] is True
    assert all(row["matchbook_raw_sides"].keys() == {"back", "lay"} for row in result["runner_prices"])

    # Diagnostic-only release: executable Matchbook interpretation remains exactly as before.
    assert MatchbookAdapter._best_back([
        {"side": "back", "odds": 1.5, "available-amount": 20},
        {"side": "lay", "odds": 3.5, "available-amount": 20},
    ]) == (1.5, 20.0)


def test_scanner_retains_raw_matchbook_sides_in_racing_source_snapshot(tmp_path):
    now = "2026-08-11T15:00:00+00:00"
    quote = Quote(
        exchange="Matchbook", event_id="mb-e", market_id="mb-m", event_name="Romford", market_name="Win",
        selection_id="1", selection="Dog One", odds=2.2, liquidity=40.0, captured_at=now,
        commission_pct=2.0, commission_source="test", sport="Greyhounds", market_type="win",
        strategy="multi_runner_win", in_play=False, market_status="OPEN", section="racing", trap_number=1,
        canonical_selection_key="trap:1|dog-one", runner_status="ACTIVE",
        raw={"raw_prices": [
            {"side": "back", "odds": 2.2, "available_amount": 40.0},
            {"side": "lay", "odds": 4.4, "available_amount": 30.0},
        ]},
    )
    market = ExchangeMarket(
        exchange="Matchbook", event_id="mb-e", market_id="mb-m", event_name="Romford", market_name="Win",
        start_time=now, quotes=[quote], market_type="win", strategy="multi_runner_win", sport="Greyhounds",
        in_play=False, section="racing", race_track="romford",
    )
    mm = MarketMatch(
        event_key="romford", market_key="win", display_event="Romford", display_market="Win", start_time=now,
        markets=[market], match_score=1.0, market_type="win", strategy="multi_runner_win", sport="Greyhounds",
        in_play=False, section="racing", race_track="romford", runner_count=1,
    )
    scanner = Scanner(DB(tmp_path / "raw-side.sqlite3"), SecretStore())
    source = scanner._source_markets(mm)[0]["runner_prices"][0]
    assert source["interpreted_source_side"] == "back"
    assert {x["side"] for x in source["raw_prices"]} == {"back", "lay"}


def test_matchbook_shared_adapter_audit_is_compact_and_sport_scoped():
    adapter = MatchbookAdapter(session_token="dummy")
    runners = [
        {"prices": [{"side": "back", "odds": 1.5, "available-amount": 10}, {"side": "lay", "odds": 3.2, "available-amount": 10}]},
        {"prices": [{"side": "back", "odds": 1.6, "available-amount": 10}, {"side": "lay", "odds": 3.3, "available-amount": 10}]},
        {"prices": [{"side": "back", "odds": 1.7, "available-amount": 10}, {"side": "lay", "odds": 3.4, "available-amount": 10}]},
    ]
    adapter._record_price_side_audit("Football", "A v B", "Match Odds", runners)
    adapter._finalize_price_side_audit()
    diag = adapter.last_price_side_audit
    assert diag["current_interpretation"] == "back"
    assert diag["by_sport"]["Football"]["both_complete"] == 1
    assert diag["by_sport"]["Football"]["avg_back_book_pct"] is not None
    assert diag["by_sport"]["Football"]["avg_lay_book_pct"] is not None
    assert len(diag["by_sport"]["Football"]["samples"]) == 1
