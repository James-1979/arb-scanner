from __future__ import annotations

from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def make_api(tmp_path: Path) -> API:
    return API(tmp_path / "v099.sqlite3")


def test_v099_identity_and_full_release_contract():
    assert __version__ == "0.9.36"
    assert "ArbScanner PoC 0.9.36" in HTML
    assert "0.9.36 final mode-owned UI guards and page-native LIVE analytics" in HTML
    assert "runtime_state" in HTML
    assert "LIVE: shared provider market/liquidity evidence" in HTML
    assert "Scenarios are historical SIM modelling only" in HTML


def test_runtime_state_is_lightweight_and_cannot_carry_dashboard_economics(tmp_path):
    api = make_api(tmp_path)
    result = api.runtime_state({})
    assert result["ok"] is True
    assert result["version"] == "0.9.36"
    assert set(result) == {"ok", "version", "settings", "background", "operations"}
    assert "dashboard" not in result
    assert "jobs" not in result
    assert "config" not in result["settings"]
    assert "credentials" not in result["settings"]
    assert set(result["settings"]) == {"mode", "data_context_mode", "betfair_feed"}


def test_live_performance_actual_is_honest_empty_actual_state(tmp_path):
    api = make_api(tmp_path)
    result = api.live_performance({"basis": "actual"})
    assert result["mode"] == "live"
    assert result["basis"] == "actual"
    assert result["summary"]["positions_executed"] == 0
    assert result["summary"]["deployed_turnover"] == 0
    assert result["summary"]["net_pnl"] is None
    assert result["orders_write_capability"] is False
    assert result["live_execution_allowed"] is False


def test_live_performance_expected_uses_isolated_decision_evidence(tmp_path, monkeypatch):
    api = make_api(tmp_path)
    monkeypatch.setattr(api.db, "live_decision_analytics", lambda *a, **k: {
        "summary": {"observed": 20, "positive": 8, "qualified": 5, "simulated_attempts": 4,
                    "simulated_fills": 3, "execution_grade": 2, "expected_profit_sum": 12.5,
                    "executable_stake_sum": 250.0, "average_executable_stake": 50.0},
        "domains": [{"domain": "sports", "observed": 20, "qualified": 5, "simulated_attempts": 4,
                     "simulated_fills": 3, "expected_profit_sum": 12.5, "executable_stake_sum": 250.0}],
        "markets": [{"sport": "Tennis", "market_type": "Match Winner", "observed": 20, "qualified": 5,
                     "simulated_attempts": 4, "simulated_fills": 3, "expected_profit_sum": 12.5,
                     "executable_stake_sum": 250.0}],
        "provider_pairs": [{"provider_pair": "betfair+matchbook", "qualified": 5, "simulated_attempts": 4,
                            "simulated_fills": 3, "expected_profit_sum": 12.5, "executable_stake_sum": 250.0}],
        "hourly": [], "quality": [], "reasons": [],
    })
    result = api.live_performance({"basis": "simulated", "scope": "sports"})
    assert result["basis"] == "simulated"
    assert result["summary"]["net_pnl"] == 12.5
    assert result["summary"]["deployed_turnover"] == 250.0
    assert result["summary"]["positions_executed"] == 0  # actual executions stay zero
    assert result["performance"]["funnel"]["simulated_fills"] == 3
    assert result["performance"]["funnel"]["executed"] == 0
    assert result["performance"]["markets"][0]["settled"] == 0
    assert result["orders_write_capability"] is False
    assert "not LIVE account P&L" in result["basis_note"]


def test_live_market_analysis_keeps_shared_evidence_but_zeroes_actual_economics(tmp_path, monkeypatch):
    api = make_api(tmp_path)
    shared = {
        "ok": True,
        "rows": [{"section": "sports", "sport": "Tennis", "market_name": "Match Winner", "in_play": 0,
                  "unique_markets": 7, "observations": 70, "net_positive": 9, "qualified": 99,
                  "attempts": 8, "executed": 7, "settled": 6, "pnl": 123.45, "deployed": 500.0}],
        "liquidity_funnel": {"observed": 70, "positive": 9, "liquidity_capable": 6, "qualified": 5,
                             "attempted": 4, "executed": 3, "settled": 2},
        "venue_summary": [{"provider_id": "matchbook", "opportunities": 99, "market_count": 7}],
        "reasons": [], "racing_discovery": {}, "latest_racing_discovery": {},
    }
    calls = []
    def fake_market_analysis(data):
        calls.append(data)
        return {**shared, "rows": [dict(x) for x in shared["rows"]], "venue_summary": [dict(x) for x in shared["venue_summary"]]}
    monkeypatch.setattr(api, "market_analysis", fake_market_analysis)
    monkeypatch.setattr(api.db, "live_decision_analytics", lambda *a, **k: {
        "summary": {"qualified": 4},
        "markets": [{"domain": "sports", "sport": "Tennis", "market_type": "Match Winner", "qualified": 4,
                     "simulated_attempts": 3, "execution_grade": 2, "expected_profit_sum": 1.25,
                     "average_executable_stake": 20.0}],
        "quality": [], "reasons": [],
    })
    result = api.live_market_analysis({"scope": "sports"})
    assert calls and calls[0]["_include_economics"] is False
    row = result["rows"][0]
    assert row["observations"] == 70 and row["unique_markets"] == 7
    assert row["qualified"] == 0
    assert row["live_decision_qualified"] == 4
    assert result["live_decision_qualified"] == 4
    assert result["liquidity_funnel"]["qualified"] == 0
    assert row["live_simulated_attempts"] == 3
    assert row["executed"] == 0 and row["settled"] == 0 and row["pnl"] == 0.0 and row["deployed"] == 0.0
    assert result["venue_summary"][0]["opportunities"] == 0
    assert result["orders_write_capability"] is False


def test_heatmap_financial_work_can_be_skipped_for_live_shared_market_view(tmp_path, monkeypatch):
    api = make_api(tmp_path)
    monkeypatch.setattr(api.db, "ensure_market_financial_hourly_rollups",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("financial rollups must not run")))
    result = api.db.market_heatmap_between("2026-08-10T00:00:00+00:00", "2026-08-11T00:00:00+00:00", include_financial=False)
    assert result["financial"] == []
    assert result["source"] == "compact_hourly_rollups"


def test_frontend_mode_integrity_guards_cover_known_cross_mode_paths():
    # Periodic state is lightweight, not get_state.
    assert "refresh=async function(){let r=await callReadBounded('runtime_state'" in HTML
    # Account context no longer hardcodes SIM after switching LIVE.
    assert "let mode=dataContextMode,token=modeRequestToken(mode,sectionId)" in HTML
    assert "callReadBounded('account_overview',[{mode,capture:false" in HTML
    # Mode switch primes every economic/analytics family before async data arrives.
    for marker in ["id==='dashboard'", "id==='sports'", "id==='monitor'", "id==='racing'",
                   "id==='racing-monitor'", "id==='activebets'", "id==='analytics'"]:
        assert marker in HTML
    # Late LIVE results/execution/replay responses are route/domain guarded.
    assert "currentAnalyticsPane()!=='results'||resultsDomain!==domain" in HTML
    assert "currentAnalyticsPane()!=='execution'||executionAnalysisDomain!==domain" in HTML
    assert "currentAnalyticsPane()!=='replay'" in HTML
    # SIM heatmap is mode-namespaced and guards before rendering.
    assert "key='sim|'+marketHeatmapCacheKey0835()" in HTML
    assert "if(!r?.ok||!modeRequestCurrent(token,true)||currentAnalyticsPane()!=='market')return r" in HTML


def test_frontend_live_performance_uses_explicit_simulated_copy():
    assert "setPerformanceKpiCopy099('perfNetPnl','Expected profit'" in HTML
    assert "setPerformanceKpiCopy099('perfPositionsExecuted','Simulated fills'" in HTML
    assert "['attempted','Sim attempts']" in HTML
    assert "['simulated_fills','Sim fills']" in HTML
    assert "['execution_grade','Execution-grade']" in HTML
    assert "Venue execution contribution requires actual LIVE orders/fills" in HTML
