# ArbScanner Refactor Safety Harness - Stage 02

Stage 02 adds validation tooling only. It does not change application runtime behaviour.

## Purpose

Every later refactor stage is measured against its user-verified parent using the same copied SQLite snapshot. The harness blocks output drift, authoritative read-path writes, material query-count regressions, and increased write activity before a candidate can be promoted.

## Projection manifest

`validation/refactor_projection_manifest.json` currently contains 23 representative interactive projections spanning Dashboard, Accounts, Sports, Racing, Performance, financial state, Market Analysis, heatmap, Replay, Scenarios and LIVE actual-only surfaces.

The comparator resolves the rolling seven-day time window once, then passes exactly the same arguments and anchor time to Reference and Candidate.

## Write classes

The SQL trace classifies read-path writes into four categories:

- `authority`: lifecycle, economic, account/configuration or execution authority. A Candidate authority write is always blocking.
- `audit`: diagnostic/history evidence such as `live_account_audit`.
- `derived`: compact market/analytics rollup maintenance.
- `other`: any write not yet classified. These remain visible and must be explained before later consolidation.

Stage 02 deliberately records existing 0.9.57 behaviour rather than changing it. Stage 04 is where read-time maintenance is reviewed and moved or justified.

## Baseline findings

On a current-schema local fixture with no exchange credentials:

- SIM Dashboard, Accounts, Sports, Racing, Performance and portfolio projections are read-only with respect to SQLite after API initialisation.
- LIVE account/status projections can append `live_account_audit` rows when credentials are unavailable. These are diagnostic writes, not account balance authority.
- SIM Market Analysis may populate `exchange_market_discovery_state` compatibility/derived state for historical hours. This can create a high write count on a fresh fixture. The harness classifies it as derived maintenance and preserves the exact baseline count for comparison.
- The selected projections produced no lifecycle/account/configuration authority writes in the Stage 02 baseline.

## Output comparison

Projection outputs are canonicalised and SHA-256 fingerprinted. Timestamps generated within 15 minutes of the common benchmark anchor, plus runtime age/latency counters, are normalised so Reference and Candidate are not failed merely because they run sequentially. Stored historical timestamps and economic values remain part of the fingerprint.

A Candidate is blocked when:

1. a projection errors;
2. an output fingerprint differs;
3. a read projection writes an authority table;
4. read-path SQL write statements increase versus Reference; or
5. query count exceeds Reference by more than `max(3, 10%)`.

Any smaller query increase is a warning and must be reviewed.

## Integrity observer

`refactor_probe.py` opens the supplied source DB in SQLite read-only mode and reports basic authoritative integrity conditions including orphan settlements/positions, invalid SIM/LIVE mode ownership and settled monitor rows missing settlement timestamps. The observer performs no repair or migration.

## LIVE lock and frontend ownership tests

`tests/test_stage02_refactor_safety_harness.py` freezes:

- LIVE operating/job/schedule activation remains rejected;
- no LIVE order-attempt row is created by those rejected actions;
- LIVE Results, Replay, Activity and Performance do not expose a deliberately seeded SIM-only lifecycle marker;
- the frontend retains mode-request tokens/current-context checks and LIVE Dashboard economic shell clearing;
- SQL write classification and read-only integrity observation work as intended;
- the same-DB comparator can compare identical builds successfully.

## Later-stage use

Use `scripts/compare_refactor_builds.py` after every structural stage. Stage 12 uses the same tool against the same copied deployed database, followed by the separate connected live-feed soak. The comparison tool is not a substitute for the connected soak.
