from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0831_release_ui_and_provider_boundary():
    assert __version__ == "0.9.36"
    for token in (
        '<title>ArbScanner PoC 0.9.36</title>',
        'id="globalDataModeSim"',
        'id="globalDataModeLive"',
        "setGlobalDataMode('sim')",
        "setGlobalDataMode('live')",
        'simAccountAction',
        'live_decision_evidence',
        'live_performance',
        'live_market_analysis',
        'SIM data is not used as fallback.',
        'aspect-ratio:1/1!important',
        'LIVE · ACTUAL ONLY',
        'Actual LIVE provider/account state only; no SIM fallback.',
    ):
        assert token in HTML


def test_live_provider_is_explicitly_isolated_and_empty(tmp_path):
    api = API(tmp_path / "isolated.sqlite3")
    # Make the SIM values unmistakably non-zero/different.
    changed = api.sim_account_adjust({"exchange": "betfair", "action": "add", "value": 321.0, "reason": "test"})
    assert changed["ok"] is True
    sim = api.account_overview({"mode": "sim"})
    assert sim["accounts"]["betfair"]["equity"] == 1071.0

    live = api.account_overview({"mode": "live"})
    assert live["mode"] == "live"
    assert live["provider"] == "live_account_provider"
    for account in live["accounts"].values():
        assert account["source"] == "live_account_provider"
        assert account["available"] is None
        assert account["equity"] is None
        assert account["freshness"] == "UNAVAILABLE"
        assert account["order_placement_enabled"] is False
    assert live["accounts"]["smarkets"]["integration_pending"] is True
    assert live["accounts"]["betfair"]["equity"] != sim["accounts"]["betfair"]["equity"]

    manifest = api.data_provider_manifest({})
    assert manifest["sim"]["isolated_from_live"] is True
    assert manifest["live"]["isolated_from_sim"] is True
    assert manifest["shared_market_data"]["available"] is True
    assert set(manifest["shared_market_data"]["consumers"]) == {"sim", "live"}
    # The read-only account provider does not own market acquisition; shared provider runtime does.
    assert manifest["live"]["capabilities"]["market_feed"] is False
    assert manifest["live"]["capabilities"]["order_placement"] is False


def test_live_page_contract_never_reads_sim_rows(tmp_path):
    api = API(tmp_path / "pages.sqlite3")
    stub = api.live_view_data({"page": "Results"})
    assert stub["ok"] is True
    assert stub["mode"] == "live"
    assert stub["available"] is False
    assert stub["rows"] == []
    assert stub["provider"] == "live_account_provider"
    assert "no SIM economic/execution data" in stub["message"]


def test_sim_account_adjustments_are_audited_without_rewriting_opening_balance(tmp_path):
    api = API(tmp_path / "funding.sqlite3")
    before = api.account_overview({"mode": "sim"})["accounts"]["matchbook"]
    assert before["opening_balance"] == 750.0
    assert before["equity"] == 750.0
    assert before["funding_adjustment"] == 0.0

    added = api.sim_account_adjust({"exchange": "matchbook", "action": "add", "value": 150.0, "reason": "top up"})
    assert added["ok"] is True
    after = api.account_overview({"mode": "sim"})["accounts"]["matchbook"]
    assert after["opening_balance"] == 750.0
    assert after["equity"] == 900.0
    assert after["funding_adjustment"] == 150.0
    assert sum(x["equity"] for x in after["allocations"]) == 900.0
    assert after["reconciliation"]["status"] == "RECONCILED"

    history = api.sim_account_adjustment_history({"exchange": "matchbook"})["rows"]
    assert history and history[0]["action"] == "add"
    assert history[0]["amount"] == 150.0
    assert history[0]["previous_equity"] == 750.0
    assert history[0]["resulting_equity"] == 900.0

    set_result = api.sim_account_adjust({"exchange": "matchbook", "action": "set", "value": 825.0, "reason": "set target"})
    assert set_result["ok"] is True
    set_state = api.account_overview({"mode": "sim"})["accounts"]["matchbook"]
    assert set_state["opening_balance"] == 750.0
    assert set_state["equity"] == 825.0
    assert set_state["funding_adjustment"] == 75.0


def test_sim_withdrawal_never_consumes_reserved_capital(tmp_path):
    api = API(tmp_path / "reserved.sqlite3")
    # Directly reserve most of one stream wallet; account withdrawal can only use available cash.
    with api.db.lock:
        api.db.conn.execute("UPDATE monitor_stream_wallets SET available_balance=0,reserved_balance=250 WHERE stream='pre_match' AND exchange='betfair'")
        api.db.conn.commit()
    state = api.account_overview({"mode": "sim"})["accounts"]["betfair"]
    available = state["available"]
    reserved = state["reserved"]
    assert reserved == 250.0
    fail = api.sim_account_adjust({"exchange": "betfair", "action": "withdraw", "value": available + 1.0})
    assert fail["ok"] is False
    after = api.account_overview({"mode": "sim"})["accounts"]["betfair"]
    assert after["reserved"] == reserved


def test_sim_history_uses_legacy_monitor_storage_but_live_history_is_separate(tmp_path):
    api = API(tmp_path / "history.sqlite3")
    api.sim_account_adjust({"exchange": "betfair", "action": "add", "value": 10.0})
    sim_hist = api.account_snapshot_history({"mode": "sim", "limit": 100})["rows"]
    live_hist = api.account_snapshot_history({"mode": "live", "limit": 100})["rows"]
    assert sim_hist
    assert all(row["mode"] == "sim" for row in sim_hist)
    assert live_hist == []
