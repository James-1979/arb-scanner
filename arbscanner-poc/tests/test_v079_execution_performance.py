from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.models import Leg


def _opp(api: API):
    legs = [
        Leg("Betfair delayed", "Home", 2.2, 100, 2.0),
        Leg("Matchbook", "Away", 2.2, 100, 2.0),
    ]
    return api.db.add_opportunity(
        "evt", "Alpha v Beta", "2026-08-10T12:00:00+00:00", "Match Winner", 2.0, 2.0,
        [asdict(x) for x in legs], [], 0.99, "sig-v079", sport="Football",
    )


def test_execution_history_carries_settlement_result(tmp_path: Path):
    api = API(tmp_path / "exec-result.sqlite3")
    oid = _opp(api)
    api.db.add_execution_run(oid, "monitor", "timed_monitor", "MONITOR_OPEN", deployed=40, expected_profit=2, captured_profit=2)
    api.db.settle(oid, "Home")
    row = api.db.execution_history()[0]
    assert row["outcome"] == "Home"
    assert row["settled_at"]


def test_settlement_queue_only_tracks_opportunities_with_execution_interest(tmp_path: Path):
    api = API(tmp_path / "interest.sqlite3")
    ignored = _opp(api)
    interested = api.db.add_opportunity(
        "evt2", "Gamma v Delta", "2026-08-10T13:00:00+00:00", "Match Winner", 2.0, 2.0,
        [asdict(Leg("Betfair delayed", "Home", 2.1, 100, 2.0)), asdict(Leg("Matchbook", "Away", 2.1, 100, 2.0))],
        [], 0.99, "sig-v079-2", sport="Football",
    )
    api.db.add_execution_run(interested, "monitor", "timed_monitor", "MONITOR_MISSED", deployed=0, expected_profit=0, captured_profit=0)
    ids = {x["id"] for x in api.db.unresolved_opportunities()}
    assert interested in ids
    assert ignored not in ids


def test_frontend_integrates_results_into_executions_and_fixes_drawer():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    nav = html[html.index('<div class="nav" id="nav"'): html.index('</div>', html.index('<div class="nav" id="nav"')) + 6]
    assert '>Results</span>' not in nav
    assert '>Executions</span>' in nav
    assert 'id="executionsStatus"' in html
    assert '<th>Result</th>' in html
    assert 'id="performanceCard"' in html
    assert 'id="perfPnl"' in html
    assert 'function lifecycleSvg(rows)' in html
