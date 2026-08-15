from pathlib import Path

import pytest

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_0934_accounts_money_labels_are_explicit_totals():
    start = HTML.index('<section id="accounts"')
    end = HTML.index('</section>', start)
    accounts = HTML[start:end]
    for label in ("Total Capital", "Total Available", "Current Exposure", "Current Utilisation"):
        assert label in accounts
    assert 'id="accountsMoneyReportingContext"' in accounts
    assert "Current aggregated account money" in accounts


def test_0934_sim_totals_exclude_unavailable_but_include_genuine_zero():
    rows = [
        {
            "provider_id": "betfair", "mode": "sim", "sim_account_enabled": True,
            "currency": "GBP", "balance": 100.0, "available": 80.0, "exposure": 20.0,
        },
        {
            "provider_id": "matchbook", "mode": "sim", "sim_account_enabled": True,
            "currency": "GBP", "balance": 0.0, "available": 0.0, "exposure": 0.0,
        },
        {
            "provider_id": "smarkets", "mode": "sim", "sim_account_enabled": True,
            "currency": None, "balance": None, "available": None, "exposure": None,
            "integration_pending": True,
        },
    ]
    totals = API._compatible_sim_currency_totals(rows, ["available", "exposure", "balance"])
    assert totals["balance"] == pytest.approx(100.0)
    assert totals["available"] == pytest.approx(80.0)
    assert totals["exposure"] == pytest.approx(20.0)
    assert totals["reporting_venue_accounts"] == 2
    assert totals["supported_venue_accounts"] == 3
    assert totals["field_reporting_counts"] == {"available": 2, "exposure": 2, "balance": 2}
    assert totals["utilisation"] == pytest.approx(20.0)


def test_0934_field_authority_is_not_coerced_to_zero_and_utilisation_requires_reconciled_scope():
    rows = [
        {
            "provider_id": "betfair", "mode": "sim", "sim_account_enabled": True,
            "currency": "GBP", "balance": 100.0, "available": 80.0, "exposure": None,
        },
        {
            "provider_id": "matchbook", "mode": "sim", "sim_account_enabled": True,
            "currency": "GBP", "balance": 50.0, "available": 50.0, "exposure": 0.0,
        },
        {
            "provider_id": "smarkets", "mode": "sim", "sim_account_enabled": True,
            "currency": None, "balance": None, "available": None, "exposure": None,
            "integration_pending": True,
        },
    ]
    totals = API._compatible_sim_currency_totals(rows, ["available", "exposure", "balance"])
    assert totals["balance"] == pytest.approx(150.0)
    assert totals["available"] == pytest.approx(130.0)
    # Only Matchbook authoritatively reports exposure, and its genuine zero is retained.
    assert totals["exposure"] == pytest.approx(0.0)
    assert totals["field_reporting_counts"]["exposure"] == 1
    assert totals["reporting_venue_accounts"] == 1
    assert totals["utilisation"] is None
    assert totals["utilisation_reconciled"] is False


def test_0934_live_authority_requires_connected_fresh_provider_state():
    rows = [
        {
            "provider_id": "betfair", "connection_state": "connected", "is_stale": False,
            "currency": "GBP", "balance": 500.0, "available": 450.0, "exposure": 50.0,
        },
        {
            "provider_id": "matchbook", "connection_state": "connected", "is_stale": True,
            "currency": "GBP", "balance": 900.0, "available": 900.0, "exposure": 0.0,
        },
        {
            "provider_id": "disabled-live", "connection_state": "connected", "is_stale": False,
            "live_account_enabled": False, "currency": "GBP", "balance": 700.0, "available": 700.0, "exposure": 0.0,
        },
        {
            "provider_id": "smarkets", "connection_state": "awaiting_api_access", "is_stale": False,
            "currency": None, "balance": None, "available": None, "exposure": None,
            "integration_pending": True,
        },
    ]
    totals = API._compatible_currency_totals(rows, ["available", "exposure", "balance"])
    assert totals["balance"] == pytest.approx(500.0)
    assert totals["available"] == pytest.approx(450.0)
    assert totals["exposure"] == pytest.approx(50.0)
    assert totals["reporting_venue_accounts"] == 1
    assert totals["supported_venue_accounts"] == 4
    assert totals["utilisation"] == pytest.approx(10.0)


def test_0934_cross_currency_totals_are_not_fabricated():
    rows = [
        {"provider_id": "betfair", "mode": "sim", "sim_account_enabled": True, "currency": "GBP", "balance": 100.0, "available": 90.0, "exposure": 10.0},
        {"provider_id": "matchbook", "mode": "sim", "sim_account_enabled": True, "currency": "EUR", "balance": 100.0, "available": 100.0, "exposure": 0.0},
    ]
    totals = API._compatible_sim_currency_totals(rows, ["available", "exposure", "balance"])
    assert totals["compatible"] is False
    assert totals["balance"] is None
    assert totals["available"] is None
    assert totals["exposure"] is None
    assert totals["utilisation"] is None



def test_0934_unavailable_different_currency_account_does_not_contaminate_reporting_total():
    rows = [
        {"provider_id": "betfair", "mode": "sim", "sim_account_enabled": True, "currency": "GBP", "balance": 100.0, "available": 90.0, "exposure": 10.0},
        {"provider_id": "future-venue", "mode": "sim", "sim_account_enabled": True, "currency": "EUR", "balance": None, "available": None, "exposure": None},
    ]
    totals = API._compatible_sim_currency_totals(rows, ["available", "exposure", "balance"])
    assert totals["compatible"] is True
    assert totals["currency"] == "GBP"
    assert totals["balance"] == pytest.approx(100.0)
    assert totals["reporting_venue_accounts"] == 1
    assert totals["supported_venue_accounts"] == 2

def test_0934_loading_shell_never_uses_zero_or_unavailable_as_placeholder():
    body = HTML[HTML.index("function clearAccountsPageModeShell"):HTML.index("function primeFinancialShellForMode")]
    assert "accounts-money-loading0934" in body
    assert "Loading authoritative" in body
    assert "textContent='Loading'" in body
