from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.market_analytics import (
    HEATMAP_METRICS,
    MarketFilters,
    heatmap_metric_ownership,
    live_heatmap_cell,
    live_market_row,
    market_row_matches,
    market_stream,
)

ROOT = Path(__file__).resolve().parents[1]


def test_market_filter_contract_normalizes_default_all_streams_without_losing_requested_set():
    filters = MarketFilters.from_data({
        "scope": "sports",
        "phase": "all",
        "sport": "Football",
        "search": " Match ",
        "streams": "pre_match,in_play,racing",
    })
    assert filters.scope == "sports"
    assert filters.sport == "Football"
    assert filters.search == "match"
    assert filters.streams == frozenset()
    assert filters.requested_streams == frozenset({"pre_match", "in_play", "racing"})
    assert filters.selected_streams_response == ["pre_match", "in_play", "racing"]


def test_racing_stream_is_domain_owned_and_not_reclassified_as_sports_phase():
    racing = {"section": "racing", "phase": "in_play", "in_play": 1}
    assert market_stream(racing) == "racing"
    assert market_stream(racing, phase_hint=False) == "racing"


def test_heatmap_stream_compatibility_uses_in_play_flag_not_phase_hint():
    row = {"section": "sports", "phase": "pre_match", "in_play": 1, "sport": "Tennis", "market_name": "Match Odds"}
    filters = MarketFilters.from_data({"streams": ["in_play"]})
    assert market_row_matches(row, filters, stream_phase_hint=False) is True
    assert market_row_matches(row, filters, stream_phase_hint=True) is False


def test_live_domain_rules_preserve_analysis_and_heatmap_legacy_distinction():
    # Market Analysis normalises the complete stream set to All before domain selection.
    filters = MarketFilters.from_data({"scope": "sports", "streams": ["pre_match", "in_play", "racing"]})
    assert filters.live_domain == "sports"
    # Heatmap historically treats an explicitly supplied mixed set as cross-domain.
    assert filters.live_heatmap_domain == "all"

    sports = MarketFilters.from_data({"scope": "all", "streams": ["pre_match"]})
    assert sports.live_domain == "sports"
    assert sports.live_heatmap_domain == "sports"
    racing = MarketFilters.from_data({"scope": "all", "streams": ["racing"]})
    assert racing.live_domain == "racing"
    assert racing.live_heatmap_domain == "racing"


def test_heatmap_metric_ownership_is_explicit_and_mode_owned():
    sim = heatmap_metric_ownership("sim")
    live = heatmap_metric_ownership("live")
    assert list(sim) == list(HEATMAP_METRICS)
    for metric in ("observations", "unique_markets", "net_positive", "available_depth", "top_book_depth", "avg_executable_stake"):
        assert sim[metric] == live[metric] == "shared"
    for metric in ("qualified", "executed", "settled", "deployed", "settled_deployed", "pnl", "roi_pct"):
        assert sim[metric] == "sim"
        assert live[metric] == "live"
    assert live["decision_qualified_evidence"] == "live_diagnostic"


def test_live_heatmap_sanitizer_preserves_shared_evidence_and_zeroes_actual_lifecycle():
    source = {
        "observations": 9, "unique_markets": 4, "net_positive": 3,
        "available_depth": 120.0, "qualified": 8, "executed": 7,
        "settled": 6, "deployed": 50.0, "settled_deployed": 40.0,
        "pnl": 5.5, "roi_pct": 13.75,
    }
    result = live_heatmap_cell(source, decision_count=11)
    assert result["observations"] == 9
    assert result["available_depth"] == 120.0
    assert result["decision_qualified_evidence"] == 11
    assert result["qualified"] == result["executed"] == result["settled"] == 0
    assert result["deployed"] == result["settled_deployed"] == result["pnl"] == result["roi_pct"] == 0.0
    assert source["qualified"] == 8  # pure projection; input is untouched


def test_live_market_row_preserves_market_evidence_but_never_sim_economics():
    row = {
        "section": "sports", "sport": "Tennis", "market_name": "Match Winner",
        "unique_markets": 7, "observations": 70, "qualified": 6, "attempts": 5,
        "executed": 4, "settled": 3, "pnl": 9.5, "deployed": 100.0,
        "returned": 109.5, "wins": 3, "losses": 0, "execution_conversion_pct": 66.7,
    }
    decision = {
        "qualified": 2, "simulated_attempts": 2, "execution_grade": 1,
        "expected_profit_sum": 4.25, "average_executable_stake": 20.0,
    }
    result = live_market_row(row, decision)
    assert result["unique_markets"] == 7 and result["observations"] == 70
    assert result["qualified"] == result["attempts"] == result["executed"] == result["settled"] == 0
    assert result["pnl"] == result["deployed"] == result["returned"] == 0.0
    assert result["live_decision_qualified"] == 2
    assert result["live_simulated_attempts"] == 2
    assert result["expected_simulated_profit"] == 4.25
    assert row["pnl"] == 9.5


def test_direct_live_selected_market_analysis_skips_sim_economic_sql(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "market.sqlite3")
    captured = {}

    def summary(started_at, finished_at, *, include_economics=True):
        captured["include_economics"] = include_economics
        return {
            "history_from_utc": None, "history_to_utc": None,
            "rows": [], "reasons": [], "activity_hours": [], "execution_hours": [],
            "exchange_discovery_rows": [], "opportunity_venue_rows": [],
            "sports_discovery": {}, "sports_scans": [], "racing_discovery": {}, "racing_scans": [],
            "summary_history_complete": True, "detailed_history_complete": True,
        }

    monkeypatch.setattr(api.analytics_store, "market_summary", summary)
    result = api.market_analysis({"mode": "live", "scope": "all"})
    assert result["ok"] is True
    assert captured["include_economics"] is False


def test_live_market_heatmap_keeps_shared_provider_evidence_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "heatmap.sqlite3")
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    finish = start + timedelta(days=1)
    hour = start.isoformat()
    monkeypatch.setattr(api.db, "market_heatmap_between", lambda *_a, **_k: {
        "source": "shared_provider_evidence", "financial_source": "none",
        "rollups": [{"hour_utc": hour, "section": "sports", "sport": "Football", "market_name": "Match Odds", "in_play": 0,
                     "observations": 10, "unique_markets": 5, "net_positive": 4}],
        # Even if a malformed source attempted to present SIM financial rows, the
        # LIVE wrapper requests include_financial=False and the sanitizer remains fail-closed.
        "financial": [], "liquidity_depth": [], "liquidity_opportunity": [],
    })
    monkeypatch.setattr(api.db, "live_decision_analytics", lambda *_a, **_k: {
        "hourly": [{"hour_utc": hour, "qualified": 3}],
        "hourly_by_sport": [{"hour_utc": hour, "sport": "Football", "qualified": 3}],
    })
    result = api.live_market_heatmap({
        "from_utc": start.isoformat(), "to_utc": finish.isoformat(), "scope": "sports",
        "phase": "all", "sport": "all", "timezone_name": "UTC", "timezone_offset_minutes": 0,
    })
    cell = next(x for x in result["cells"] if x["date"] == "2026-08-10" and x["hour"] == 0)
    assert cell["observations"] == 10 and cell["unique_markets"] == 5
    assert cell["decision_qualified_evidence"] == 3
    assert cell["qualified"] == cell["executed"] == cell["settled"] == 0
    assert cell["pnl"] == 0.0 and cell["deployed"] == 0.0
    assert result["metric_ownership"]["observations"] == "shared"
    assert result["metric_ownership"]["pnl"] == "live"
    assert result["live_execution_allowed"] is False


def test_market_analytics_module_is_pure_and_has_no_db_or_provider_runtime_dependency():
    import arbscanner.market_analytics as market_analytics

    source = (ROOT / "arbscanner" / "market_analytics.py").read_text(encoding="utf-8")
    assert not hasattr(market_analytics, "DB")
    assert not hasattr(market_analytics, "LiveProviderRegistry")
    assert "from .db import" not in source
    assert "from .live_providers import" not in source


def test_direct_live_selected_market_analysis_does_not_read_sim_lifecycle_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "live-read.sqlite3")
    statements: list[str] = []
    api.db.conn.set_trace_callback(statements.append)
    result = api.market_analysis({"mode": "live", "scope": "all"})
    api.db.conn.set_trace_callback(None)
    assert result["ok"] is True
    sql = "\n".join(" ".join(statement.lower().split()) for statement in statements)
    for table in ("opportunities", "monitor_positions", "execution_runs", "settlements"):
        assert f" from {table} " not in f" {sql} "
        assert f" join {table} " not in f" {sql} "
    # LIVE Market Analysis may inspect LIVE account readiness for its selected-mode
    # feed RAG, but it must not traverse the SIM account authority.
    assert " from account_snapshots " not in f" {sql} "
    assert " join account_snapshots " not in f" {sql} "
    assert " from live_account_snapshots " in f" {sql} "
