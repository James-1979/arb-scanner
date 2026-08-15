from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
SCANNER = (ROOT / "arbscanner" / "scanner.py").read_text()
ENGINES = (ROOT / "arbscanner" / "strategy_engines.py").read_text()


def test_0937_selected_mode_health_ignores_opposite_mode_disablement():
    control = {"sim_feed_enabled": True, "sim_account_enabled": True, "live_feed_enabled": False, "live_account_enabled": False}
    sim = API._provider_selected_mode_state("sim", control, "connected", "ready", "disabled")
    live = API._provider_selected_mode_state("live", control, "connected", "ready", "disabled")
    assert sim["state"] == "ready"
    assert sim["expected"] is True
    assert live["state"] == "disabled"
    assert live["expected"] is False


def test_0937_selected_mode_health_distinguishes_disabled_degraded_and_error():
    enabled = {"sim_feed_enabled": True, "sim_account_enabled": True, "live_feed_enabled": False, "live_account_enabled": False}
    disabled = {"sim_feed_enabled": False, "sim_account_enabled": False, "live_feed_enabled": False, "live_account_enabled": False}
    assert API._provider_selected_mode_state("sim", disabled, "disabled", "disabled", "disabled")["state"] == "disabled"
    assert API._provider_selected_mode_state("sim", enabled, "stale", "ready", "disabled")["state"] == "degraded"
    assert API._provider_selected_mode_state("sim", enabled, "error", "ready", "disabled")["state"] == "error"


def test_0937_dashboard_uses_expected_provider_denominators_and_selected_mode_state():
    assert "selected_mode_summary" in (ROOT / "arbscanner" / "api.py").read_text()
    assert "feeds_expected" in HTML and "feeds_ready" in HTML
    assert "feed.mode_states?.[mode]" in HTML
    assert "LIVE feed OFF" not in HTML[HTML.index('id="v0937-closure-script"'):]


def test_0937_performance_has_fixed_inspector_and_continuous_animation_without_financial_interpolation():
    assert 'id="performanceFinancialInspector0937"' in HTML
    assert 'id="performanceTimelineTooltip0931"' not in HTML
    script = HTML[HTML.index('id="v0937-closure-script"'):]
    assert "requestAnimationFrame(performanceFrameStep0937)" in script
    assert "performanceAuthoritativeIndex0937" in script
    assert "if(b.ts[i]<=cursor)idx=i" in script
    assert "performanceInspector0937(idx,cursorMs)" in script
    assert "data-performance-bucket0937" in script


def test_0937_accounts_use_current_state_exposure_terminology_without_arithmetic_rewrite():
    accounts = HTML[HTML.index('<section id="accounts"'):HTML.index('</section>', HTML.index('<section id="accounts"'))]
    assert "Current Exposure" in accounts
    assert "Current Utilisation" in accounts
    assert "Total Exposure" not in accounts
    assert "Total Utilisation" not in accounts
    assert "current exposure / total capital" in accounts
    assert "Current Exposure</span>" in HTML


def test_0937_sports_config_is_guardrails_only_with_one_save_action():
    page = HTML[HTML.index('<section id="sports-config"'):HTML.index('</section>', HTML.index('<section id="sports-config"'))]
    assert "Portfolio-wide Sports operating policy and hard guardrails" in page
    assert page.count("Save Changes") == 1
    for old in ("preMatchBfStart", "preMatchMbStart", "inPlayBfStart", "inPlayMbStart", "Apply & reset", "Save Sports config"):
        assert old not in page
    script = HTML[HTML.index('id="v0937-closure-script"'):]
    assert "sportsPreEnabled0937" in script and "sportsInplayEnabled0937" in script
    assert "SIM Execution Model" in script
    assert "smarkets" in script and "Awaiting integration" in script
    assert "callReadBounded('venue_controls'" in script
    assert "sportsSimProviders0937=r.rows.map" in script
    assert "saveSportsConfig0937" in script
    assert "legacySportsCoverage.hidden=true" in HTML


def test_0937_stream_disablement_and_guardrail_precedence_are_enforced_in_runtime():
    assert 'cfg.get("pre_match_monitor_enabled", True)' in SCANNER
    assert 'cfg.get("inplay_monitor_enabled", True)' in SCANNER
    assert "Engines may request stricter minima/lower maxima" in ENGINES
    assert 'config[key] = max(float(config.get(key, 0.0) or 0.0), float(value or 0.0))' in ENGINES
    assert 'config[key] = float(value or 0.0) if current is None else min(' in ENGINES
    assert "Capped by Sports Config" in HTML


def test_0937_market_analysis_header_and_refresh_contract():
    assert 'id="marketAnalysisRefresh0937"' in HTML
    assert "◌ Refreshing…" in HTML and "✓ Updated" in HTML and "Refresh failed" in HTML
    assert "marketAnalysisRequestEpoch0937" in HTML
    assert "requestEpoch!==marketAnalysisRequestEpoch0937" in HTML
    css = HTML[HTML.index('id="v0937-closure-css"'):HTML.index('</style>', HTML.index('id="v0937-closure-css"'))]
    assert "justify-content:flex-end" in css


def test_0937_replay_header_labels_time_and_prioritises_running_pnl():
    assert "RUNNING P&amp;L" in HTML
    assert "REPLAY TIME" in HTML
    assert "timeline-running-pnl0937>strong{font-size:17px" in HTML
    assert "timeline-replay-time0937>strong{font-size:11px" in HTML


def test_0937_lifecycle_contract_is_not_reopened():
    for token in ("Processed", "Opportunities", "Qualified", "Executed", "Settled", "Realised P&amp;L"):
        assert token in HTML
    assert 'id="monitorMode0917"' not in HTML
    assert 'id="positionResultsMode0917"' not in HTML
    assert "Legacy / Unverified" in HTML or "LEGACY / UNVERIFIED" in HTML
