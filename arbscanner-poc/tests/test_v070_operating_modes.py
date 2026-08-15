from pathlib import Path

from arbscanner.api import API


def test_old_paper_mode_migrates_to_monitor(tmp_path: Path):
    api = API(tmp_path / "modes.sqlite3")
    api.db.set_setting("mode", "paper")
    api2 = API(tmp_path / "modes.sqlite3")
    state = api2.get_state()
    assert state["version"] == "0.9.36"
    assert state["settings"]["mode"] == "sim"
    assert state["settings"]["operating_modes"]["sim"]["available"] is True
    assert state["settings"]["operating_modes"]["live"]["available"] is False


def test_watch_and_monitor_timing_alias_to_monitor_and_live_is_locked(tmp_path: Path):
    api = API(tmp_path / "mode-switch.sqlite3")
    assert api.get_state()["settings"]["mode"] == "sim"
    changed = api.set_operating_mode({"mode": "monitor_timing"})
    assert changed["ok"] is True
    assert changed["state"]["settings"]["mode"] == "sim"
    locked = api.set_operating_mode({"mode": "live"})
    assert locked["ok"] is False
    assert "locked" in locked["message"].lower()
    assert locked["state"]["settings"]["mode"] == "sim"
    assert api.db.get_setting("mode") == "sim"


def test_scanner_screen_exposes_always_on_watch_optional_monitor_timing_and_locked_live():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert "scanner always runs in WATCH" in html
    assert 'id="automationStateTitle"' in html
    assert 'id="monitor_timingActionBtn"' in html
    assert "ACTIVATE MONITOR_TIMING" in html
    assert "STOP MONITOR_TIMING" in html
    assert "ACTIVATE LIVE BETTING" in html
    assert '<button class="secondary modeaction" disabled>ACTIVATE LIVE BETTING</button>' in html
    assert "toggleMonitorTiming" in html
    assert ">Jobs</span>" not in html
