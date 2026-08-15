from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _api(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "accounts0933.sqlite3")
    api.service.status = lambda: {"installed": True, "loaded": True, "worker_path": "/tmp/worker"}
    return api


def test_0933_operational_feed_status_merges_price_and_discovery_per_provider(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    price_id = api.db.start_scan(scan_kind="price")
    api.db.finish_scan(
        price_id,
        statuses=[{"exchange": "Betfair delayed", "ok": True, "markets": 10, "latency_ms": 111, "message": "price ok"}],
    )
    discovery_id = api.db.start_scan(scan_kind="discovery")
    api.db.finish_scan(
        discovery_id,
        statuses=[{"exchange": "Matchbook", "ok": True, "markets": 12, "latency_ms": 222, "message": "discovery ok"}],
    )

    feeds = {x["provider_id"]: x for x in api._operational_status()["feeds"]}
    assert feeds["betfair"]["state"] == "connected"
    assert feeds["betfair"]["latency_ms"] == 111
    assert feeds["betfair"]["status_source"] == "price"
    assert feeds["matchbook"]["state"] == "connected"
    assert feeds["matchbook"]["latency_ms"] == 222
    assert feeds["matchbook"]["status_source"] == "discovery"
    assert feeds["smarkets"]["state"] == "awaiting_api_access"


def test_0933_enabled_feed_without_observation_is_waiting_not_unknown(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    feeds = {x["provider_id"]: x for x in api._operational_status()["feeds"]}
    for provider in ("betfair", "matchbook"):
        if feeds[provider]["enabled"]:
            assert feeds[provider]["state"] == "waiting"
            assert feeds[provider]["message"] == "Awaiting first feed observation"


def test_0933_sim_account_transactions_are_normalized_from_audited_funding_ledger(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    # accounts_page initializes the canonical SIM venue wallets.
    api.accounts_page({"mode": "sim", "period": "ALL"})
    assert api.sim_account_adjust({"exchange": "betfair", "action": "add", "value": 25.0})["ok"]
    assert api.sim_account_adjust({"exchange": "betfair", "action": "withdraw", "value": 10.0})["ok"]

    page = api.accounts_page({"mode": "sim", "period": "ALL"})
    rows = page["transactions"]
    types = [x["transaction_type"] for x in rows]
    assert "FUNDS ADDED" in types
    assert "FUNDS WITHDRAWN" in types
    assert page["transaction_summary"]["added"] >= 25.0
    assert page["transaction_summary"]["withdrawn"] >= 10.0
    assert page["transaction_summary"]["net_funding"] == page["transaction_summary"]["added"] - page["transaction_summary"]["withdrawn"]
    assert page["transaction_summary"]["transactions"] == len(rows)
    assert all(x["mode"] == "sim" for x in rows)


def test_0933_accounts_ui_is_read_only_transaction_history_not_configuration():
    start = HTML.index('<section id="accounts"')
    end = HTML.index('</section>', start) + len('</section>')
    accounts = HTML[start:end]
    assert "Account transactions" in accounts
    assert "accountsTxAdded" in accounts
    assert "accountsTxWithdrawn" in accounts
    assert "accountsTxNet" in accounts
    assert "accountsTxCount" in accounts
    assert "<th>Type</th>" in accounts
    assert "<th>Mode</th>" not in accounts
    assert 'type="checkbox"' not in accounts
    assert "SIM Funding" not in accounts
