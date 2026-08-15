from __future__ import annotations

from .venues import provider_id_for_name


def portfolio_streams(scope: str) -> list[str]:
    """Return the canonical allocation streams owned by a portfolio scope."""
    scope = str(scope or "all").lower()
    if scope == "sports":
        return ["pre_match", "in_play"]
    if scope == "racing":
        return ["racing"]
    return ["pre_match", "in_play", "racing"]


def project_portfolio_financial_state(
    accounts: dict,
    *,
    mode: str,
    scope: str = "all",
    venue: str = "all",
) -> dict:
    """Project current selected-mode portfolio finances from account authority.

    The function is deliberately pure: callers fetch SIM or LIVE authority first,
    then this projector applies the shared attribution/currency/unavailable rules.
    It never creates wallets/accounts, refreshes providers, or repairs authority.
    """
    mode = "live" if str(mode or "sim").lower() == "live" else "sim"
    scope = str(scope or "all").lower()
    if scope not in {"all", "sports", "racing"}:
        scope = "all"
    venue = str(venue or "all").strip().lower()
    streams = set(portfolio_streams(scope))
    rows = []

    for venue_id, account0 in sorted((accounts or {}).items()):
        account = dict(account0 or {})
        canonical_venue = (
            provider_id_for_name(str(account.get("provider_id") or account.get("venue_id") or venue_id))
            or str(venue_id).lower()
        )
        if venue not in {"", "all"} and canonical_venue != venue:
            continue

        allocations_raw = account.get("allocations")
        if mode == "live" and allocations_raw is None:
            allocations_raw = (account.get("metadata") or {}).get("allocations")
        allocations = list(allocations_raw or [])

        if scope == "all":
            if mode == "live":
                capital = account.get("equity") if account.get("equity") is not None else account.get("balance")
                available = account.get("available")
                deployed = account.get("exposure") if account.get("exposure") is not None else account.get("reserved")
                row_allocations = allocations
            else:
                capital = account.get("equity")
                available = account.get("available")
                deployed = account.get("reserved")
                row_allocations = allocations
        else:
            selected = [x for x in allocations if str(x.get("stream") or "") in streams]
            row_allocations = allocations if mode == "live" else selected
            if not selected:
                capital = available = deployed = None
            else:
                capital = sum(float(x.get("equity") or 0.0) for x in selected)
                available = sum(float(x.get("available") or 0.0) for x in selected)
                if mode == "live":
                    deployed = sum(
                        float(x.get("reserved") if x.get("reserved") is not None else x.get("exposure") or 0.0)
                        for x in selected
                    )
                else:
                    deployed = sum(float(x.get("reserved") or 0.0) for x in selected)

        authoritative = bool(capital is not None and available is not None and deployed is not None)
        if mode == "live":
            authoritative = bool(authoritative and not account.get("is_stale") and not account.get("integration_pending"))

        rows.append({
            "venue_id": canonical_venue,
            "provider_id": canonical_venue,
            "display_name": account.get("display_name") or canonical_venue.replace("_", " ").title(),
            "currency": account.get("currency"),
            "capital": None if capital is None else round(float(capital), 4),
            "available": None if available is None else round(float(available), 4),
            "capital_deployed": None if deployed is None else round(float(deployed), 4),
            "exposure": None if deployed is None else round(float(deployed), 4),
            "allocations": row_allocations,
            "authoritative": authoritative,
            "source": account.get("source") or ("live_provider" if mode == "live" else "virtual_ledger"),
            "last_updated": (
                account.get("last_updated") or account.get("captured_at")
                if mode == "live"
                else account.get("last_updated")
            ),
        })

    reporting = [x for x in rows if x.get("authoritative")]
    currencies = sorted({str(x.get("currency") or "").upper() for x in reporting if x.get("currency")})
    compatible = bool(reporting) and len(currencies) == 1
    if compatible:
        capital = round(sum(float(x["capital"]) for x in reporting), 4)
        available = round(sum(float(x["available"]) for x in reporting), 4)
        deployed = round(sum(float(x["capital_deployed"]) for x in reporting), 4)
    else:
        capital = available = deployed = None
    utilisation = None
    if capital is not None and deployed is not None and capital > 0:
        utilisation = round(100.0 * deployed / capital, 6)

    result = {
        "mode": mode,
        "scope": scope,
        "venue": venue or "all",
        "streams": sorted(streams),
        "currency": currencies[0] if compatible else None,
        "compatible_currency": compatible,
        "capital": capital,
        "available": available,
        "capital_deployed": deployed,
        "exposure": deployed,
        "utilisation_pct": utilisation,
        "reporting_venues": len(reporting),
        "reporting_venue_ids": [x["venue_id"] for x in reporting],
        "rows": rows,
    }
    if mode == "live":
        result["attribution_available"] = scope == "all" or bool(reporting)
    return result


def authoritative_account_totals(rows: list[dict], fields: list[str], *, mode: str) -> dict:
    """Aggregate authoritative selected-mode account fields without fabricating missing values."""
    mode = "live" if str(mode).lower() == "live" else "sim"
    if mode == "live":
        eligible = [
            x for x in rows
            if x.get("connection_state") == "connected"
            and not x.get("is_stale")
            and not x.get("integration_pending")
            and bool(x.get("live_account_enabled", True))
            and x.get("currency")
        ]
    else:
        eligible = [
            x for x in rows
            if str(x.get("mode") or "sim").lower() == "sim"
            and bool(x.get("sim_account_enabled", True))
            and not x.get("integration_pending")
            and x.get("currency")
        ]

    supported_ids = [str(x.get("provider_id") or x.get("exchange") or "").lower() for x in rows]
    field_reporting_counts = {}
    field_reporting_venues = {}
    field_currencies = {}
    result = {}
    for field in fields:
        reporting = [x for x in eligible if x.get(field) is not None]
        currencies = sorted({str(x.get("currency") or "").upper() for x in reporting if x.get("currency")})
        field_reporting_counts[field] = len(reporting)
        field_reporting_venues[field] = [str(x.get("provider_id") or x.get("exchange") or "").lower() for x in reporting]
        field_currencies[field] = currencies
        result[field] = (
            round(sum(float(x.get(field)) for x in reporting), 4)
            if reporting and len(currencies) == 1
            else None
        )

    full_reporting = [x for x in eligible if all(x.get(field) is not None for field in fields)]
    financial_reporters = [x for x in eligible if any(x.get(field) is not None for field in fields)]
    common_currencies = sorted({str(x.get("currency") or "").upper() for x in financial_reporters if x.get("currency")})
    compatible = len(common_currencies) <= 1
    if not compatible:
        for field in fields:
            result[field] = None
    capital_venues = set(field_reporting_venues.get("balance") or [])
    exposure_venues = set(field_reporting_venues.get("exposure") or [])
    capital = result.get("balance")
    exposure = result.get("exposure")
    utilisation = None
    utilisation_reconciled = bool(capital_venues) and capital_venues == exposure_venues
    if utilisation_reconciled and capital is not None and exposure is not None and float(capital) > 0:
        utilisation = round(100.0 * float(exposure) / float(capital), 6)
    result.update({
        "currency": common_currencies[0] if len(common_currencies) == 1 else None,
        "currencies": common_currencies,
        "compatible": compatible,
        "supported_venue_accounts": len(rows),
        "reporting_venue_accounts": len(full_reporting),
        "reporting_venue_ids": [str(x.get("provider_id") or x.get("exchange") or "").lower() for x in full_reporting],
        "field_reporting_counts": field_reporting_counts,
        "field_reporting_venues": field_reporting_venues,
        "field_currencies": field_currencies,
        "supported_venue_ids": supported_ids,
        "utilisation": utilisation,
        "utilisation_reconciled": utilisation_reconciled,
    })
    return result
