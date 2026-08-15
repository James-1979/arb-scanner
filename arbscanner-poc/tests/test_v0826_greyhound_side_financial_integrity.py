import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.adapters import MatchbookAdapter
from arbscanner.api import API, _racing_book_analysis_from_sources
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, MarketMatch, Quote
from arbscanner.scanner import Scanner

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0826_version_and_visible_financial_basis():
    assert __version__ == "0.9.36"
    assert "PoC 0.9.36" in HTML
    assert "Wallet committed capital" in HTML
    assert "Locked return on balanced capital" in HTML
    assert 'id="activeBetsLockedBasis"' in HTML


def test_matchbook_win_lose_aliases_are_canonicalised_without_losing_raw_side():
    rows = MatchbookAdapter._raw_price_rows([
        {"side": "win", "odds": 2.2, "available-amount": 40},
        {"side": "lose", "odds": 2.3, "available-amount": 35},
    ])
    assert [x["side"] for x in rows] == ["back", "lay"]
    assert [x["source_side"] for x in rows] == ["win", "lose"]
    assert MatchbookAdapter._best_back([{"side": "win", "odds": 2.2, "available-amount": 40}]) == (2.2, 40.0)


def test_stored_win_lose_evidence_rebuilds_back_and_lay_books():
    bf = {
        "exchange": "Betfair delayed",
        "runner_prices": [
            {"selection": "Dog A", "trap_number": 1, "odds": 2.5, "liquidity": 100, "raw_prices": []},
            {"selection": "Dog B", "trap_number": 2, "odds": 3.5, "liquidity": 100, "raw_prices": []},
            {"selection": "Dog C", "trap_number": 3, "odds": 4.5, "liquidity": 100, "raw_prices": []},
        ],
    }
    mb = {
        "exchange": "Matchbook",
        "runner_prices": [
            {"selection": "dog a", "odds": 2.6, "liquidity": 90, "raw_prices": [{"side": "win", "odds": 2.6, "available_amount": 90}, {"side": "lose", "odds": 2.7, "available_amount": 80}]},
            {"selection": "dog b", "odds": 3.6, "liquidity": 90, "raw_prices": [{"side": "win", "odds": 3.6, "available_amount": 90}, {"side": "lose", "odds": 3.7, "available_amount": 80}]},
            {"selection": "dog c", "odds": 4.6, "liquidity": 90, "raw_prices": [{"side": "win", "odds": 4.6, "available_amount": 90}, {"side": "lose", "odds": 4.7, "available_amount": 80}]},
        ],
    }
    result = _racing_book_analysis_from_sources([bf, mb], minimum_liquidity=2.0)
    assert result["valid"] is True
    assert result["matchbook_side_audit"]["complete"] == {"back": True, "lay": True}
    assert all(set(row["matchbook_raw_sides"]) == {"back", "lay"} for row in result["runner_prices"])


def test_matched_racing_missing_side_uses_diagnostic_probe_only(tmp_path):
    adapter = MatchbookAdapter(session_token="test", enabled_sports=["Greyhounds"])

    async def fake_probe(requests):
        assert requests == [{"event_id": "e1", "market_id": "m1", "missing_sides": ["lay"]}]
        return [{
            "ok": True, "event_id": "e1", "market_id": "m1", "side": "lay",
            "observed_at": "2026-08-11T18:00:00+00:00", "runners": {
                "r1": [{"side": "lay", "source_side": "lose", "requested_side": "lay", "source": "side_probe", "observed_at": "2026-08-11T18:00:00+00:00", "odds": 2.3, "available_amount": 30.0}],
            },
        }]

    adapter.probe_racing_price_sides = fake_probe
    q = Quote(
        exchange="Matchbook", event_id="e1", market_id="m1", event_name="Track", market_name="Win",
        selection_id="r1", selection="Dog A", odds=2.2, liquidity=40, captured_at="2026-08-11T17:59:00+00:00",
        market_type="win", strategy="multi_runner_win", sport="Greyhounds", section="racing",
        raw={"prices": [{"side": "win", "odds": 2.2, "available-amount": 40}]},
    )
    market = ExchangeMarket("Matchbook", "e1", "m1", "Track", "Win", None, [q], market_type="win", strategy="multi_runner_win", sport="Greyhounds", section="racing")
    mm = MarketMatch("track", "win", "Track", "Win", None, [market], 1.0, market_type="win", strategy="multi_runner_win", sport="Greyhounds", section="racing", runner_count=1)
    scanner = Scanner(DB(tmp_path / "probe.sqlite3"), None)
    summary = asyncio.run(scanner._augment_racing_matchbook_side_evidence([mm], [adapter]))
    assert summary["completed"] == 1
    raw = scanner._raw_matchbook_prices(q.raw)
    assert {x["side"] for x in raw} == {"back", "lay"}
    lay = next(x for x in raw if x["side"] == "lay")
    assert lay["source_side"] == "lose"
    assert lay["source"] == "side_probe"
    assert q.odds == 2.2  # probe evidence must not alter executable quote


def _insert_settled(db: DB, signature: str, pnl: float, expected: float, settled_at: str):
    oid = db.add_opportunity(signature, signature, settled_at, "Match Odds", 1.0, 1.0, [], [], 1.0, signature, strategy="1x2", sport="Football")
    with db.lock:
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?,'pre_match','Home',?,?)""",
            (oid, signature, "Match Odds", settled_at, settled_at, 100.0, expected, "{}", "{}", "{}", pnl, "{}"),
        )
        db.conn.commit()
    return oid


def test_dashboard_best_win_uses_settled_realized_pnl_and_reconciles_ledger(tmp_path):
    api = API(tmp_path / "best-win.sqlite3")
    now = datetime.now(timezone.utc)
    oid = _insert_settled(api.db, "best-realized", 12.3456, 999.0, (now - timedelta(minutes=5)).isoformat())
    _insert_settled(api.db, "old-large-win", 50.0, 50.0, (now - timedelta(hours=25)).isoformat())
    dash = api.dashboard_results_24h({})
    assert dash["settled_only"] is True
    assert dash["pnl_basis"] == "realized_pnl"
    assert dash["best_win"]["opportunity_id"] == oid
    assert dash["best_win"]["pnl"] == 12.3456
    ledger = api.settled_positions({"from_utc": (now - timedelta(hours=24)).isoformat(), "to_utc": (now + timedelta(seconds=2)).isoformat(), "include_rows": True})
    row = next(x for x in ledger["rows"] if int(x["opportunity_id"]) == oid)
    assert round(float(row["realized_pnl"]), 4) == dash["best_win"]["pnl"]


def test_dashboard_exposes_locked_return_denominator(tmp_path):
    api = API(tmp_path / "locked-basis.sqlite3")
    oid = api.db.add_opportunity("a v b", "A v B", None, "Match Winner", 2.0, 2.0, [], [], 1.0, "locked-basis", strategy="two-way", sport="Tennis")
    sim = {"stakes": [{"exchange": "Betfair delayed", "selection": "A", "odds": 2.0, "stake": 60}, {"exchange": "Matchbook", "selection": "B", "odds": 2.2, "stake": 40}], "after_hedge": {"worst_case_pnl": 8.0, "best_case_pnl": 8.5, "balanced": True}}
    opened, reason = api.db.open_monitor_position(opportunity_id=oid, execution_run_id=None, event_key="a v b", market_name="Match Winner", deployed=100.0, expected_profit=8.2, stakes_by_exchange={"betfair": 60, "matchbook": 40}, normal_stakes_by_exchange={"betfair": 60, "matchbook": 40}, outcome_exchange_pnls={"A": {"betfair": 8, "matchbook": 0}, "B": {"betfair": 0, "matchbook": 8.5}}, simulation=sim, hedge_reserve_pct=20, stream="pre_match")
    assert opened, reason
    result = api.dashboard_overview({})
    assert result["locked_open_deployed"] == 100.0
    assert result["locked_position_count"] == 1
    assert result["locked_return_basis"] == "balanced_position_deployed_capital"
    assert result["locked_open_return_pct"] == 8.0
