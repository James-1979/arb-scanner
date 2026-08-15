# Stage 04 — Backend Command/Query Boundary and Read Purity

## Goal

Prevent UI reads from becoming hidden lifecycle/account repair commands. Stage 04 does not redesign canonical lifecycle authority; that is Stage 05. It makes the boundary explicit and removes the currently evidenced SIM-wallet repair calls from query paths.

## Boundary contract

`validation/refactor_operation_boundaries.json` classifies every public `API` method exactly once as one of:

- **query** — projection/read. It may report missing or drifted authority but must not create, backfill, sync, rebalance or settle authority state.
- **command** — explicit operator/configuration action. It may write only the authority needed for that requested action.
- **lifecycle_write** — scanner/discovery/settlement lifecycle boundary.
- **maintenance** — explicit reset/migration/archive/package/system maintenance; never implicit in a projection.
- **diagnostic** — observation/test/integrity operation. It may observe external systems but does not repair lifecycle/account authority unless separately invoked as a command.

The Stage 02 SQL tracer is strengthened so `monitor_wallets`, `monitor_stream_wallets` and `sim_account_adjustments` are authority tables. `live_account_snapshots` is classified as observational/audit state rather than account authority.

## Runtime correction

The following query paths no longer call `ensure_monitor_streams()`:

- `scenario_capital_sources()`
- `_monitor_account_state(..., capture=False)` and its query callers
- `sim_portfolio_budget_overview()`
- `dashboard_overview()`

Wallet initialization remains at `API.__init__` (startup/migration boundary), and `sim_account_adjust()` retains an explicit ensure because it is a funding command and must have target allocation wallets before applying the requested mutation.

If wallet authority is manually deleted or drifted after startup, a query now reads what exists and leaves the drift untouched. The query does not silently recreate the missing authority. Stage 05 can then report/resolve lifecycle authority through an explicit integrity/write boundary rather than a render side effect.

## Derived analytics exception

Stage 04 deliberately does **not** remove the existing bounded Market Analysis/heatmap compact-rollup maintenance. Those writes are classified as `derived`, are already visible in the Stage 02 harness, and exist to recover older historical evidence. Removing them safely requires equivalent raw/canonical fallback behaviour and belongs to the later analytics ownership/performance stages. They may not expand or become authority writes.

Similarly, LIVE account refresh/audit observation remains provider-observation state rather than trading/account authority; central LIVE order writing remains locked.

## Gates

Stage 04 adds tests that:

1. classify every public API method exactly once;
2. reject direct wallet `ensure_*` calls from query methods;
3. classify SIM wallet/funding tables as authority in the SQL harness;
4. delete wallet authority after startup and prove Dashboard, Scenario Capital, Budget Overview and private SIM account projection do not repair it;
5. prove the explicit `sim_account_adjust` command is still allowed to initialize missing target wallets;
6. rerun the Stage 02 23-projection parent/candidate gate and Stage 03 frontend safety tests.

This is the fail-closed rule carried into Stage 05: **reads report; writes repair.**
