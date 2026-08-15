from __future__ import annotations

import re
from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.account_providers import AccountProvider

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _seed_stream_wallets(api: API) -> None:
    api.db.reset_monitor_wallets({"betfair": 100.0, "matchbook": 200.0}, stream="pre_match", capture_snapshot=False)
    api.db.reset_monitor_wallets({"betfair": 50.0, "matchbook": 75.0}, stream="in_play", capture_snapshot=False)
    api.db.reset_monitor_wallets({"betfair": 25.0, "matchbook": 30.0}, stream="racing", capture_snapshot=False)


def _function_body(name: str) -> str:
    marker = f"function {name}("
    start = HTML.find(marker)
    assert start >= 0, f"missing function {name}"
    brace = HTML.find("{", start)
    assert brace >= 0
    depth = 0
    quote = None
    escaped = False
    template_depth = 0
    for i in range(brace, len(HTML)):
        ch = HTML[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {"'", '"', '`'}:
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return HTML[brace + 1:i]
    raise AssertionError(f"unterminated function {name}")


def test_v094_identity_and_one_global_mode_control():
    assert __version__ == "0.9.39"
    assert '<title>ArbScanner PoC 0.9.39</title>' in HTML
    assert HTML.count('class="global-data-mode"') == 1
    assert 'id="globalDataModeSim"' in HTML and 'id="globalDataModeLive"' in HTML
    assert 'id="accountsModeSelector0918"' not in HTML
    assert 'id="accountsModeBadge"' in HTML
    assert 'id="adminAccountModeBadge"' in HTML
    assert 'id="accountModeMonitor"' not in HTML
    assert 'id="accountModeLive"' not in HTML
    assert 'id="adminAccountMode"' not in HTML


def test_v094_frontend_has_single_mode_authority_and_epoch_guards():
    assert "accountContextMode" not in HTML
    assert "var dataContextMode='sim'" in HTML
    assert "var modeEpoch=0" in HTML
    assert "function modeRequestToken" in HTML
    assert "function modeRequestCurrent" in HTML
    assert "function orchestrateRouteLoad" in HTML
    assert HTML.count("function orchestrateRouteLoad") == 1
    assert "queueMicrotask(()=>orchestrateRouteLoad(activePageId()))" in HTML
    # Saved mode is resolved in the primary startup path rather than a delayed second-mode boot.
    assert "setTimeout(()=>{let m=localStorage.getItem('arbscannerDataMode')" not in HTML


def test_v094_live_config_sync_is_pure_and_does_not_recursively_load_accounts():
    sync_body = _function_body("syncLiveConfigControls")
    assert "loadLiveConfigAccountContext(" not in sync_body
    load_body = _function_body("loadLiveConfigAccountContext")
    assert load_body.count("accounts_page") == 1
    assert "modeRequestToken('live',pageId)" in load_body
    assert "modeRequestCurrent(token,true)" in load_body


def test_sim_accounts_aggregate_actual_virtual_wallets_without_live_health(tmp_path):
    api = API(tmp_path / "sim-aggregate.sqlite3")
    _seed_stream_wallets(api)
    # A real provider can be disabled/unavailable without changing SIM economic state.
    for spec in api.provider_runtime.providers.all():
        api.provider_runtime.set_runtime_enabled(spec.provider_id, False)

    page = api.accounts_page({"mode": "sim", "period": "ALL"})
    assert page["ok"] is True
    assert page["mode"] == "sim"
    assert page["current"]["currency"] == "GBP"
    assert page["current"]["available"] == pytest.approx(480.0)
    assert page["current"]["balance"] == pytest.approx(480.0)
    assert page["current"]["exposure"] == pytest.approx(0.0)
    assert page["current"]["venue_accounts"] == 2
    assert {row["provider_id"] for row in page["providers"]} == {"betfair", "matchbook", "smarkets"}
    actual = [row for row in page["providers"] if row["provider_id"] != "smarkets"]
    assert all(row["mode"] == "sim" for row in actual)
    sm = next(row for row in page["providers"] if row["provider_id"] == "smarkets")
    assert sm["integration_pending"] is True and sm["available"] is None
    api.db.conn.close()


def test_sim_financial_revision_is_monotonic_and_consistent_across_account_views(tmp_path):
    api = API(tmp_path / "revision.sqlite3")
    _seed_stream_wallets(api)
    before = api.db.sim_financial_revision()
    result = api.db.adjust_sim_account(exchange="betfair", action="add", value=20.0, reason="v094 test")
    assert result["ok"] is True
    after = api.db.sim_financial_revision()
    assert after > before

    overview = api.account_overview({"mode": "sim"})
    page = api.accounts_page({"mode": "sim", "period": "ALL"})
    dashboard = api.dashboard_overview()
    assert overview["financial_revision"] == after
    assert page["financial_revision"] == after
    assert dashboard["financial_revision"] == after
    assert page["current"]["balance"] == pytest.approx(500.0)
    api.db.conn.close()


def test_live_accounts_never_fall_back_to_known_sim_money(tmp_path):
    api = API(tmp_path / "isolation.sqlite3")
    api.db.reset_monitor_wallets({"betfair": 1234.0, "matchbook": 5678.0}, stream="pre_match", capture_snapshot=False)
    api.db.reset_monitor_wallets({"betfair": 0.0, "matchbook": 0.0}, stream="in_play", capture_snapshot=False)
    api.db.reset_monitor_wallets({"betfair": 0.0, "matchbook": 0.0}, stream="racing", capture_snapshot=False)
    sim = api.accounts_page({"mode": "sim", "period": "ALL"})
    assert sim["current"]["available"] == pytest.approx(6912.0)

    # Prevent all real-account network clients. LIVE must return unavailable state, never SIM values.
    for spec in api.provider_runtime.providers.all():
        api.provider_runtime.set_runtime_enabled(spec.provider_id, False)
    live = api.accounts_page({"mode": "live", "period": "ALL", "refresh": False})
    assert live["mode"] == "live"
    assert live["read_only"] is True
    assert live["live_order_placement"] is False
    assert live["current"]["available"] is None
    assert live["current"]["balance"] is None
    assert live["current"]["available"] != sim["current"]["available"]
    api.db.conn.close()


def test_reset_monitor_balances_returns_canonical_sim_account_state_and_revision(tmp_path):
    api = API(tmp_path / "reset.sqlite3")
    _seed_stream_wallets(api)
    before = api.db.sim_financial_revision()
    response = api.reset_monitor_balances({"stream": "racing", "balances": {"betfair": 70.0, "matchbook": 80.0}, "force": True})
    assert response["ok"] is True
    assert response["account_overview"]["mode"] == "sim"
    assert response["account_overview"]["financial_revision"] > before
    racing = response["wallets"]["racing"]
    assert racing["betfair"]["equity"] == pytest.approx(70.0)
    assert racing["matchbook"]["equity"] == pytest.approx(80.0)
    api.db.conn.close()


def test_v094_preserves_read_only_live_account_contract():
    forbidden = {"place_order", "replace_order", "update_order", "cancel_order", "cancel_all", "submit_order"}
    assert not forbidden.intersection(set(dir(AccountProvider)))
    assert "LIVE order writing remains centrally locked" in HTML
