from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).parents[1]
HTML = ROOT.joinpath("frontend", "index.html").read_text()


def test_v0827_version_and_live_safety_boundary(tmp_path):
    assert tuple(int(x) for x in __version__.split(".")) >= (0, 8, 27)
    api = API(tmp_path / "boundary.sqlite3")
    racing = api.racing_overview({})
    assert racing["monitor_execution_allowed"] is True
    assert racing["live_execution_allowed"] is False
    assert racing["research_only"] is False
    assert "MONITOR only." in HTML
    assert "LIVE order placement remains hard-locked" in HTML


def test_racing_ui_separates_theoretical_from_deployable_and_streams():
    assert "Theoretical best" in HTML
    assert "Deployable selected" in HTML
    assert "Liquidity prevents the theoretical best price from being deployable" in HTML
    assert 'id="dashRacingEquity"' in HTML
    assert 'id="dashRacingProfit"' in HTML
    assert 'value="racing">Racing' in HTML
    assert "modeled_racing_monitor" in HTML
    assert "monitorStreamLabel" in HTML


def test_racing_wallets_are_isolated_from_sports_by_default(tmp_path):
    api = API(tmp_path / "wallets.sqlite3")
    overview = api.dashboard_overview({})
    streams = overview["stream_summary"]
    assert set(streams) == {"pre_match", "in_play", "racing"}
    assert streams["racing"]["equity"] == 500.0
    assert streams["pre_match"]["equity"] == 500.0
    assert streams["in_play"]["equity"] == 500.0
    assert overview["racing_working_bankroll"] == 500.0
    assert overview["sports_working_bankroll"] == 1000.0
