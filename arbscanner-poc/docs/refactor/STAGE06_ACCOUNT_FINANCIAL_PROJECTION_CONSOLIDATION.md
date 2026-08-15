# Stage 06 — Account and Financial Projection Consolidation

## Goal

Consolidate selected-mode account/portfolio financial arithmetic without merging SIM and LIVE authority or changing financial semantics.

## Shared pure projection boundary

`arbscanner.financial_projection` now owns three pure rules:

- `portfolio_streams(scope)` — canonical Sports/Racing allocation ownership;
- `project_portfolio_financial_state(...)` — current capital, available, deployed/exposure, utilisation, currency compatibility and allocation attribution;
- `authoritative_account_totals(...)` — Accounts-page venue totals with explicit zero-vs-missing and currency rules.

The projector does **not** import the DB or LIVE provider registry and cannot create wallets, refresh providers, backfill history or repair authority.

## Mode ownership remains explicit

The API still fetches authority through separate boundaries:

- SIM: `_monitor_account_state(...)` from the virtual allocation wallet ledger;
- LIVE: `_live_account_state_async(...)` from provider account state.

Only after that selected-mode state exists is it passed into the shared pure projector. No shared helper chooses or combines modes.

## Unavailable semantics

Stage 06 preserves the existing fail-closed rules:

- Sports/Racing LIVE capital stays unavailable when the provider account has no allocation provenance;
- stale or integration-pending LIVE values are not authoritative;
- missing values stay `None`, while genuine zero values remain reportable;
- unlike currencies are never summed into a fabricated total;
- SIM portfolio attribution comes only from stored allocation wallets, never from activity percentages or reconstructed P&L.

## Consumer effect

`portfolio_financial_state`, Sports Overview and Performance now use one shared current-state projector for both modes. Accounts continues its period/activity composition but its current venue totals use the same shared authoritative account aggregator rather than API-local arithmetic.

No response contract is intentionally changed. The Stage 05 → Stage 06 same-DB benchmark must therefore retain identical output fingerprints and no new read writes.
