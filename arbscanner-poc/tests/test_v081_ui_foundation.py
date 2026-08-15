from pathlib import Path

from arbscanner.api import API


def _html():
    return Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()


def test_dashboard_has_single_scope_and_automatic_layout():
    html = _html()
    assert 'data-dashboard-scope="all"' in html
    assert 'data-dashboard-scope-button="all"' in html
    assert 'data-dashboard-scope-button="sports"' in html
    assert 'data-dashboard-scope-button="racing"' in html
    assert 'data-dashboard-display-button="fit"' not in html
    assert 'data-dashboard-display-button="full"' not in html
    assert 'Dashboard layout is fully automatic' in html
    assert "autobuys.dashboard.scope" in html


def test_dashboard_promotes_profit_and_keeps_racing_finances_separate():
    html = _html()
    assert 'id="dashTotalProfit"' in html
    assert 'id="dashTodayCaptured"' in html
    assert 'id="dashBankroll"' in html
    assert 'id="dashAvailable"' in html
    assert 'id="dashRaceMatched"' in html
    assert 'id="dashRaceQualified"' in html
    assert 'id="dashRaceNextOff"' in html
    assert 'id="dashRaceBestRoi"' in html
    assert 'MONITOR execution · LIVE locked' in html
    assert 'id="dashRacingEquity"' in html
    assert 'id="dashRacingProfit"' in html


def test_monitor_copy_describes_live_pipeline_not_just_execution_history():
    html = _html()
    assert "Live feeds, discovery, market matching and processing through opportunities, qualification and execution." in html
    assert "openAnalytics('execution')\">Execution history" in html


def test_dashboard_total_profit_is_relative_to_current_starting_balances(tmp_path):
    api = API(tmp_path / "profit.sqlite3")
    overview = api.dashboard_overview({})
    assert overview["ok"] is True
    assert overview["starting_bankroll"] == 1500.0
    assert overview["working_bankroll"] == 1500.0
    assert overview["total_profit"] == 0.0


def test_racing_live_execution_boundary_remains_locked(tmp_path):
    api = API(tmp_path / "racing-lock.sqlite3")
    racing = api.racing_overview({})
    assert racing["research_only"] is False
    assert racing["monitor_execution_allowed"] is True
    assert racing["live_execution_allowed"] is False
