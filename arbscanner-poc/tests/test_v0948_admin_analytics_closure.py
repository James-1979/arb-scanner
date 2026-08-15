from __future__ import annotations

from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
API_SOURCE = (ROOT / "arbscanner" / "api.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")
NOTES = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")


def _js() -> str:
    return HTML.split('<script id="v0948-product-closure-js">', 1)[1].split("</script>", 1)[0]


def _fn(name: str) -> str:
    js = _js()
    start = js.index(name)
    # Functions in this closure script are intentionally compact and each next function
    # begins on its own line. This is enough for ownership assertions without parsing JS.
    tail = js[start:]
    nxt = tail.find("\nfunction ", 1)
    if nxt < 0:
        nxt = tail.find("\nconst ", 1)
    return tail if nxt < 0 else tail[:nxt]


def test_0948_release_identity():
    assert __version__ == "0.9.48"
    assert "<title>ArbScanner PoC 0.9.48</title>" in HTML
    assert 'EXPECTED_VERSION="0.9.48"' in INSTALLER
    assert "# 0.9.48 — Admin & Analytics Closure" in NOTES


def test_0948_get_state_returns_effective_defaults(tmp_path: Path):
    api = API(tmp_path / "state.sqlite3")
    api.db.set_setting("config", {"scan_interval_seconds": 77})
    state = api.get_state()
    cfg = state["settings"]["config"]
    assert cfg["scan_interval_seconds"] == 77
    assert cfg["settlement_poll_seconds"] == DEFAULT_CONFIG["settlement_poll_seconds"]
    assert cfg["price_scan_cache_limit"] == DEFAULT_CONFIG["price_scan_cache_limit"]
    assert "cfg = {**DEFAULT_CONFIG, **(self.db.get_setting(\"config\", DEFAULT_CONFIG) or {})}" in API_SOURCE


def test_0948_admin_is_tiled_and_responsibility_ordered():
    js = _js()
    for section in (
        "System & Safety",
        "Providers & Connections",
        "Accounts & Funding",
        "Market Data & Scanner",
        "Alerts",
        "Storage & Maintenance",
        "Technical Settings",
    ):
        assert section in js
    assert "admin-field-grid0948" in HTML
    assert "admin-technical-grid0948" in HTML
    assert "Runtime & cadence" in js
    assert "Data retention" in js
    assert "LIVE decision evidence safety" in js
    assert "Interface &amp; modelling" in js
    assert "page.querySelector('.workspace-head .viewactions')?.remove()" in js


def test_0948_admin_commands_do_not_broad_save():
    js = _js()
    # Final command overrides are deliberately side-effect narrow.
    test_connections = js.split("testConnections=async function()", 1)[1].split(";\ntestAlert=", 1)[0]
    test_alert = js.split("testAlert=async function()", 1)[1].split(";\nfunction technicalPatch0948", 1)[0]
    assert "saveAll" not in test_connections
    assert "save_settings" not in test_connections
    assert "saveAll" not in test_alert
    assert "save_settings" not in test_alert
    assert "syncPrimaryEngineStrategy0915" not in _fn("async function saveScannerAdmin0948")
    assert "alert_retry_minutes" not in _fn("function alertPatch0948")
    assert "alert_retry_minutes" in _fn("function technicalPatch0948")
    assert "beginner_mode" in _fn("function technicalPatch0948")
    assert "scenarios" not in _fn("async function saveScannerAdmin0948")


def test_0948_admin_navigation_no_dashboard_side_effect():
    # Both LIVE and SIM Admin routes load Admin account state directly.
    assert "if(id==='settings'){loadDashboardOverview();return loadAdminAccounts(false)}" not in HTML
    assert "if(id==='settings'){return loadAdminAccounts(false)}" in HTML
    assert "if(id==='settings'){loadAdminAccounts(false);return renderLiveModePanel('settings')}" in HTML


def test_0948_technical_settings_are_operator_visible_and_server_validated():
    js = _js()
    for key, control in (
        ("engine_max_concurrent_runtimes", "adminEngineConcurrency0948"),
        ("price_scan_cache_limit", "adminPriceCache0948"),
        ("settlement_poll_seconds", "adminSettlementPoll0948"),
        ("alert_retry_minutes", "adminAlertRetry0948"),
        ("snapshot_legacy_keep_rows", "adminSnapshotKeep0948"),
        ("snapshot_prune_batch_rows", "adminSnapshotBatch0948"),
        ("snapshot_maintenance_seconds", "adminSnapshotMaint0948"),
        ("matched_market_retention_hours", "adminMatchedRetention0948"),
        ("matched_market_prune_batch_rows", "adminMatchedBatch0948"),
        ("matched_market_heartbeat_seconds", "adminMatchedHeartbeat0948"),
        ("matched_market_maintenance_seconds", "adminMatchedMaint0948"),
        ("live_decision_max_quote_age_seconds", "adminLiveQuoteAge0948"),
        ("live_decision_max_receipt_spread_ms", "adminLiveSpread0948"),
        ("live_decision_min_mapping_confidence", "adminLiveMapping0948"),
    ):
        assert key in js
        assert control in js
        assert key in API_SOURCE
    assert "operator-visible technical settings are validated server-side" in API_SOURCE


def test_0948_performance_uses_buttons_exposure_only_and_direct_drag():
    js = _js()
    assert "upgradePerformanceFilters0948" in js
    assert "selectButtonGroup0948('performancePeriod'" in js
    assert "selectButtonGroup0948('performanceScope'" in js
    final_capital = js.split("renderCapitalTimeline0931=function(rows)", 1)[1].split("function updatePerformanceCopy0948", 1)[0]
    assert "Capital Exposure" in js
    assert "capital-exposure-line0948" in final_capital
    assert "available-path" not in final_capital
    assert "capital-path" not in final_capital
    assert "installPerformanceDrag0948" in js
    assert "pointerdown" in _fn("function installPerformanceDrag0948")
    assert "pointermove" in _fn("function installPerformanceDrag0948")
    assert "cursor:grab" in HTML and "cursor:grabbing" in HTML


def test_0948_market_analysis_buttons_and_heatmap_payload_hydration():
    js = _js()
    assert "selectButtonGroup0948('marketAnalysisPeriod'" in js
    assert "selectButtonGroup0948('marketAnalysisScope'" in js
    assert "#marketVenueSummary{display:none!important}" in HTML
    assert "heatmapSports0948" in js
    for stream in ("Pre-match", "In-play", "Racing"):
        assert stream in _fn("function renderHeatmapControls0948")
    loader = js.split("loadMarketHeatmapDay0835=async function", 1)[1].split(";loadMarketHeatmapDay=", 1)[0]
    assert "live_market_heatmap" in loader
    assert "market_heatmap" in loader
    assert "marketHeatmapPayload0842" in loader
    assert "applyHeatmapSport0948()" in loader
    assert "marketAnalysisWeekCells" in _fn("function applyHeatmapSport0948")


def test_0948_replay_buttons_dynamic_cursor_tiles_and_drag():
    js = _js()
    assert "selectButtonGroup0948('timelineReplayPeriod'" in js
    assert "selectButtonGroup0948('timelineReplayPhase'" in js
    assert ".replay-stream-grid{display:none!important}" in HTML
    assert "Sports in this period" in js
    assert "Engines in this period" in HTML
    assert "renderReplayCursorKpis0948" in js
    update = js.split("timelineReplayUpdateAt=function(progress", 1)[1].split(";\nconst __loadTimelineReplay0948", 1)[0]
    assert "renderReplayCursorKpis0948" in update
    assert "renderReplayActivityTiles0842" in update
    assert "installReplayDrag0948" in js
    assert "pointerdown" in _fn("function installReplayDrag0948")
    assert "pointermove" in _fn("function installReplayDrag0948")
    assert "let vals=[.5,1,2,5,10]" in HTML
    assert "speedButtons0940" in HTML


def test_0948_sports_freshness_uses_completed_scan_fallback():
    js = _js()
    fn = js.split("renderSportsOverview0935=function(r)", 1)[1].split(";\n\n/* ---------- final init", 1)[0]
    assert "scan.finished_at" in fn
    assert "scan.started_at" in fn
    assert "No completed scan recorded" in fn
    assert "Last Scan" in fn
