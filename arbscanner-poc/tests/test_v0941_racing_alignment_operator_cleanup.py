from __future__ import annotations

from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
API_SRC = (ROOT / "arbscanner" / "api.py").read_text()
SCANNER_SRC = (ROOT / "arbscanner" / "scanner.py").read_text()



def test_0941_release_identity_and_installer_lock():
    from arbscanner import __version__
    assert __version__ == "0.9.41"
    assert '<title>ArbScanner PoC 0.9.41</title>' in HTML
    installer = (ROOT / "BUILD_AND_INSTALL.command").read_text()
    assert 'EXPECTED_VERSION="0.9.41"' in installer
    assert '## 0.9.41' in (ROOT / "RELEASE_NOTES.md").read_text()

def test_0941_performance_labels_use_non_rag_capital_language_and_replay_buttons_only():
    assert "Available to Deploy" in HTML
    assert "In Open Positions" in HTML
    assert "#performanceCapitalTimeline0931 .deployed-step0939{stroke:#7c3aed" in HTML
    assert "Open positions " in HTML
    assert '<input id="timelineReplaySpeed" type="hidden" value="1">' in HTML
    assert '<select id="timelineReplaySpeed"' not in HTML
    for label in ("0.5x", "1x", "2x", "5x", "10x"):
        assert label in HTML or "vals=[.5,1,2,5,10]" in HTML


def test_0941_smarkets_sim_pending_is_neutral_not_reporting_and_not_expected():
    assert "Supported provider slot" in HTML
    assert "NOT REPORTING" in HTML
    assert "NOT EXPECTED" in HTML
    assert "AWAITING API ACCESS" in HTML
    assert "reporting venue accounts" in HTML


def test_0941_sports_monitor_and_results_have_one_compact_filter_surface():
    assert "sportsMonitorSportChips0941" in HTML
    assert "sportsResultsSportChips0941" in HTML
    assert "sportsMonitorActiveSports0941" in HTML
    assert "sportsResultsActiveSports0941" in HTML
    assert "positionResultsPeriodButtons0941" in HTML
    for label in ("Today", "Yesterday", "7 Days", "30 Days"):
        assert label in HTML
    # Old secondary controls remain only as hidden backing state; the operator gets one surface.
    assert "secondary.hidden=true" in HTML
    assert "positionResultsPhaseWrap" in HTML and "hidden=racing" in HTML


def test_0941_sports_highlight_icon_registry_covers_supported_sports():
    for sport in (
        "Football", "Tennis", "Cricket", "Basketball", "Darts", "Snooker",
        "Ice Hockey", "Volleyball", "Rugby Union", "Rugby League",
        "American Football", "Baseball", "Handball", "Australian Rules", "Field Hockey",
    ):
        assert sport in HTML
    for icon in ("\\u26bd", "\\ud83c\\udfbe", "\\ud83c\\udfcf", "\\ud83c\\udfc0", "\\ud83c\\udfaf"):
        assert icon in HTML


def test_0941_racing_pages_follow_shared_operator_boundaries_without_sports_only_filters():
    for marker in (
        'id="racing" class="page racing-overview0941"',
        'id="racing-monitor" class="page racing-monitor0941"',
        'id="racing-engines" class="page racing-engines0941"',
        'id="racing-config" class="page racing-config0941"',
        "Total Racing Capital", "Race Highlights", "Current Racing Positions",
        "Racing Monitor", "Last Detected", "Racing Engines", "SIM Execution Model",
    ):
        assert marker in HTML
    racing_monitor = HTML.split('<section id="racing-monitor"', 1)[1].split('</section>', 1)[0]
    assert "Engine" in racing_monitor and "Status" in racing_monitor and "Venue" in racing_monitor and "Search" in racing_monitor
    assert "Stream" not in racing_monitor and "All sports" not in racing_monitor
    assert "Processed" in racing_monitor and "Opportunities" in racing_monitor and "Qualified" in racing_monitor and "Executed" in racing_monitor
    racing_config = HTML.split('<section id="racing-config"', 1)[1].split('</section>', 1)[0]
    for forbidden in ("starting balance", "Starting balance", "Apply & reset", "Current equity", "Betfair wallet", "Matchbook wallet"):
        assert forbidden not in racing_config
    assert racing_config.count("Save Changes") == 1
    assert "Minimum expected profit" in racing_config
    assert "Minimum expected return" in racing_config
    assert "Minimum opportunity quality" in racing_config
    assert "Maximum stake" in racing_config


def test_0941_racing_engine_view_is_full_width_results_authority_and_drawer_based():
    racing_engines = HTML.split('<section id="racing-engines"', 1)[1].split('</section>', 1)[0]
    assert "Active Engines" not in racing_engines  # rendered dynamically from canonical lifecycle
    assert "+ Add Engine" in racing_engines
    assert "racingEngineDrawer0941" in racing_engines
    assert "Enabled" not in racing_engines  # table columns rendered from canonical JS
    assert "Export Engine" in HTML
    assert "Settled P&amp;L" in HTML
    assert "from Results" in HTML
    assert "LIVE Execution</span><strong>CENTRALLY LOCKED" in HTML


def test_0941_racing_overview_is_mode_scoped_and_does_not_warn_about_removed_starting_balances():
    start = API_SRC.index("    def racing_overview")
    end = API_SRC.index("    def racing_monitor", start)
    block = API_SRC[start:end]
    assert 'canonical_mode_value(data.get("mode") or "sim")' in block
    assert '_live_portfolio_financial_state_async(cfg, scope="racing", venue="all")' in block
    assert '_sim_portfolio_financial_state(cfg, scope="racing", venue="all")' in block
    assert '_operational_status("live")' in block
    assert '_operational_status("sim")' in block
    assert "no usable SIM venue capital" not in block
    assert '"minimum_quality_band"' in block


def test_0941_multi_sport_lifecycle_aggregation_does_not_double_first_row(tmp_path):
    api = API(tmp_path / "lifecycle.sqlite3")
    calls = []

    def fake_rows(**kwargs):
        calls.append(kwargs.get("sport"))
        sport = kwargs.get("sport")
        if sport == "Football":
            return [{"engine_instance_id": "E1", "processed": 2, "opportunities": 1, "qualified": 1,
                     "executed": 1, "settled": 1, "realised_pnl": 3.0, "errors": 0,
                     "last_activity": "2026-08-14T10:00:00+00:00"}]
        return [{"engine_instance_id": "E1", "processed": 5, "opportunities": 2, "qualified": 1,
                 "executed": 0, "settled": 0, "realised_pnl": 0.0, "errors": 1,
                 "last_activity": "2026-08-14T11:00:00+00:00"}]

    api.db.engine_lifecycle_rows = fake_rows
    out = api.engine_lifecycle({"section": "sports", "mode": "sim", "sports": ["Football", "Tennis"]})
    assert out["ok"] is True
    assert calls == ["Football", "Tennis"]
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["processed"] == 7
    assert row["opportunities"] == 3
    assert row["qualified"] == 2
    assert row["executed"] == 1
    assert row["settled"] == 1
    assert row["realised_pnl"] == 3.0
    assert row["errors"] == 1
    assert row["last_activity"] == "2026-08-14T11:00:00+00:00"


def test_0941_racing_guardrail_slippage_applies_engine_cap_after_platform_value():
    assert 'cfg.get("racing_execution_max_slippage_pct"' in SCANNER_SRC
    # Engine maximum is re-applied after the Racing platform value, so the engine can be stricter but never looser.
    racing_pos = SCANNER_SRC.index('cfg.get("racing_execution_max_slippage_pct"')
    later = SCANNER_SRC[racing_pos:racing_pos + 900]
    assert 'max_slippage_pct = min(max_slippage_pct' in later
