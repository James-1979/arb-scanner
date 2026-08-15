from __future__ import annotations

from pathlib import Path

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _section(html: str, section_id: str) -> str:
    start = html.index(f'<section id="{section_id}"')
    end = html.index('</section>', start) + len('</section>')
    return html[start:end]


def test_0932_accounts_is_read_only_observation_surface():
    accounts = _section(HTML, "accounts")
    assert "Operational readiness" in accounts
    assert "Money now" in accounts
    assert "Venue status" in accounts
    assert "Account transactions" in accounts
    assert "accountsReadinessScanner" in accounts
    assert "accountsUtilisation" in accounts

    # No configuration controls or duplicate mode owner on Accounts.
    assert "accountsModeSelector0918" not in accounts
    assert "accountsManagementHost0918" not in accounts
    assert "accountsConnectionHost0918" not in accounts
    assert 'type="checkbox"' not in accounts
    assert "Edit nickname" not in accounts
    assert "Connection &amp; credentials" not in accounts
    assert "SIM Funding" not in accounts
    assert "Advanced account settings" not in accounts


def test_0932_admin_owns_provider_credentials_enablement_and_funding():
    settings = _section(HTML, "settings")
    assert "Advanced account settings & SIM funding" in settings
    assert "adminVenueControlGrid0932" in settings
    assert "simBudgetEditor" in settings
    assert "accountCurrency" in settings

    # Provider credential source is relocated into Admin by the IA bootstrap.
    ia = HTML[HTML.index("function prepareInformationArchitecture"):HTML.index("function showSettingsPane")]
    assert "['Connections & credentials',$('exchanges')]" in ia
    assert "accountsConnectionHost0918" not in ia
    assert "accountsManagementHost0918" not in ia

    controls = HTML[HTML.index("function renderVenueControls0917"):HTML.index("async function setVenueControl0917")]
    assert "adminVenueControlGrid0932" in controls
    assert "sim_feed_enabled" in controls
    assert "live_feed_enabled" in controls
    assert "sim_account_enabled" in controls
    assert "live_account_enabled" in controls
    assert "live_execution_enabled" in controls
    assert "Edit nickname" in controls
    assert "showTab('accounts')" not in controls


def test_0932_accounts_provider_cards_have_status_not_controls():
    card = HTML[HTML.index("function accountProviderCard092"):HTML.index("function readinessTone0932")]
    assert "Account" in card
    assert "SIM feed" in card and "LIVE feed" in card
    assert "Market" in card
    assert "Latency" in card
    assert "Update" in card
    assert "Capital" in card and "Available" in card and "Exposure" in card
    assert "type=\"checkbox\"" not in card
    assert "onchange=" not in card
    assert "editVenueNickname0917" not in card
    assert "openAccountConnection0918" not in card


def test_0932_accounts_backend_supplies_operational_status_and_is_mode_scoped(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    api = API(tmp_path / "accounts.sqlite3")

    sim = api.accounts_page({"mode": "sim", "period": "TODAY"})
    assert sim["ok"] is True
    assert sim["mode"] == "sim"
    assert sim["page_read_only"] is True
    assert sim["isolated_from_live"] is True
    assert isinstance(sim.get("operations"), dict)
    assert isinstance(sim["operations"].get("feeds"), list)
    assert {x["provider_id"] for x in sim["providers"]} == {"betfair", "matchbook", "smarkets"}


def test_0932_accounts_activity_has_only_observation_filters():
    accounts = _section(HTML, "accounts")
    assert 'data-account-period="TODAY"' in accounts
    assert 'data-account-period="7D"' in accounts
    assert 'data-account-period="30D"' in accounts
    assert 'data-account-period="ALL"' in accounts
    assert 'data-account-period="MTD"' not in accounts
    assert 'data-account-period="YTD"' not in accounts
    assert "<th>Type</th>" in accounts
    assert "<th>Mode</th>" not in accounts
    assert "<th>Balance</th>" in accounts
