# ArbScanner Refactor Scope — Recovery Stage 01

Status: frozen scope reconstruction from the verified 0.9.57 reference. This document changes no runtime behaviour.

## Authoritative baseline

- Reference: ArbScanner 0.9.57 Dashboard Upgrade & Mode Integrity Closure.
- Reference ZIP SHA-256: `f192f583c020c0e387d5ea1a9860868b30ed26f6effcfb0367f44e3ea8c79389`.
- The unavailable corrected Stage 12 candidate from the prior chat is historical evidence only, not source code.

## Refactor objective

Reduce coupling, redundant routes/data paths and UI latency while preserving observable behaviour, financial semantics, mode ownership and the central LIVE order-write lock.

The refactor is successful only if the application becomes easier to reason about and faster to operate without introducing cross-mode state, silent data repair, changed settlement/account semantics, or hidden regressions.

## Non-negotiable invariants

1. **SIM/LIVE isolation**
   - SIM and LIVE lifecycle/economic state never fall back to each other.
   - A mode switch must not briefly render stale data from the previous mode.
   - Stale in-flight responses must be rejected before they can clear or overwrite the current mode's UI.

2. **Shared market evidence is explicit, not accidental**
   - Provider market/discovery/liquidity evidence may be shared where it is genuinely mode-independent.
   - Qualification, execution, positions, settlement, P&L, balances and other lifecycle/economic projections remain owned by their authoritative mode.
   - LIVE diagnostic decision evidence is not promoted to LIVE Qualified/Executed lifecycle state.

3. **Read purity / command-query separation**
   - UI read, refresh and poll projections must not mutate canonical lifecycle/account authority.
   - Repair, sync, backfill and migration occur only at explicit authoritative write/migration boundaries.
   - Integrity reads report drift; they do not silently fix it.

4. **Canonical lifecycle authority**
   - Position and settlement state has one explicit authoritative write path.
   - Any future canonical projection must be maintained when lifecycle writes occur, not lazily reconstructed during reads.

5. **LIVE safety**
   - Central LIVE order writing remains structurally locked throughout the refactor and validation.
   - Read-only account connectivity or provider health cannot unlock order submission.

6. **Financial semantic stability**
   - No refactor may alter stake, commission, realised P&L, settlement, capital, available, exposure, wallet allocation, utilisation or ROI semantics unless separately approved as a product change.
   - Missing/unavailable values remain unavailable rather than being invented or copied from another scope/mode.

7. **Performance is measured**
   - Route/query reductions must be benchmarked against the verified parent checkpoint.
   - Faster code is not accepted if it changes data ownership or financial results.

8. **No opportunistic UI redesign inside the architecture refactor**
   - Existing 0.9.57 UI is the behavioural presentation baseline unless a later stage explicitly identifies a required structural change.
   - Previously requested UI refinements are recorded below but are not silently mixed into architecture work.

## Product/UI requirements carried forward from the prior chat

These are preserved as product requirements/history, not automatically part of each architecture stage:

- Accounts is an observe/status surface: scanner/feed/account readiness, connection/freshness, financial totals and account transactions; configuration belongs in Admin.
- Performance financial timelines include capital/available/exposure/deployed context, playback/reveal behaviour, and clear current-state reconciliation.
- Replay running P&L positioning and timeline controls must remain usable; timeline/scrubber should support direct manipulation where implemented.
- Market Analysis filters are compact/right-aligned; heatmap data must remain correct and route-local failures must not blank unrelated analytics.
- Money Now uses total semantics.
- Engine views are operational surfaces: enablement/status/activity plus processed opportunities, executions and P&L; Sports/Racing engines should remain visually/semantically aligned.
- Choice/period/filter buttons use the common Today-style button treatment rather than competing segmented styles.
- Capital-over-time should distinguish total capital, available capital and capital in use so required strategy funding can be judged.
- Admin navigation/content ownership is tabbed/sectioned rather than duplicating configuration controls across operational pages.

## Explicitly out of scope unless separately approved

- Enabling real LIVE order placement.
- Changing strategy qualification thresholds or engine economics.
- Repricing, staking or settlement model changes.
- Destructive database reset/migration.
- Fabricating historical account state where snapshots do not exist.
- Treating LIVE provider decision diagnostics as economic lifecycle records.
- Racing/Sports product redesign beyond parity needed for shared components or route ownership.

## Known historical failure lesson

The prior unavailable Stage 12 candidate reportedly introduced lazy canonical lifecycle sync in read projections. Those reads performed INSERT/UPDATE work and created severe query/write regressions. The corrected candidate was reported to remove those side effects, but that source was never downloaded.

Therefore this recovery refactor will implement the *principle* independently from the verified 0.9.57 source and will not assume any unavailable code exists.
