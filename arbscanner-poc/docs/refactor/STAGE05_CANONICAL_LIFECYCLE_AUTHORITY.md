# Stage 05 — Canonical Lifecycle Authority

## Goal

Make SIM Position / Settlement / Result authority explicit at lifecycle write boundaries while preserving Stage 04 read purity. Reads may report drift; they never repair it.

## Canonical runtime settlement boundary

Runtime scanner settlement now calls `DB.settle_canonical_lifecycle(...)` exactly once after provider winner mapping succeeds.

That boundary uses one SQLite transaction for:

- Monitor position settlement (`monitor_positions`);
- SIM wallet principal/P&L release (`monitor_stream_wallets`);
- execution settlement state (`execution_runs`);
- scenario realised P&L (`scenario_runs.realized_pnl`);
- canonical result settlement (`settlements`);
- opportunity lifecycle state (`opportunities.status`).

A single `settled_at` timestamp is shared by Monitor and Result settlement when both exist. If Monitor settlement reconciliation fails, no Result settlement is written. If the Result phase raises after Monitor state has been changed, the transaction is rolled back and the Monitor/wallet changes are not committed.

## Compatibility helpers

`DB.settle_monitor_position(...)` and `DB.settle(...)` remain available because historical tests and maintenance tooling call them directly. They now accept private transaction controls used only by the canonical boundary. The runtime scanner no longer chains those two separately committed helpers.

This preserves compatibility without retaining the production partial-commit risk.

## Read-only integrity verification

`DB.lifecycle_authority_integrity(...)` performs bounded read-only checks for:

- orphan Result settlements;
- opportunities marked settled without a Result settlement;
- Result settlement rows whose opportunity is not settled;
- settled Monitor positions missing a Result settlement;
- Monitor/Result outcome disagreement;
- settled Monitor positions missing `settled_at`;
- orphan LIVE settlements;
- LIVE positions marked settled without a LIVE settlement.

The report returns drift counts and bounded samples. It does not insert, update, delete, backfill or sync anything.

The Stage 02 probe's pre-run authority integrity check is strengthened with the same core SIM lifecycle consistency checks so a benchmark snapshot with lifecycle drift is visible before comparison.

## Read-path rule

Dashboard, Sports, Racing, Results, Replay, Performance and account projections continue reading stored authority directly. Stage 05 introduces no lazy `ensure`, sync or backfill call into a projection.

**Reads report; lifecycle writes repair.**

## LIVE boundary

Stage 05 does not unlock LIVE execution. Existing LIVE actual-position/settlement tables remain isolated, and the central order-writing lock is unchanged.
