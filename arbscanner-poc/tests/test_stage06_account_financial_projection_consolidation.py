from __future__ import annotations

from arbscanner.financial_projection import (
    authoritative_account_totals,
    portfolio_streams,
    project_portfolio_financial_state,
)


def _sim_accounts():
    return {
        "betfair": {
            "provider_id": "betfair",
            "display_name": "Betfair",
            "mode": "sim",
            "currency": "GBP",
            "equity": 300.0,
            "available": 250.0,
            "reserved": 50.0,
            "allocations": [
                {"stream": "pre_match", "equity": 100.0, "available": 80.0, "reserved": 20.0},
                {"stream": "in_play", "equity": 120.0, "available": 100.0, "reserved": 20.0},
                {"stream": "racing", "equity": 80.0, "available": 70.0, "reserved": 10.0},
            ],
            "last_updated": "2026-08-15T12:00:00+00:00",
        },
        "matchbook": {
            "provider_id": "matchbook",
            "display_name": "Matchbook",
            "mode": "sim",
            "currency": "GBP",
            "equity": 200.0,
            "available": 170.0,
            "reserved": 30.0,
            "allocations": [
                {"stream": "pre_match", "equity": 80.0, "available": 70.0, "reserved": 10.0},
                {"stream": "in_play", "equity": 70.0, "available": 60.0, "reserved": 10.0},
                {"stream": "racing", "equity": 50.0, "available": 40.0, "reserved": 10.0},
            ],
            "last_updated": "2026-08-15T12:00:00+00:00",
        },
    }


def test_scope_streams_are_canonical():
    assert portfolio_streams("sports") == ["pre_match", "in_play"]
    assert portfolio_streams("racing") == ["racing"]
    assert portfolio_streams("unknown") == ["pre_match", "in_play", "racing"]


def test_sim_projector_uses_allocation_authority_for_sports_and_racing():
    sports = project_portfolio_financial_state(_sim_accounts(), mode="sim", scope="sports")
    assert sports["mode"] == "sim"
    assert sports["capital"] == 370.0
    assert sports["available"] == 310.0
    assert sports["capital_deployed"] == 60.0
    assert sports["exposure"] == 60.0
    assert sports["currency"] == "GBP"
    assert sports["reporting_venue_ids"] == ["betfair", "matchbook"]
    assert all({x["stream"] for x in row["allocations"]} <= {"pre_match", "in_play"} for row in sports["rows"])

    racing = project_portfolio_financial_state(_sim_accounts(), mode="sim", scope="racing")
    assert racing["capital"] == 130.0
    assert racing["available"] == 110.0
    assert racing["capital_deployed"] == 20.0


def test_live_scope_without_allocation_stays_unavailable_and_never_borrows_all_account_balance():
    accounts = {
        "betfair": {
            "provider_id": "betfair",
            "currency": "GBP",
            "balance": 500.0,
            "available": 475.0,
            "exposure": 25.0,
            "is_stale": False,
            "integration_pending": False,
            "source": "live_provider",
        }
    }
    all_scope = project_portfolio_financial_state(accounts, mode="live", scope="all")
    assert all_scope["capital"] == 500.0
    assert all_scope["available"] == 475.0
    assert all_scope["capital_deployed"] == 25.0
    assert all_scope["attribution_available"] is True

    sports = project_portfolio_financial_state(accounts, mode="live", scope="sports")
    assert sports["capital"] is None
    assert sports["available"] is None
    assert sports["capital_deployed"] is None
    assert sports["reporting_venues"] == 0
    assert sports["attribution_available"] is False


def test_live_stale_account_is_not_authoritative_even_when_values_exist():
    accounts = {
        "betfair": {
            "provider_id": "betfair", "currency": "GBP", "balance": 500.0,
            "available": 480.0, "exposure": 20.0, "is_stale": True,
            "integration_pending": False,
        }
    }
    result = project_portfolio_financial_state(accounts, mode="live", scope="all")
    assert result["capital"] is None
    assert result["available"] is None
    assert result["reporting_venues"] == 0
    assert result["rows"][0]["authoritative"] is False


def test_cross_currency_portfolio_total_is_unavailable_not_fabricated():
    accounts = _sim_accounts()
    accounts["matchbook"]["currency"] = "USD"
    result = project_portfolio_financial_state(accounts, mode="sim", scope="all")
    assert result["compatible_currency"] is False
    assert result["currency"] is None
    assert result["capital"] is None
    assert result["available"] is None
    assert result["capital_deployed"] is None


def test_authoritative_account_totals_preserve_zero_missing_and_currency_semantics():
    rows = [
        {
            "provider_id": "betfair", "mode": "sim", "sim_account_enabled": True,
            "currency": "GBP", "available": 0.0, "exposure": 0.0, "balance": 100.0,
        },
        {
            "provider_id": "matchbook", "mode": "sim", "sim_account_enabled": True,
            "currency": "GBP", "available": 50.0, "exposure": None, "balance": 100.0,
        },
    ]
    result = authoritative_account_totals(rows, ["available", "exposure", "balance"], mode="sim")
    assert result["available"] == 50.0
    assert result["exposure"] == 0.0
    assert result["balance"] == 200.0
    assert result["field_reporting_counts"]["exposure"] == 1
    assert result["reporting_venue_accounts"] == 1
    assert result["utilisation"] is None
    assert result["utilisation_reconciled"] is False


def test_live_account_totals_require_connected_fresh_enabled_provider():
    rows = [
        {"provider_id": "betfair", "connection_state": "connected", "is_stale": False,
         "integration_pending": False, "live_account_enabled": True, "currency": "GBP",
         "available": 90.0, "exposure": 10.0, "balance": 100.0},
        {"provider_id": "matchbook", "connection_state": "connected", "is_stale": True,
         "integration_pending": False, "live_account_enabled": True, "currency": "GBP",
         "available": 900.0, "exposure": 100.0, "balance": 1000.0},
    ]
    result = authoritative_account_totals(rows, ["available", "exposure", "balance"], mode="live")
    assert result["available"] == 90.0
    assert result["exposure"] == 10.0
    assert result["balance"] == 100.0
    assert result["reporting_venue_ids"] == ["betfair"]


def test_projection_module_is_pure_and_has_no_db_or_provider_runtime_dependency():
    import arbscanner.financial_projection as fp
    assert not hasattr(fp, "DB")
    assert not hasattr(fp, "LiveProviderRegistry")
