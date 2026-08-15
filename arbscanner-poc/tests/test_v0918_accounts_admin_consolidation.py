from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.provider_runtime import default_provider_runtime_registry
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).resolve().parents[1]


def make_api(tmp_path, monkeypatch) -> API:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return API(tmp_path / "arbscanner.sqlite3")


def test_0918_venue_controls_are_mode_specific_by_default(tmp_path):
    db = DB(tmp_path / "controls.sqlite3")
    rows = {x["provider_id"]: x for x in db.venue_controls()}
    assert set(rows) == {"betfair", "matchbook", "smarkets"}
    for pid in ("betfair", "matchbook"):
        assert rows[pid]["sim_feed_enabled"] is True
        assert rows[pid]["live_feed_enabled"] is False
        assert rows[pid]["sim_account_enabled"] is True
        assert rows[pid]["live_account_enabled"] is True
        assert rows[pid]["live_execution_enabled"] is False
    assert rows["smarkets"]["sim_feed_enabled"] is False
    assert rows["smarkets"]["live_feed_enabled"] is False
    assert rows["smarkets"]["sim_account_enabled"] is False
    assert rows["smarkets"]["live_account_enabled"] is False


def test_0918_sim_live_feed_controls_are_independent_and_transport_is_or_gate(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    first = api.update_venue_control({"provider_id": "matchbook", "sim_feed_enabled": False})
    assert first["ok"] is True
    row = api.db.venue_control("matchbook")
    assert row["sim_feed_enabled"] is False
    assert row["live_feed_enabled"] is False
    assert api.db.get_setting("config", {}).get("matchbook_enabled") is False

    second = api.update_venue_control({"provider_id": "matchbook", "live_feed_enabled": True})
    assert second["ok"] is True
    row = api.db.venue_control("matchbook")
    assert row["sim_feed_enabled"] is False
    assert row["live_feed_enabled"] is True
    assert api.db.get_setting("config", {}).get("matchbook_enabled") is True

    third = api.update_venue_control({"provider_id": "matchbook", "sim_feed_enabled": True})
    assert third["ok"] is True
    row = api.db.venue_control("matchbook")
    assert row["sim_feed_enabled"] is True
    assert row["live_feed_enabled"] is True


def test_0918_scanner_applies_sim_and_live_consumption_gates_independently(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    db = DB(tmp_path / "scanner.sqlite3")
    runtime = default_provider_runtime_registry()
    scanner = Scanner(db, SecretStore(), provider_runtime=runtime)
    fake = [SimpleNamespace(provider_id="betfair"), SimpleNamespace(provider_id="matchbook")]
    monkeypatch.setattr(runtime, "build_market_data_adapters", lambda cfg, secrets: list(fake))

    db.update_venue_control("betfair", sim_feed_enabled=True, live_feed_enabled=False)
    db.update_venue_control("matchbook", sim_feed_enabled=False, live_feed_enabled=True)
    assert [x.provider_id for x in scanner._adapters("sim")] == ["betfair"]
    assert [x.provider_id for x in scanner._adapters("live")] == ["matchbook"]


def test_0918_provider_runtime_exposes_only_supported_normal_venues():
    runtime = default_provider_runtime_registry()
    assert [x.provider_id for x in runtime.providers.all()] == ["matchbook", "betfair", "smarkets"]
    assert runtime.profile("smarkets").api_state == "awaiting_api_access"


def test_0918_accounts_page_has_three_venues_and_no_fabricated_smarkets_money(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    sim = api.accounts_page({"mode": "sim", "period": "30D"})
    rows = {x["provider_id"]: x for x in sim["providers"]}
    assert set(rows) == {"betfair", "matchbook", "smarkets"}
    sm = rows["smarkets"]
    assert sm["integration_pending"] is True
    assert sm["balance"] is None
    assert sm["available"] is None
    assert sm["exposure"] is None
    assert sim["current"]["total_providers"] == 3

    live = api.accounts_page({"mode": "live", "period": "30D"})
    live_rows = {x["provider_id"]: x for x in live["providers"]}
    assert set(live_rows) == {"matchbook", "betfair", "smarkets"}
    assert live_rows["smarkets"]["integration_pending"] is True
    assert live_rows["smarkets"].get("available") is None


def test_0918_old_shared_venue_control_migrates_to_sim_and_live_starts_off(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    db = DB(path)
    db.conn.execute("DROP TABLE venue_controls")
    db.conn.execute(
        """CREATE TABLE venue_controls (
        provider_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, account_nickname TEXT NOT NULL,
        feed_enabled INTEGER NOT NULL, account_access_enabled INTEGER NOT NULL,
        sim_enabled INTEGER NOT NULL, live_execution_enabled INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    db.conn.execute(
        "INSERT INTO venue_controls VALUES(?,?,?,?,?,?,?,?,?)",
        ("betfair", "betfair", "Primary BF", 1, 1, 1, 0, "2026-08-13T00:00:00+00:00", "2026-08-13T00:00:00+00:00"),
    )
    db.conn.commit(); db.conn.close()

    upgraded = DB(path)
    row = upgraded.venue_control("betfair")
    assert row["account_nickname"] == "Primary BF"
    assert row["sim_feed_enabled"] is True
    assert row["live_feed_enabled"] is False
    assert row["sim_account_enabled"] is True
    assert row["live_account_enabled"] is True


def test_0918_archive_and_prune_settings_survive_account_schema_upgrade(tmp_path):
    path = tmp_path / "archive-upgrade.sqlite3"
    db = DB(path)
    cfg = dict(db.get_setting("config", {}) or {})
    cfg.update({
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "sentinel_0918": "keep",
    })
    db.set_setting("config", cfg)
    db.conn.close()
    upgraded = DB(path)
    after = upgraded.get_setting("config", {})
    assert after["matched_market_archive_enabled"] is True
    assert after["matched_market_archive_runtime_gate_required"] is True
    assert after["matched_market_archive_required_before_prune"] is False
    assert after["sentinel_0918"] == "keep"


def test_0918_dashboard_is_three_wide_and_latest_admin_boundary_is_preserved():
    html = (ROOT / "frontend" / "index.html").read_text()
    assert ".exchange-account-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in html
    assert "@media (max-width:1180px){.exchange-account-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}" in html
    assert "@media (max-width:760px){.exchange-account-grid{grid-template-columns:1fr}" in html
    assert "Connections & credentials" in html
    assert "Advanced account settings & SIM funding" in html
    assert "['Matched-market research',$('matched')]" not in html
    assert "SIM feed" in html and "LIVE feed" in html
    accounts = html[html.index('<section id="accounts"'):html.index('</section>', html.index('<section id="accounts"'))]
    assert 'type="checkbox"' not in accounts
    assert 'accountsConnectionHost0918' not in accounts
    assert 'accountsManagementHost0918' not in accounts
    admin_fn = html[html.index("function renderVenueControls0917"):html.index("async function setVenueControl0917")]
    assert "adminVenueControlGrid0932" in admin_fn
    assert "Open Account" not in admin_fn


def test_0918_no_active_legacy_mode_vocabulary_in_shipped_source():
    legacy = "".join(("sha", "dow"))
    assert not (ROOT / "arbscanner" / f"{legacy}.py").exists()
    files = list((ROOT / "arbscanner").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + [ROOT / "README.md", ROOT / "RELEASE_NOTES.md"]
    for path in files:
        text = path.read_text(errors="ignore").lower()
        assert legacy not in text, path
    html = (ROOT / "frontend" / "index.html").read_text().lower()
    # Standard CSS visual-depth properties are unrelated to an application mode.
    html = html.replace("box-" + legacy, "").replace("drop-" + legacy, "").replace("text-" + legacy, "")
    assert legacy not in html


def test_0918_frontend_has_no_single_shared_feed_control():
    html = (ROOT / "frontend" / "index.html").read_text()
    assert "'feed_enabled'" not in html
    assert "'sim_feed_enabled'" in html
    assert "'live_feed_enabled'" in html
    assert "setFeedEnabledExplicit" not in html
