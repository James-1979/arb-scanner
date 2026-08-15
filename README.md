ArbScanner v1.0

ArbScanner is a desktop arbitrage-scanning and strategy-research platform for sports and racing markets.

Version 1.0 is the first production baseline after the PoC series. It consolidates the SIM/LIVE operating model, lifecycle authority, account and financial projections, market analytics ownership, Sports/Racing engine projections, API routing, performance hot paths and frontend mode integrity into a single validated release.

The v1.0 baseline preserves the core operating safety model:

SIM and LIVE economic state are isolated.
LIVE provider connectivity is read-only.
LIVE order writing remains centrally locked.
Shared provider market evidence may be reused where safe, but economic and lifecycle state never falls back across modes.
Scenarios is a global research workspace and is intentionally identical in SIM and LIVE.
UI route changes clear stale economic data before new asynchronous reads render.
Platform overview

ArbScanner provides:

Sports and Racing/Greyhounds market discovery and monitoring.
Strategy-engine lifecycle management.
SIM evaluation with isolated synthetic bankrolls and economics.
Read-only LIVE market, account and operational evidence.
Canonical Results, Replay and Performance projections.
Market Analysis and historical analytics.
Global Scenarios research and capital modelling.
Provider/account status and operational diagnostics.
Controlled archival and long-period historical storage.
Strategy engine platform

The canonical real strategy families are:

SPORTS_BASELINE_ARB
SPORTS_SUPERBET_ARB
GREYHOUNDS_BASELINE_ARB

Reference/research types remain:

SPORTS_DEPTH_ARB_REFERENCE
NOOP_TEST_ENGINE

Greyhounds is a first-class product/UI domain and is processed through GREYHOUNDS_BASELINE_ARB rather than a platform strategy special-case.

Engine grade remains independent from operational enablement and execution authority:

RESEARCH -> STANDARD -> ADVANCED -> EXTREME

Active economic modes are:

SIM
LIVE

Legacy third-mode lifecycle values are migrated one-way to SIM. SIM and LIVE engine enablement remain independently selectable.

Engine library and portable engines

Sports and Racing/Greyhounds Engines pages are installed-engine managers. Each engine has immutable identity/version/configuration provenance plus editable nickname, description and notes metadata.

Dense operational views use the nickname while retaining immutable IDs underneath. Monitor, Results and Replay expose Engine, Venue, Account and mode provenance and filters.

.arbengine files are portable ZIP-based engine packages. Import follows:

Quarantine -> Static Validate -> Review -> Explicit Install

Uploaded strategy source is not executed during validation, arbitrary dependencies are not installed, unsafe archive structures are rejected, and new engine instances install as RESEARCH + DISABLED until explicitly enabled.

Typical workflow:

develop/review engine
-> .arbengine
-> Quarantine
-> Validate
-> Review
-> Install DISABLED
-> configure
-> Scenarios
-> enable SIM
-> enable LIVE evaluation separately if authorised

SPORTS_SUPERBET_ARB remains a genuine strategy family but is not a global scanner mode or source of truth. Generic scanner/execution plumbing resolves scaled-entry behaviour through engine capabilities.

Venues and accounts

Accounts is the canonical read-only provider/account status and current-money surface. Admin owns credentials, provider/session setup, SIM funding and mode-specific controls.

Normal user-facing venue inventory is:

Betfair
Matchbook
Smarkets

Each venue has independent mode-specific controls:

SIM Feed              ON/OFF
SIM Account           ON/OFF
LIVE Feed             ON/OFF
LIVE Account Access   ON/OFF
LIVE Execution        ON/OFF request

Effective LIVE execution remains centrally locked in v1.0.

SIM Feed and LIVE Feed are separate evidence-consumption gates. A physical provider connection may be shared where safe, but eligibility and economic state remain mode-owned.

Feed OFF is non-destructive: it stops new evidence for that mode while preserving credentials, configuration, account metadata, Results, Replay and archived evidence.

Accounts displays provider/account readiness, authoritative current capital/available/exposure, venue status and account transactions without exposing configuration controls.

Dashboard is a display-only operational summary covering selected-mode health, venue economics, activity, latest settled results, daily performance, portfolio summaries and reconciled settled P&L context.

SIM and LIVE money are separate ledgers:

SIM bankroll/available/exposure are synthetic.
LIVE balance/available/exposure come only from real read-only venue evidence where supported.
There is no cross-mode balance fallback.

Smarkets remains staged as AWAITING API ACCESS. Provider identity and control state are present, but no Smarkets network adapter, fabricated balance or order-write path is enabled until API activation is available and separately validated.

Canonical lifecycle and projections

ArbScanner v1.0 uses explicit lifecycle ownership at write boundaries.

The canonical lifecycle is:

Processed
-> Opportunities
-> Qualified
-> Executed
-> Settled
-> Realised P&L

Settlement is handled atomically so Monitor position state, Results authority, opportunity state and wallet movement cannot be partially committed.

Read projections are read-only with respect to lifecycle and account authority. Integrity drift is reported rather than repaired during page load, refresh or polling.

This applies across Dashboard, Accounts, Performance, Market Analysis, Sports, Racing, Engines, Monitor, Results and Replay.

Monitor, Results and Replay

Monitor and Results expose Engine, Venue, Account and Mode attribution.

Results keeps SIM and LIVE cohorts independently filterable and does not use SIM funds or P&L as LIVE economics.

Replay retains the same provenance on recorded positions and supports Engine/Venue/Account/Mode filtering. The row-level Replay ledger is collapsible so the chronological timeline remains the primary review surface.

Market Analysis

Market Analysis separates shared provider evidence from mode-owned economic analytics.

Shared evidence may be reused where valid, but SIM financial/lifecycle state is never projected into LIVE. LIVE lifecycle/economic fields fail closed where authoritative LIVE evidence does not exist.

The v1.0 refactor also removes unnecessary opposite-mode operational reads from Market Analysis while preserving projection output.

Sports and Racing

Sports and Racing retain separate domain semantics while sharing bounded operator-projection contracts where appropriate.

Sports keeps its pre-match/in-play behaviour and strategy lifecycle.

Racing keeps its schedule, book and runner-specific logic. LIVE Racing execution remains hard locked.

Engine catalogue and lifecycle projections share common bounded projection logic without merging Sports and Racing economics.

Scenarios

Scenarios is the deliberate exception to SIM/LIVE presentation isolation.

It is a single global research workspace:

GLOBAL · RESEARCH

The same Scenario state, controls, assumptions, results, KPIs and charts are visible regardless of whether the application header is currently SIM or LIVE.

Switching SIM <-> LIVE while on Scenarios does not clear, recalculate or replace Scenario state.

Running a Scenario never changes operational settings, wallets, order state or lifecycle authority.

Mode integrity and UI behaviour

v1.0 treats mode/route integrity as a first-class UI requirement.

When moving between data-heavy routes or switching SIM/LIVE:

stale economic/data DOM is cleared synchronously;
old route/pane request tokens are invalidated;
responses from an old mode, route or pane cannot repaint the current view;
SIM lifecycle totals cannot remain visible in LIVE surfaces;
LIVE data cannot appear in SIM through fallback or delayed rendering.

This includes the field-verified fixes for Sports Monitor, Performance, Sports Overview and Racing Overview.

Performance

The v1.0 refactor removes redundant read work without introducing stateful UI caches.

Measured local benchmark improvements include:

cumulative reference-to-v1 query reduction while preserving output fingerprints;
removal of redundant SIM account read repair;
reduced Market Analysis operational reads;
major Racing Monitor query reduction by avoiding a full Racing Overview load.

Performance work is accepted only where projections remain output-equivalent and authority writes remain zero.

Install / upgrade

Unzip the release and run:

bash ./BUILD_AND_INSTALL.command --verify-only

The preflight should report:

Detected source version: 1.0
Preflight OK: source and frontend are ArbScanner v1.0.

Then install:

bash ./BUILD_AND_INSTALL.command

The installer replaces:

/Applications/ArbScanner.app

and, when already installed, restarts the background worker.

It does not replace operational state under:

~/Library/Application Support/ArbScanner/

That directory owns the SQLite database, settings, engine metadata/config/history, venue controls, runtime archive state, verified manifests, prune audit state and Parquet archive.

In-place upgrades therefore preserve operational state.

Data lifecycle

ArbScanner keeps current/high-resolution market state in SQLite and compact finalized hourly history for long-period analytics. Verified historical Parquet supplies detailed drill-down outside the hot SQLite window.

Fresh installations that do not enable the archive pilot retain the established 48-hour verbose matched-market lifecycle.

Destructive archive-gated pruning remains fail-closed and is not exposed as a normal Admin-page delete action.

Archive administration

Supported archive operator commands are consolidated into:

python3 scripts/archive_admin.py \
  --db "$HOME/Library/Application Support/ArbScanner/arbscanner.sqlite3" \
  status

Supported operations include:

runtime-gate
pilot
archive-hour
prune-plan
prune

Destructive prune controls require exact confirmation tokens shown by --help or the command response.

v1.0 safety boundary
SIM and LIVE economic/execution state remain isolated.
Active operational modes are SIM and LIVE only.
Legacy third-mode values migrate one-way to SIM.
Each engine has independent SIM and LIVE enablement.
SIM feed, LIVE feed, SIM account availability, LIVE account access and LIVE execution request are separate controls.
LIVE provider connectivity remains read-only.
LIVE order writes remain centrally disabled.
Engines are provider-blind and cannot bypass central validation/risk controls.
Imported engines never auto-activate or auto-promote to LIVE.
Read projections do not repair lifecycle or account authority.
Canonical settlement is atomic.
Scenario research is global but cannot modify operational state.
Stale route/mode responses cannot repaint the active UI.
Continuous archival remains gated by compatible runtime evidence.
Archive-gated destructive pruning remains fail-closed and OFF unless explicitly armed.
v1.0 validation baseline

The v1.0 release was promoted from the field-verified Stage 12.3 candidate without reopening runtime logic.

Release validation includes:

cumulative refactor and mode-integrity regression tests;
Reference-vs-Candidate projection comparison;
zero read-path authority writes;
architecture boundary validation;
JavaScript syntax validation;
browser regressions for SIM/LIVE stale rendering;
Sports Monitor SIM-to-LIVE lifecycle isolation;
global Scenarios SIM/LIVE preservation;
package and checksum integrity.

The v1.0 package is the new development baseline. Future work should branch from the frozen v1.0 release rather than from an earlier PoC build.

Release history

v1.0 supersedes the PoC 0.9.x release line as the primary baseline.

See RELEASE_NOTES.md for the consolidated historical release record.
::: ​​
