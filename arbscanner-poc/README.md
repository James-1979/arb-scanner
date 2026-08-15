# ArbScanner v1.0

ArbScanner v1.0 is the production-baseline promotion of the verified Stage 12.3 candidate. It preserves the validated 0.9.57 economic and operational contracts while incorporating the completed refactor, read-purity and lifecycle-authority work, SIM/LIVE route isolation, account/financial and market-analytics consolidation, Sports/Racing/Engine projection cleanup, API route consolidation, measured read-path optimisation, structural decomposition, stale-render closure, and the global Scenarios research exception.

The v1.0 promotion is release-identity only relative to Stage 12.3: no new strategy, provider, database, settlement, financial, or LIVE execution behaviour is introduced. LIVE order writing remains centrally locked.


## Strategy engine platform

0.9.38 is an operations/reliability release on top of the 0.9.37 UX closure and 0.9.36 canonical Sports lifecycle. A market-specific eligibility failure is now always local evidence state rather than global engine disablement, and stale `INSUFFICIENT_COMPATIBLE_VENUE_FEEDS` lifecycle state heals automatically on the next valid multi-venue observation. Sports Monitor now distinguishes Last Scan from authoritative Last Detected, uses one filter surface and neutral current-routing provenance. Sports Engines is a full-width lifecycle table with a right-side detail drawer and an explicit quarantine-first Add Engine review/install flow. Visible Refresh actions give immediate working/success/failure feedback while retaining current data. The canonical `Processed → Opportunities → Qualified → Executed → Settled → Realised P&L` lifecycle, Results settlement authority, global SIM/LIVE ownership, 0.9.37 selected-mode health semantics and central LIVE order-write lock are preserved.

The canonical real strategy families are:

```text
SPORTS_BASELINE_ARB
SPORTS_SUPERBET_ARB
GREYHOUNDS_BASELINE_ARB
```

Reference/research types remain `SPORTS_DEPTH_ARB_REFERENCE` and `NOOP_TEST_ENGINE`. Greyhounds remains a first-class product/UI domain and is processed through `GREYHOUNDS_BASELINE_ARB` rather than a platform strategy special-case.

Engine grade remains independent from operational enablement and execution authority:

```text
RESEARCH → STANDARD → ADVANCED → EXTREME
```

Active economic modes are only:

```text
SIM
LIVE
```

Legacy third-mode lifecycle values are migrated one-way to SIM. Active economic modes are SIM and LIVE only, their engine enablement is independently selectable, and LIVE order writes remain centrally locked.

## Engine library and portable engines

Sports and Racing/Greyhounds Engines pages are installed-engine managers. Each engine has immutable identity/version/configuration provenance plus editable `nickname`, description and notes metadata. Nickname edits do not create configuration versions.

Dense operational views use the nickname while retaining the immutable IDs underneath. Monitor, Results and Replay expose Engine, Venue, Account and SIM/LIVE provenance and filters. Replay reviews recorded provenance; strategy modelling/comparison remains in **Scenarios**.

`.arbengine` files are portable ZIP-based engine packages. Upload uses **Quarantine → Static Validate → Review → Explicit Install**. Uploaded strategy source is not executed during validation, arbitrary dependencies are not installed, unsafe archive structures are rejected, and new engine instances install **RESEARCH + DISABLED** until the operator enables evaluation. Reviewed local restricted Python engines may execute only after explicit installation confirmation; package checksum/version/install provenance is retained.

Typical workflow:

```text
develop/review engine -> .arbengine -> Quarantine -> Validate -> Review -> Install DISABLED -> configure -> Scenarios -> enable SIM -> enable LIVE evaluation separately if authorised
```

SuperBet remains a genuine strategy (`SPORTS_SUPERBET_ARB`) but is not a global scanner mode/source-of-truth. Generic scanner/execution plumbing resolves scaled-entry behaviour through engine capabilities.

## Venues and accounts

Accounts is the canonical read-only provider/account status and current-money surface. Admin owns provider credentials, feed/account enablement, connection setup and SIM funding. Normal user-facing venue inventory is:

```text
Betfair
Matchbook
Smarkets
```

Each venue has independent mode-specific controls:

```text
SIM Feed              ON/OFF
SIM Account           ON/OFF
LIVE Feed             ON/OFF
LIVE Account Access   ON/OFF
LIVE Execution        ON/OFF request; effective execution remains centrally locked
```

SIM Feed and LIVE Feed are separate evidence-consumption gates. When one provider transport can safely serve both modes, ArbScanner may share the physical connection while keeping eligibility independent. Feed OFF is non-destructive: it stops new evidence for that mode while preserving credentials, configuration, account metadata, Results, Replay and archived evidence.

Accounts displays provider/account readiness, authoritative current capital/available/exposure, venue status and account transactions without configuration controls. Admin owns provider credentials/session management, editable provider/account configuration, SIM funding and mode-specific feed/account enablement. Dashboard is a display-only operational summary: clocks and selected-mode health, Activity Monitor, venue economics, an isolated latest-settled-result ticker, daily performance KPIs, Sports/Greyhounds portfolio summaries, and reconciled settled P&L context.

SIM and LIVE money are separate ledgers. SIM bankroll/available/exposure are synthetic; LIVE balance/available/exposure come only from real read-only venue evidence where supported. There is no cross-mode balance fallback.

Smarkets is staged as **AWAITING API ACCESS**. Provider identity and control state are present, but no Smarkets network adapter, fabricated balance or order-write path is enabled until API activation is available and separately validated. Betfair and Matchbook operation is unaffected.

## Monitor, Results, Replay and Scenarios

Monitor and Results show Engine, Venue, Account and Mode attribution. Results keeps SIM and LIVE cohorts independently filterable and does not use SIM funds/P&L as LIVE economics.

Replay retains the same provenance on recorded positions and supports Engine/Venue/Account/Mode filtering. The row-level Replay ledger is collapsible so the chronological timeline remains the primary review surface.

Scenarios is the strategy-modelling workspace. Installed engines may be selected there against controlled historical evidence without changing operational SIM/LIVE enablement.

## Install / upgrade

Unzip the release and double-click `BUILD_AND_INSTALL.command`.

The installer replaces `/Applications/ArbScanner.app` and, when already installed, restarts the background worker. It does **not** replace operational state under:

```text
~/Library/Application Support/ArbScanner/
```

That directory owns the SQLite database, settings, engine metadata/config/history, venue controls, runtime archive state, verified manifests, prune audit state and Parquet archive. In-place upgrades therefore preserve a running archive pilot and its prune configuration.

## Data lifecycle

ArbScanner keeps current/high-resolution market state in SQLite and compact finalized hourly history for long-period analytics. Verified historical Parquet supplies detailed drill-down outside the hot SQLite window.

0.9.13 added the controlled archive-gated prune executor. The 0.9.12 dry-run planner remains the single eligibility authority. When the verified archive pilot is enabled but pruning is not armed, retention-expired hours are finalized but raw matched-market rows are retained. Destructive archive-gated pruning ships **OFF** and cannot be enabled from the Admin UI.

Fresh installations that do not enable the archive pilot retain the established 48-hour verbose matched-market lifecycle.

## Archive administration

All supported archive operator commands are consolidated into:

```bash
python3 scripts/archive_admin.py --db "$HOME/Library/Application Support/ArbScanner/arbscanner.sqlite3" status
```

Supported operations include `runtime-gate`, `pilot`, `archive-hour`, `prune-plan`, and `prune`. Destructive prune controls require exact confirmation tokens shown by `--help`/the command response; there is no Admin-page delete button.

## Safety boundary

- SIM and LIVE economic/execution state remain isolated.
- Active operational modes are SIM and LIVE only; legacy third-mode values migrate one-way to SIM.
- Each engine has independent SIM and LIVE enablement.
- SIM feed, LIVE feed, SIM account availability, LIVE account access and LIVE execution request are separate controls.
- LIVE provider account connectivity remains read-only and LIVE order writes remain structurally disabled.
- Engines are provider-blind and cannot bypass central validation/risk controls.
- Imported engines never auto-activate or auto-promote to LIVE.
- Continuous archival is gated by DuckDB availability and compatible runtime-gate evidence.
- Archive-gated pruning remains **OFF by default** in 0.9.29.
- While the archive pilot is ON and pruning is OFF, legacy raw-row deletion is paused rather than bypassing the archive gate.
- Every destructive prune reuses the fail-closed planner, deletes in bounded batches, records resumable audit evidence, verifies SQLite integrity and confirms the archived hour remains queryable.

## Release history

See `RELEASE_NOTES.md` for the complete consolidated release history.