from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.adapters import BetfairDelayedAdapter
from arbscanner.api import API
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def make_api(tmp_path: Path) -> API:
    api = API(tmp_path / "v097.sqlite3")
    api.secrets = SecretStore(tmp_path / "secrets.json")
    api.scanner.secrets = api.secrets
    api.live_providers.secrets = api.secrets
    return api


def quote_row(exchange: str, provider_id: str, *, feed: str = "delayed") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "exchange": exchange,
        "provider_id": provider_id,
        "venue_id": provider_id,
        "event_id": f"{provider_id}-event",
        "event_name": "Alpha v Beta",
        "market_id": f"{provider_id}-market",
        "market_name": "Match Winner",
        "selection_id": "home",
        "selection": "Alpha",
        "side": "BACK",
        "odds": 2.10,
        "liquidity": 100.0,
        "captured_at": now,
        "source_latency_ms": 10,
        "commission_pct": 2.0,
        "market_type": "match winner",
        "strategy": "two-way",
        "sport": "Tennis",
        "in_play": False,
        "market_status": "OPEN",
        "section": "sports",
        "feed_entitlement": feed,
        "market_data_transport": "poll",
        "timestamp_quality": "LOCAL_RECEIPT",
        "quote_age_ms": 25,
        "depth_levels_json": '[{"side":"BACK","level":1,"odds":2.10,"available_size":100}]',
    }


def test_v097_identity_and_live_ui_is_not_globally_hidden():
    assert __version__ == "0.9.36"
    assert "ArbScanner PoC 0.9.36" in HTML
    assert 'page.active:not(#accounts):not(#sports-config):not(#racing-config)>*' not in HTML
    assert 'body[data-data-mode="live"] #dashScanBtn{display:none!important}' not in HTML
    assert "LIVE scanning/feed provider not integrated" not in HTML
    assert "LIVE Racing feed provider not integrated" not in HTML
    assert "function orchestrateRouteLoad" in HTML
    assert "loadLiveExecutions" in HTML
    assert "loadLiveResults" in HTML
    assert "loadLivePerformance" in HTML
    assert "loadLiveReplay" in HTML
    assert "loadLiveMarketAnalysis" in HTML


def test_v097_betfair_feed_ui_has_requested_effective_and_two_key_classes():
    assert 'id="bfMarketFeed"' in HTML
    assert '<option value="delayed">DELAYED</option>' in HTML
    assert '<option value="live">LIVE</option>' in HTML
    assert 'id="bfKey"' in HTML
    assert 'id="bfLiveKey"' in HTML
    assert 'id="bfRequestedFeed"' in HTML
    assert 'id="bfEffectiveFeed"' in HTML
    assert 'id="bfFeedReason"' in HTML
    assert "set_betfair_market_feed" in HTML
    assert "betfair_feed_entitlement" in HTML


def test_betfair_feed_defaults_delayed_and_status_contains_no_secret_value(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set_many({"betfair_app_key": "DELAYED-SECRET", "betfair_session_token": "TOKEN-SECRET"})
    status = api.betfair_feed_status()
    assert status["requested_feed_entitlement"] == "delayed"
    assert status["effective_feed_entitlement"] == "delayed"
    assert status["delayed_app_key_configured"] is True
    assert status["live_app_key_configured"] is False
    assert status["orders_write_capability"] is False
    assert status["live_execution_allowed"] is False
    assert "DELAYED-SECRET" not in repr(status)
    assert "TOKEN-SECRET" not in repr(status)
    state = api.get_state()
    assert "DELAYED-SECRET" not in repr(state["settings"]["betfair_feed"])
    assert "TOKEN-SECRET" not in repr(state["settings"]["betfair_feed"])


def test_requesting_live_without_live_key_fails_closed_and_does_not_change_mode(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set("betfair_app_key", "DELAYED-KEY")
    api.set_data_context_mode({"mode": "live", "generation": 10})
    result = api.set_betfair_market_feed({"feed": "live"})
    assert result["ok"] is True
    assert result["requested_feed_entitlement"] == "live"
    assert result["effective_feed_entitlement"] == "unavailable"
    assert "not configured" in (result["feed_reason"] or "").lower()
    assert result["orders_write_capability"] is False
    assert result["live_execution_allowed"] is False
    assert api.db.get_setting("data_context_mode") == "live"
    assert api.db.get_setting("mode") == "sim"


def test_requesting_live_with_key_stays_unknown_until_provider_confirms(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set_many({"betfair_live_app_key": "LIVE-KEY", "betfair_session_token": "TOKEN"})
    result = api.set_betfair_market_feed({"feed": "live"})
    assert result["requested_feed_entitlement"] == "live"
    assert result["effective_feed_entitlement"] == "unknown"
    assert "confirmation" in (result["feed_reason"] or "").lower()
    assert result["live_execution_allowed"] is False


def test_betfair_effective_feed_comes_from_marketbook_not_selector():
    live_requested = BetfairDelayedAdapter(app_key="x", session_token="y", requested_feed_entitlement="live")
    assert live_requested._effective_feed_from_book({"isMarketDataDelayed": True}) == "delayed"
    assert live_requested._effective_feed_from_book({"isMarketDataDelayed": False}) == "live"
    assert live_requested._effective_feed_from_book({}) == "unknown"

    delayed_requested = BetfairDelayedAdapter(app_key="x", session_token="y", requested_feed_entitlement="delayed")
    assert delayed_requested._effective_feed_from_book({}) == "delayed"


def test_feed_change_invalidates_only_bounded_betfair_market_state(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set_many({"betfair_app_key": "D", "betfair_live_app_key": "L", "betfair_session_token": "T"})
    api.db.upsert_latest_snapshots([
        quote_row("Betfair delayed", "betfair", feed="delayed"),
        quote_row("Matchbook", "matchbook", feed="live"),
    ])
    # Seed SIM economic state to prove a feed switch does not mutate it.
    api.db.reset_monitor_wallets({"betfair": 111.0, "matchbook": 222.0}, stream="pre_match", capture_snapshot=False)
    before_sim = api.account_overview({"mode": "sim", "capture": False})
    before_bf = api.db.conn.execute("SELECT COUNT(*) FROM latest_snapshots WHERE LOWER(exchange) LIKE 'betfair%'").fetchone()[0]
    before_mb = api.db.conn.execute("SELECT COUNT(*) FROM latest_snapshots WHERE LOWER(exchange)='matchbook'").fetchone()[0]
    before_depth_bf = api.db.conn.execute("SELECT COUNT(*) FROM latest_depth_snapshots WHERE provider_id='betfair'").fetchone()[0]
    assert before_bf == 1 and before_mb == 1 and before_depth_bf >= 1

    result = api.set_betfair_market_feed({"feed": "live"})
    assert result["requested_feed_entitlement"] == "live"
    assert result["effective_feed_entitlement"] == "unknown"
    assert api.db.conn.execute("SELECT COUNT(*) FROM latest_snapshots WHERE LOWER(exchange) LIKE 'betfair%'").fetchone()[0] == 0
    assert api.db.conn.execute("SELECT COUNT(*) FROM latest_depth_snapshots WHERE provider_id='betfair'").fetchone()[0] == 0
    assert api.db.conn.execute("SELECT COUNT(*) FROM latest_snapshots WHERE LOWER(exchange)='matchbook'").fetchone()[0] == 1
    after_sim = api.account_overview({"mode": "sim", "capture": False})
    before_money = {k: (v.get("available"), v.get("reserved"), v.get("equity")) for k, v in before_sim["accounts"].items()}
    after_money = {k: (v.get("available"), v.get("reserved"), v.get("equity")) for k, v in after_sim["accounts"].items()}
    assert before_money == after_money


def test_actual_live_economic_read_models_are_valid_empty_not_sim_fallback(tmp_path):
    api = make_api(tmp_path)
    # Seed unmistakable SIM evidence that must not appear in LIVE read models.
    api.db.reset_monitor_wallets({"betfair": 1234.0, "matchbook": 5678.0}, stream="pre_match", capture_snapshot=False)
    execution = api.live_execution_activity({"domain": "sports"})
    results = api.live_results({})
    performance = api.live_performance({})
    replay = api.live_replay({})
    assert execution["mode"] == "live" and execution["rows"] == [] and execution["metrics"]["attempted"] == 0
    assert execution["metrics"]["filled"] == 0 and execution["orders_write_capability"] is False
    assert results["mode"] == "live" and results["rows"] == [] and results["count"] == 0
    assert performance["mode"] == "live" and performance["rows"] == []
    assert performance["summary"]["positions_executed"] == 0
    assert performance["summary"]["net_pnl"] is None
    assert replay["mode"] == "live" and replay["rows"] == [] and replay["count"] == 0
    assert "1234" not in repr(execution) + repr(results) + repr(performance) + repr(replay)
    assert "5678" not in repr(execution) + repr(results) + repr(performance) + repr(replay)


def test_feed_and_global_economic_context_are_independent(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set("betfair_app_key", "D")
    live_context = api.set_data_context_mode({"mode": "live", "generation": 50})
    feed = api.set_betfair_market_feed({"feed": "delayed"})
    assert live_context["data_context_mode"] == "live"
    assert feed["requested_feed_entitlement"] == "delayed"
    assert api.db.get_setting("data_context_mode") == "live"
    assert api.db.get_setting("mode") == "sim"
    # Selecting a feed does not change the global data context.
    api.secrets.set("betfair_live_app_key", "L")
    api.set_betfair_market_feed({"feed": "live"})
    assert api.db.get_setting("data_context_mode") == "live"
    assert api.db.get_setting("mode") == "sim"


def test_live_order_write_boundary_remains_locked(tmp_path):
    api = make_api(tmp_path)
    state = api.get_state()
    assert state["settings"]["live_execution_available"] is False
    feed = api.betfair_feed_status()
    assert feed["orders_write_capability"] is False
    assert feed["live_execution_allowed"] is False


def test_manual_scan_freezes_requested_data_context_and_never_unlocks_execution(tmp_path):
    api = make_api(tmp_path)
    calls = []

    def fake_scan_once(job_id=None, *, data_context_mode=None):
        calls.append(data_context_mode)
        return {"ok": True, "found": [], "pipeline": {"processed": 0, "opportunities": 0, "qualified": 0, "executed": 0}}

    api.scanner.scan_once = fake_scan_once
    result = api.run_scan_now({"data_context_mode": "live", "generation": 100})
    assert result["ok"] is True
    assert calls == ["live"]
    assert result["data_context_mode"] == "live"
    assert result["operating_mode"] == "sim"
    assert result["state"]["settings"]["live_execution_available"] is False

    # A stale manual scan request is cancelled instead of crossing into the
    # opposite evidence/economic sink.
    api.set_data_context_mode({"mode": "sim", "generation": 200})
    stale = api.run_scan_now({"data_context_mode": "live", "generation": 150})
    assert stale["ok"] is False
    assert stale["stale_request"] is True
    assert stale["data_context_mode"] == "sim"
    assert calls == ["live"]


def test_frontend_manual_scan_synchronizes_and_passes_mode_generation():
    assert "function manualScanContext" in HTML
    assert "await syncBackendDataContextMode(requested)" in HTML
    assert "run_scan_now',{data_context_mode:ctx.mode,generation:ctx.generation}" in HTML


def test_runtime_effective_feed_can_remain_delayed_when_live_was_requested(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set_many({"betfair_live_app_key": "LIVE-KEY", "betfair_session_token": "TOKEN"})
    selected = api.set_betfair_market_feed({"feed": "live"})
    assert selected["requested_feed_entitlement"] == "live"
    assert selected["effective_feed_entitlement"] == "unknown"

    api.provider_runtime.update_market_health(
        "betfair",
        ok=True,
        requested_feed_entitlement="live",
        effective_feed_entitlement="delayed",
        feed_reason="Betfair MarketBook reports delayed market data",
        feed_generation=selected["feed_generation"],
    )
    status = api.betfair_feed_status()
    assert status["requested_feed_entitlement"] == "live"
    assert status["effective_feed_entitlement"] == "delayed"
    assert "delayed" in (status["feed_reason"] or "").lower()
    assert status["orders_write_capability"] is False


def test_betfair_live_app_key_is_redacted_from_live_provider_diagnostics(tmp_path):
    api = make_api(tmp_path)
    api.secrets.set_many({"betfair_live_app_key": "LIVE-KEY-SECRET", "betfair_session_token": "TOKEN"})
    redacted = api.live_providers._redact_message("provider failed LIVE-KEY-SECRET while reading")
    assert "LIVE-KEY-SECRET" not in redacted
    assert "[REDACTED]" in redacted
