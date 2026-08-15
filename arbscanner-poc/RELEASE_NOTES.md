# Release notes

## 1.0 — Verified Production Baseline

ArbScanner v1.0 promotes the verified Stage 12.3 candidate to the first production-baseline release. The release preserves the validated economic, lifecycle, provider and execution contracts while carrying forward the completed recovery refactor: command/query read purity, atomic canonical settlement ownership, account and financial projection consolidation, shared market-evidence ownership, Sports/Racing/Engine projection consolidation, API route pruning, measured data-load optimisation, structural decomposition, and frontend mode/route ownership.

The final field corrections are included: Sports Monitor can no longer render stale SIM lifecycle totals in LIVE, heavy data pages clear prior visible state synchronously before asynchronous reloads, and Scenarios is explicitly GLOBAL research state with identical controls/results across SIM and LIVE application contexts. LIVE order writing remains centrally locked.

This 1.0 promotion changes release identity and packaging metadata only relative to the verified Stage 12.3 source; it does not introduce a new DB migration, provider integration, strategy, settlement model, financial model or execution path.

## 0.9.57 — Dashboard Upgrade & Mode Integrity Closure

0.9.57 is a release-blocking Dashboard repair on top of 0.9.56. The 0.9.56 routing/settlement audit schema had been present in the full schema and historical migration path but was omitted from the mature-database current-schema markers. An existing 0.9.55 database could therefore be classified as current, skip the additive routing column/audit-table migration, and then fail `dashboard_overview()` when the new routing diagnostics were queried. Activity/operational status and Latest Result use separate routes, which is why those could continue updating while SIM account, capital, portfolio and trend economics remained blank.

0.9.57 adds a dedicated pre-current-schema additive migration for `opportunities.routing_diagnostics_json`, `settlement_audits` and its indexes, and includes those objects in the current-schema contract. This preserves the mature-database fast-open path without rerunning unrelated historical migrations. Routing diagnostics are also explicitly fail-soft inside Dashboard overview: diagnostic evidence can report unavailable, but it cannot suppress canonical account/financial state.

Dashboard mode ownership is tightened at the frontend boundary as well. A stale/in-flight LIVE Dashboard load now exits before clearing any economic state when SIM is selected, and the final LIVE clear/render owner checks the selected data mode before blanking values. This prevents LIVE empty-state labels from surviving or overwriting a SIM Dashboard during a mode-switch race. Existing selected-mode latency/RAG behaviour, wallet-drift diagnostics, settlement reconciliation, Scenarios layout and central LIVE order lock are unchanged. No database reset is required.

## 0.9.56 — Scenarios Layout & Exchange Routing Integrity

0.9.56 is a closure-only integrity pass on top of 0.9.55. Scenarios now consumes the full desktop workspace with larger controls, KPI cards and a flexible timeline/output area while retaining the one-page SIM-only console and its 24 Hours / 48 Hours / 7 Days horizons.

Arbitrage route selection remains guaranteed-profit-first, but economically equivalent books are no longer decided by provider enumeration order. Candidate quote order is canonicalised; routes within a tight profit/ROI tolerance enter a secondary wallet-health tie-break based on current available funds, existing reserved exposure and projected post-placement utilisation. Where wallet evidence is unavailable, a provider-neutral deterministic fallback is used. Routing diagnostics are persisted with selected leg distribution, favourite venue, economic-tie status, equivalent alternatives and final selection reason.

Settlement winner resolution now fails closed. Resolution proceeds from exact provider selection ID, canonical selection identity, exact normalised name, controlled alias, then an explicitly high-confidence fuzzy match with a separation margin. An unresolved winner produces `SETTLEMENT_MAPPING_ERROR`, remains financially unsettled and is persisted for investigation. Successful settlement stores mapping evidence and checks gross venue result, commission, net venue contribution and combined realised P&L before mutating SIM wallets; a material mismatch produces `SETTLEMENT_RECONCILIATION_ERROR`. Dashboard venue economics now use **Settlement Contribution Today** terminology and expose wallet share/drift plus aggregate routing diagnostics so normal settlement-driven migration can be distinguished from strategy loss or routing skew.

Dashboard operational status also receives a final render-ownership correction: selected-mode provider latency can fall back to measured price-stage timing/runtime health, scanner/discovery duration can be reconstructed from completed timestamps/stage timing, and a final status-strip commit writes the RAG/latency values after legacy render layers. SIM/LIVE Admin enablement ownership is unchanged. An additive schema migration adds per-opportunity routing diagnostics and the settlement-audit ledger; no database reset is required. No architecture refactor, provider integration expansion, or LIVE order-writing change is introduced.

## 0.9.55 — Dashboard Status Integrity Closure

0.9.55 is a closure-only Dashboard operational-status correction on top of 0.9.54. The Dashboard status strip now exposes explicit RAG health lights and visible latency/duration values instead of relying on generic state classes that could render `ready`/`degraded` as neutral grey. Provider health is selected-mode owned: SIM uses SIM feed/account enablement, LIVE uses LIVE feed/account enablement, disabled providers remain neutral grey, degraded/slow/stale providers are amber, hard errors/offline state are red, and ready providers are green. Provider latency is shown only when the selected mode expects that market feed; account-only providers show `N/A` rather than borrowing opposite-mode market latency.

The LIVE Dashboard now commits the LIVE-scoped operational payload back into the provider status strip as part of both account and activity refreshes, preventing stale SIM health from surviving a SIM→LIVE switch. Admin Venue Enablement updates carry the current data mode into the operational response so feed/account toggles immediately produce the correct selected-mode readiness. Price Scan and Discovery use RAG state plus their last completed cycle duration. Sports Monitor is no longer hard-coded `ACTIVE`: it is derived from Pre-match/In-play enablement, scanner worker state, selected-mode feed readiness and the existing Admin feed controls; it shows disabled/partial/degraded/offline/active state and worst selected-feed latency. LIVE order writing remains centrally locked.

No architecture refactor, database migration/reset, provider integration expansion, or economic-model change is included.

## 0.9.54 — Scenarios Console Closure

0.9.54 is a closure-only redesign of the Scenarios operator surface on top of 0.9.53. Scenarios now fits into one deterministic SIM console rather than layering Replay, Performance and Engine-analysis panels together. The top configuration surface owns the historical start date, exact **24 Hours / 48 Hours / 7 Days** forward output horizon, multi-select Sports, Stream, Venue and installed Engine filters, Evidence Quality, and scenario-local Starting Balance, Hedge Reserve, Minimum Profit, Maximum Stake and Minimum ROI assumptions. None of those scenario assumptions are persisted into Admin or Monitor configuration.

The replay route now accepts the selected sport, stream, venue and Engine sets end-to-end. Starting Balance is distributed across selected SIM venues in the current venue-equity proportions so the existing venue-aware paper wallet model remains authoritative. Maximum Stake is enforced inside the replay economics by scaling the equal-return stake plan before wallet fitting; Hedge Reserve can be overridden for the scenario without changing stored configuration. Unattributed historical positions remain available through **All engines** but are never manufactured into a pseudo-engine.

Scenario output is deliberately economic: **Ending Balance, Net P&L, ROI, Executed Positions, Max Capital Exposure, Max Drawdown, Capital Deployed and Peak Balance** surround a compact balance timeline with a capital-exposure band and direct scrub cursor. **Max Capital Exposure** is a primary KPI. LIVE continues to hide the SIM Scenarios economic surface and cannot invoke the SIM replay route; the hidden render boundary is enforced in both JavaScript and layout CSS. No architecture refactor, DB migration/reset, provider integration expansion, or LIVE order-writing change is included; central LIVE order writing remains locked.

## 0.9.53 — Replay & Market Analysis Route Integrity

0.9.53 is a closure-only release-blocking correction on top of 0.9.52. Replay no longer creates or exposes `Legacy / Unverified` as an Engine: only positions with authoritative `runtime_origin` / `execution_origin` provenance and a real `engine_instance_id` populate Engine tiles and filters. Historical unattributed positions remain visible in All-engines Replay totals and the ledger, where Engine is shown as unavailable rather than being assigned a fabricated identity. Replay Engine filtering is also corrected to route by `engine_instance_id` instead of comparing an Engine ID against a nickname.

Market Analysis restores the default All-stream state to the established unfiltered route, avoiding a new stream-filter dependency when no filtering is requested. Partial Pre-match / In-play / Racing combinations are transported as an explicit stream filter and are now applied consistently to the main Market Analysis rows, reasons, activity, execution, discovery/comparator and liquidity-period paths as well as the weekly heatmap. The heatmap also preserves the selected Portfolio scope instead of forcing `all`. Stream changes refresh the complete Market Analysis surface so KPI/leaderboard and heatmap ownership cannot diverge. Route failures remain local to the heatmap rather than silently presenting an empty grid.

No architecture refactor, DB migration/reset, provider integration expansion, or LIVE order-writing change is included. LIVE order writing remains centrally locked.

## 0.9.52 — Market Analysis Multi-Stream Closure

0.9.52 is a narrow closure correction on top of 0.9.51. Market Analysis weekly heatmap Stream controls now include an explicit **All** button and support true multi-selection across **Pre-match**, **In-play**, and **Racing**. All starts with all three selected; individual streams can be toggled independently; the last remaining stream cannot be deselected; and All restores the complete set. The frontend sends the selected stream set explicitly to the SIM/LIVE heatmap route instead of collapsing combinations into a legacy single scope/phase pair. Backend heatmap filtering classifies Racing independently from Sports phase, while LIVE diagnostic qualification remains fail-closed whenever a partial Sports phase selection cannot be represented precisely by the existing LIVE decision rollup. No architecture refactor, DB reset, provider integration, or LIVE order-writing change is included.

## 0.9.51 — Scenarios Routing & Engine Integrity Closure

0.9.51 is a closure-only technical pass on Scenarios. It corrects Yesterday/This week/This month period routing, preserves Racing as its own replay stream and applies Racing execution assumptions, adds Engine-provenance cohort filtering, scopes the actual-performance comparator to the selected sport/stream/engine, and removes the duplicate late Scenario loader/calendar overrides that could throw after successful responses. Scenario capital and engine modelling are now explicitly SIM-only at the render boundary; LIVE hides the complete SIM Scenario surface. The transaction ledger exposes Engine provenance and can hand an opportunity directly to the installed Engine comparison surface. No architecture refactor or LIVE order-writing change is included.

# ArbScanner Release Notes

## 0.9.50 — Analytics & Admin Integrity Closure

0.9.50 is a closure-only correction pass on top of 0.9.49. It does not begin the post-UI architecture refactor.

- Performance and Replay align their visible filter-button groups to the right on desktop; responsive layouts return them to the left on narrow screens.
- Performance Capital Exposure changes from purple to the common analytics blue without changing the underlying exposure series or inspector semantics.
- Market Analysis receives a full weekly-route integrity pass. All 16 selectable heatmap metrics are explicitly sourced from activity, authoritative SIM financial, liquidity-depth or liquidity-opportunity streams; sport/stream filters are applied consistently; explicit page refresh invalidates the bounded week cache; and route diagnostics expose source row counts for verification.
- LIVE Market Analysis now routes Refresh/period/portfolio reloads through the same heatmap-owned Pre-match / In-play / Racing loader used by direct heatmap interaction. This removes the remaining legacy path that could silently reset the LIVE heatmap to hidden all/all filters. Actual LIVE lifecycle/economic values remain fail-closed and never fall back to SIM.
- Admin Accounts adds a Smarkets readiness card when no Smarkets account API payload exists. It shows no fabricated balance and clearly remains `AWAITING API`, while the provider/account grids use the full workspace with three aligned venue columns on desktop.
- Admin scanner, account, alert and technical controls are rehydrated from effective configuration after the dynamic Admin layout is built, preventing valid persisted/default values from appearing as blank controls.
- Storage & Maintenance uses a cleaner two-column workspace with wide diagnostic/status surfaces and wrapped action rows.
- No provider integration expansion, scanner/economic redesign, DB migration/reset or architecture refactor is introduced.

LIVE order writing remains centrally locked.

## 0.9.49 — Analytics Layout & Admin Tabs Closure

0.9.49 is a closure-only correction pass on top of 0.9.48. It does not begin the post-UI architecture refactor.

- Performance removes the redundant All Venues / Betfair / Matchbook / Smarkets quick-selector from Capital Exposure. The hidden compatibility venue control is normalised to `all`, keeping the page a portfolio-level financial view.
- Market Analysis heatmap financial cells now read the bounded authoritative SIM opportunity/position ledger on every request. Discovery/activity and liquidity still use compact hourly rollups, but settled P&L / deployed / executed / qualified data can no longer remain stale because an immutable historical financial rollup was built before later reconciliation.
- Replay removes the duplicate right-side Running P&L console. The timeline takes the full available width at a corrected 190px desktop height, while Replay Time, Start/Pause and 0.5x/1x/2x/5x/10x controls move into a compact strip in the timeline header. Cursor-owned KPI, Engine and Sport tiles remain unchanged.
- Admin exposes System & Safety, Providers & Connections, Accounts & Funding, Market Data & Scanner, Alerts, Storage & Maintenance and Technical Settings as top navigation buttons. The existing responsibility-owned content stays in its relevant section and only the active section is displayed.
- No provider integration expansion, scanner/economic redesign or architecture refactor is introduced.

No DB migration/reset is required. LIVE order writing remains centrally locked.

## 0.9.48 — Admin & Analytics Closure

- Rebuilt Admin as tiled responsibility-owned sections: System & Safety, Providers & Connections, Accounts & Funding, Market Data & Scanner, Alerts, Storage & Maintenance, and Technical Settings.
- Admin commands are scoped: connection tests and notification tests no longer call the broad settings save path; provider/account/scanner/alert/technical save actions persist only their owned configuration. Admin no longer triggers the SIM Dashboard loader on entry.
- `get_state()` now returns the effective configuration (defaults merged with persisted values), allowing previously missing operator-relevant technical settings to display authoritatively.
- Performance visible dropdown filters are replaced by the common button grammar; the capital chart is now Capital Exposure only, with direct visible draggable playhead interaction retained across both timeline charts.
- Market Analysis visible top filters are Period + Portfolio buttons only; venue summary/search/type/sport filter surfaces are removed from the operator view. Weekly heatmap now owns Sport icon buttons and Pre-match/In-play/Racing stream buttons, and the heatmap renderer is explicitly hydrated from the selected mode/stream payload so populated cells cannot be lost between payload and render state.
- Replay visible filters are Period + Stream buttons only. The old stream summary row is removed; Engines in this period and Sports in this period both update at the replay cursor. Headline Replay KPI tiles now change as the cursor moves. The timeline exposes a visible grab handle and direct click/drag scrubbing.
- Sports Overview freshness now falls back to the authoritative completed-scan timestamp instead of displaying an unexplained dash when `last_age_seconds` is absent.
- LIVE order writing remains centrally locked; no provider integration, settlement authority or DB migration/reset is added.

## 0.9.47 — Timeline Interaction & Heatmap Integrity

0.9.47 builds on 0.9.46 as the final pre-refactor analytics interaction and correctness pass. Performance now presents total funding versus available capital on one scale, with the gap explicitly representing capital currently in use and peak capital-in-use labelled for funding-capacity planning. Both Performance charts and the Replay timeline can be clicked or dragged to scrub time directly. Replay playback speed is deterministic at 0.5x, 1x, 2x, 5x and 10x and can be changed during playback without resetting the cursor. Market Analysis now exposes all useful weekly heatmap datasets (including unique markets, net-positive evidence, settled positions/capital, top-book depth and liquidity rejections), distinguishes unobserved slots from real zeroes, and explicitly routes shared evidence versus SIM/LIVE lifecycle economics. SIM financial heatmap SQL is fail-closed to SIM; LIVE decision evidence remains diagnostic and cannot populate canonical LIVE Qualified/Executed/P&L. LIVE order writing remains centrally locked. No DB migration/reset is introduced.

## 0.9.46 — UI Control Consistency & Racing Engine Parity

0.9.46 builds on 0.9.45 as a final pre-refactor UI consistency pass. Choice/filter controls now share one reusable outlined-button component using the established Today-button visual grammar instead of mixed grey segmented troughs. Sports and Racing Monitor rows render EXECUTED status in green. Racing Engines now shares the Sports Engines page grammar: matching five lifecycle KPIs, safety notice, full-width lifecycle table, period/search/refresh controls, enablement treatment and solid right-side drawer structure, while Racing-specific content and guardrails remain Racing-native. No provider, scanner, engine-economics, settlement, SIM/LIVE or database semantics are changed.

### Verification focus

- Choice-button presentation is consistent across Dashboard scope, Accounts period controls, Active Positions filters, Monitor, Results, Engine periods, Execution Analysis action filters, venue quick filters and playback-speed controls.
- EXECUTED Monitor status is green in Sports and Racing rows.
- Racing Engines no longer applies the legacy grid `.engine-row0914` class to table rows, preventing table-layout divergence from Sports Engines.
- Racing Engine drawer remains fully opaque and uses the same action/nav/detail grammar as Sports.
- Central LIVE order-write lock and existing SIM/LIVE data isolation remain unchanged.

This is the single release-history document for ArbScanner. New releases append a section here; per-build README or `RELEASE_<version>.md` files are not created.

## 0.9.45 — SIM Market Analysis Data Recovery

0.9.45 builds on 0.9.44 and fixes a mature-database read-path defect that could leave Market Analysis empty in SIM even while current matched-market evidence still existed.

- Market Analysis no longer treats `matched_market_history_state` by itself as proof that every compact summary structure for an hour is present. A finalized marker with a missing compact market group now falls back to the still-present raw matched-market evidence for that exact group.
- Normal compact-history semantics are preserved: finalized compact groups remain authoritative, while legacy/unfinalized hours continue to read raw history. The recovery path therefore does not double-count a healthy compact + raw hour or promote short-lived legacy rollups prematurely.
- Unique-market identity and rejection-reason reads use the same per-structure recovery rule, so a partial compact-history inconsistency cannot erase the leaderboard while another compact table remains healthy.
- Liquidity opportunity rollups now validate that compact rows actually exist as well as their rollup-state marker. If the state marker is orphaned while hot raw evidence is available, the missing compact liquidity rows are rebuilt and Market Analysis does not return a false zero positive/liquidity funnel.
- SIM Market Analysis read timeout is increased from 10 to a bounded 20 seconds for large operational databases. A timeout/backend failure now produces a clear `Market Analysis could not load` retry/error state instead of silently resembling a legitimate empty period.
- The fix is read/recovery focused and introduces no DB migration or reset. Archive/finalisation ownership remains intact, SIM/LIVE remain strictly isolated, and LIVE order writing remains centrally locked.

## 0.9.44 — Active Positions LIVE State Integrity

0.9.44 builds on 0.9.43 and fixes the remaining Active Positions mode-consistency defect found during LIVE operator review.

- Active Positions now has one selected-mode payload owner for rows, filter counts, active-position count, committed capital, locked P&L, locked return, balanced-capital context and the locked-profit badge.
- Entering LIVE primes the entire Active Positions shell immediately, before the asynchronous actual-LIVE activity read returns. Stale SIM headline economics therefore cannot remain visible while the LIVE table is empty.
- LIVE Refresh uses the same atomic payload path; all headline metrics and row/filter state are committed together only when the request token still matches LIVE + Active Positions.
- The current LIVE execution contract remains actual-only and empty while order writing is locked. Empty LIVE state renders `0` positions, `£0.00` committed capital, unavailable locked P&L/return, zero filter counts and no locked-profit badge.
- Active Positions LIVE copy no longer describes positions as simulated Monitor fills/hedges. SIM retains the existing Monitor/simulated wording.
- SIM Dashboard responses are normalised through the same Active Positions payload helper after their existing render, keeping counts, rows and economics reconciled from one response.

No DB migration/reset is required. LIVE order writing remains centrally locked.

## 0.9.43 — LIVE Semantic Integrity & Racing Dashboard Repair

0.9.43 builds on 0.9.42 and fixes the remaining LIVE lifecycle/status inconsistencies found during operator review.

- Dashboard Activity Monitor no longer promotes simulated LIVE decision evidence into canonical `Qualified`. Shared Discovered/Matched/Processed remain market/runtime facts; LIVE Opportunities remain isolated LIVE decision evidence; Qualified/Executed now come only from authoritative LIVE lifecycle/execution state. Decision-qualified evidence is retained as a clearly separate diagnostic count.
- `live_execution_activity` exposes an explicit authoritative `qualified: 0` contract while LIVE order/execution persistence remains unavailable and centrally locked.
- Engine lifecycle responses in LIVE preserve simulated engine decision-boundary counts as `decision_qualified_evidence` but force operator-facing `qualified` to zero, preventing Engines and Monitor from reintroducing simulated qualification through a second path.
- Sports Monitor LIVE decision rows are classified as Positive/Rejected decision evidence rather than Qualified lifecycle records.
- Dashboard Racing summary is now selected-mode aware and can no longer call the SIM Racing endpoint while the global context is LIVE. Stale Racing values are cleared on LIVE entry.
- LIVE Racing uses shared provider-derived discovery only for current/future race schedule and matching facts. `Matched` and `Next Off` exclude past/closed races, so a stale discovery snapshot cannot leave an already-off race current.
- LIVE Sports and Racing Qualified remain zero. Simulated decision-qualified evidence is retained separately for diagnostics and is never promoted into Market/Race Highlights; without authoritative LIVE lifecycle qualification/positions, those LIVE highlight rails stay empty. Frontend renderers also fail closed in LIVE even if an unexpected decision-evidence highlight row is returned.
- LIVE `pipeline_analytics` and Market Analysis heatmap cells now keep simulated qualification only as `decision_qualified_evidence`; canonical Qualified/Executed are zero until authoritative LIVE lifecycle/execution records exist.
- Racing Monitor LIVE decision rows can no longer enter the Qualified filter through legacy `matched`/decision status aliases. They remain explicit Positive/Rejected decision evidence only.
- Runtime/state operational summaries are scoped to the selected data mode, preventing a generic periodic state refresh from briefly repainting a LIVE route with SIM lifecycle semantics.
- Dashboard Racing refreshes every 10 seconds while LIVE Dashboard is active, and Racing Overview/Monitor also fail closed on invalid or already-off `Next Off` values.
- Dashboard provider health now distinguishes account readiness from market-feed readiness. An account-only LIVE provider renders `ACCOUNT READY · LIVE account READY · market feed not expected` rather than the contradictory `READY · LIVE feed OFF · Market ERROR`.

No DB migration/reset is required. LIVE order writing remains centrally locked.

## 0.9.42 — Deep LIVE Isolation & UI Consistency

0.9.42 builds on 0.9.41 and closes the LIVE-state and cross-page consistency defects found during operator review without reopening the canonical lifecycle architecture.

- Active Positions Refresh is mode-aware; switching/refreshing in LIVE clears SIM positions immediately and reads only the actual LIVE execution activity endpoint.
- Empty LIVE Performance timelines clear the fixed Financial Inspector so previous SIM capital/P&L values cannot remain beside a LIVE empty chart.
- LIVE Market Analysis keeps provider-derived decision evidence as an explicit diagnostic field while operator-facing Qualified / Attempted / Executed / Settled remain zero until authoritative LIVE lifecycle records exist.
- LIVE Sports/Racing Overview market highlights require meaningful current provider evidence; weak/empty rows are not promoted into highlight cards and simulated LIVE evidence never increments operator Qualified.
- Replay period activity is engine-first. Authoritative originating engines get period tiles, position/win/loss/P&L totals and timeline highlighting; legacy rows remain `Legacy / Unverified`. MONITOR_SETTLED execution state is recognised for Replay engine P&L even when a legacy execution row has no explicit `settled_at` timestamp.
- Sports and Racing Engines remove top dropdown filters, use Today / Yesterday / 7 Days / 30 Days buttons, include Stream as a list column, and retain solid mirrored right-side detail drawers.
- Sports Monitor and Results expose one visible filter surface only; legacy backing controls remain hidden for compatibility. Sport toggles are generated only from sports represented in the current Monitor/Results scope and remain on one horizontal rail.
- Racing Monitor keeps Engine / Status / Venue / Search on one horizontal desktop line.
- Selected toggle styling is positive/neutral rather than inverted, and LIVE scan feedback calls simulated qualification `decision-qualified evidence` to avoid confusing it with the authoritative lifecycle stage.

No destructive DB migration or reset is required. LIVE order writing remains centrally locked.

## 0.9.41 — Racing Alignment & Operator Consistency

0.9.41 builds on 0.9.40 and applies the established operator architecture to Racing while closing the remaining held UI consistency issues.

- Racing Overview is now a concise current-operations page with Racing-only capital, availability, deployed capital, active positions, Today P&L, scanner/discovery/matching/freshness state, Race Highlights and current positions. LIVE remains actual-only with no SIM fallback.
- Racing Monitor now uses one Racing-native filter surface (Engine / Status / Venue / Search), explicit Last Detected, a Processed → Opportunities → Qualified → Executed funnel, and detailed race/matching evidence on demand.
- Racing Engines now follows the full-width lifecycle/results pattern with five summary tiles, a solid right-side drawer, Export Engine, Add Engine quarantine/review flow, mode-specific enablement and Results-derived settlement/P&L authority.
- Racing Results continues to use the shared authoritative Results ledger but removes Sports-only Stream/sport controls in Racing context; Period / Engine / Result / Search remain the primary operator controls.
- Racing Config is now a single-save Racing operating-envelope page. Money/funding/starting-balance/reset controls are removed; Greyhound/pre-race enablement, qualification/matching/risk guardrails and provider-registry SIM execution context remain. Engines may be stricter than the Racing envelope, never looser.
- Sports Monitor and Results use one visible filter system each. Monitor uses Stream / Engine / Status / Search plus multi-select sport buttons; Results uses Today / Yesterday / 7 Days / 30 Days plus Stream / Engine / Result / Search and the same sport-toggle grammar.
- Sports Overview Market Highlights use the shared supported-sport icon mapping. Replay retains speed buttons only (0.5x / 1x / 2x / 5x / 10x) with no visible speed dropdown.
- Performance Capital over time uses operator labels `Total Capital`, `Available to Deploy` and `In Open Positions`; deployed-capital rendering uses a neutral analytical violet rather than RAG health/P&L colours.
- Smarkets in SIM is shown as a neutral supported-but-not-reporting provider when API integration is not expected; unavailable values stay `—` and remain excluded from readiness/financial totals.
- Multi-sport engine lifecycle aggregation was corrected so the first selected sport is not double-counted. Racing config health no longer depends on removed legacy starting-balance settings.

No destructive DB migration or reset is required. LIVE order writing remains centrally locked.

## 0.9.40 — Analytics, Monitor & Engine UI Consolidation

0.9.40 closes the next held operator UI list on top of 0.9.39 without changing the canonical Sports lifecycle or LIVE order-write safety boundary.

- Market Analysis now renders Betfair, Matchbook and Smarkets as three equal provider panels with selected-mode SIM/LIVE health semantics; not-expected Smarkets remains neutral and missing LIVE state never falls back to SIM.
- Performance renames the upper financial chart to `Capital over time` with `Total Capital`, `Available Capital` and `Capital Deployed`, adds an All/Betfair/Matchbook/Smarkets quick selector bound to the existing Venue filter, and uses clean GBP axis labels.
- SIM Capital-over-time availability is recalculated at authoritative open/release events from the canonical identity `Available = Capital - Capital Deployed`, producing the expected cash dip/release profile without interpolating unknown financial values.
- Performance and Replay playback speed use direct `0.5x / 1x / 2x / 5x / 10x` buttons instead of a dropdown; speed changes apply immediately without resetting playback.
- Sports Overview uses tighter desktop spacing and limits the current-position preview to four rows so the operational summary fits the page more naturally.
- Sports Engines metadata fields are aligned, the drawer retains its solid treatment and top-level `Export Engine`, and Sports stream guardrail ownership is stated explicitly.
- Monitor and Results no longer use the legacy filter accordion over their lifecycle controls; each exposes one visible canonical filter surface.
- Sports Monitor adds a first-column `Detected` timestamp for each row while retaining the separate header-level Last Detected timestamp.
- Sports Config continues to show icons for every supported sport and explicitly owns Pre-match/In-play minimum profit, minimum return, opportunity quality and maximum stake; engines may only be stricter.

No DB migration is required. LIVE order writing remains centrally locked.

## 0.9.39 — Feed Freshness, Capital Deployment & UI Consolidation

0.9.39 closes the held operator fix list on top of 0.9.38 without reopening the canonical Sports lifecycle architecture.

- Dashboard Activity Monitor polling now requests the selected global SIM/LIVE mode, preventing a LIVE `0/0` feed summary from appearing in SIM.
- Operational provider state now exposes transport connectivity and data freshness separately. Dashboard provider cards can therefore say `Market CONNECTED · DATA STALE` instead of implying a disconnect.
- Discovery catalogue work runs in its own worker thread/DB connection so a long Discovery cycle cannot block the fast price scanner and manufacture stale-feed symptoms.
- The Dashboard scanner status card is labelled `Price Scan`, removing ambiguity beside the independent Discovery card.
- Performance exposes an event-driven `capital_timeline`: Capital Deployed rises on position opens and falls on settlement/release at authoritative event timestamps. The chart uses a clearly labelled deployed-capital scale and the fixed inspector remains stepwise/authoritative rather than interpolating financial values.
- Performance Refresh and Market Analysis controls share the same right-aligned header/control baseline.
- Replay moves Running P&L, Replay Time and playback controls into a fixed right-hand console; the timeline keeps its own reserved grid row and no longer overlaps following sections.
- Sports Engines detail is a solid drawer, removes duplicate SIM/LIVE/routing/configure controls, and places `Export Engine` at the top.
- Sports Monitor fits Price Scan, Betfair, Matchbook, Next Price Check, Discovery, Next Discovery and Last Detected on one desktop row. Last Detected renders explicit date + time plus relative age.
- Sports Config coverage tiles now use recognisable icons for every supported sport.

No DB migration is required. LIVE order writing remains centrally locked.

## 0.9.38 — Scanner Recovery, Monitor Clarity & Engines Operations

0.9.38 is a reliability/operations release on top of the 0.9.37 UX closure and the 0.9.36 canonical Sports lifecycle. It fixes the scanner self-disable regression first, then aligns Monitor detection/provenance, redesigns Sports Engines as a full-width operational list with detail on demand, adds a quarantine-first engine package review/install flow, and completes the visible Refresh feedback sweep.

Key changes:

- fixes the P0 scanner recovery regression: `INSUFFICIENT_COMPATIBLE_VENUE_FEEDS` is now a local market/evidence rejection and can no longer persist an operator-enabled engine as globally disabled;
- automatically heals the exact stale `DISABLED / INSUFFICIENT_COMPATIBLE_VENUE_FEEDS` state left by 0.9.36/0.9.37 when subsequent valid multi-venue evidence arrives;
- adds Monitor **Last Detected** date/time, separate from Last Scan/refresh, using first-seen authoritative detection evidence and respecting mode/filter/engine attribution;
- treats healthy SIM in-play modelling and the central LIVE order-write lock as neutral information rather than Monitor degradation;
- renames the Monitor attribution column to **Engine**, marks current routing as neutral `Routed`, and reserves **Legacy / Unverified** for genuinely historical rows without authoritative origin;
- consolidates Monitor onto one visible Stream / Engine / Status / Sport / Market / Venue / Account / Search filter state, with lifecycle counters and records sharing the same scope;
- redesigns Sports Engines to five equal summary tiles plus a full-width lifecycle table; the Enabled control is furthest right and row detail moves into a right-side drawer;
- adds contextual drawer navigation to Monitor, Results and Scenarios while preserving canonical engine IDs;
- adds **+ Add Engine** with `Upload → Quarantine → Validate → Review → Install`; upload validation is static and never executes strategy code or installs dependencies;
- validates archive count/size/path traversal/symlink/native-payload/dependency policy and manifest/API/config compatibility before installation review;
- requires explicit installation confirmation, defaults new engines DISABLED, routes duplicate IDs through Upgrade Review and records package filename/checksum/install/previous-version provenance;
- validates reviewed restricted code only after explicit install confirmation and before replacing an existing package, so a failed upgrade cannot overwrite the installed version;
- standardises visible Refresh controls on immediate `Refreshing…`, duplicate-click protection, `Updated` / failure feedback, preserved current data and coherent parent refresh where applicable.

Safety / persistence:

- additive database migration only: engine-evaluation venue provenance plus engine package filename/install/previous-version metadata; no destructive reset;
- no historical engine ownership is inferred or backfilled;
- Results remains authoritative for Settled/Realised P&L and Replay reconciliation;
- global SIM/LIVE ownership and strict cross-mode lifecycle isolation remain intact;
- LIVE order writes remain centrally locked.

## 0.9.37 — UX & Semantic Closure

0.9.37 is a closure/refinement release on top of the 0.9.36 Sports lifecycle architecture. It does not add a new product area; it closes the remaining selected-mode health, financial replay, Sports Config and analytics consistency gaps while preserving authoritative lifecycle/provenance and Results settlement authority.

Key changes:

- evaluates Dashboard provider health against the currently selected global SIM/LIVE mode instead of mixing opposite-mode capability state into the health colour/denominator;
- derives SIM account readiness from canonical SIM virtual-account state and LIVE readiness from LIVE provider-account state;
- treats intentionally disabled/not-expected providers as neutral, stale expected services as degraded and actual failures as errors;
- uses expected-provider denominators so healthy Betfair + Matchbook with Smarkets not expected can report `2 / 2 HEALTHY`;
- changes Performance playback from bucket-index jumps to a continuously moving `requestAnimationFrame` playhead while financial values remain the latest authoritative observation at/before the cursor;
- replaces the large floating Performance tooltip with a fixed Financial Inspector showing replay time, Capital, Available, Capital Deployed/utilisation, Running P&L/bucket P&L and settlement context;
- keeps P&L bars event-timed and both Performance chart panels on one shared cursor, with the inspector moving below the charts on narrower layouts;
- rebuilds Sports Config as the portfolio-wide Sports policy/guardrail surface: coverage, symmetrical Pre-match/In-play enablement, qualification/risk guardrails, provider-aware SIM execution modelling and one page-level Save Changes flow;
- removes money/funding/reset controls from Sports Config; Admin remains the owner of provider/account/funding configuration and Accounts/Sports Overview own current-money visibility;
- enforces global stream disablement in the scanner so an engine cannot process a Sports stream that Sports Config has disabled;
- enforces guardrail precedence so engines may be stricter than Sports Config but cannot loosen the outer Sports minimum/maximum envelope;
- renames Accounts headline `Total Exposure` / `Total Utilisation` to **Current Exposure** / **Current Utilisation** without changing 0.9.34 authoritative arithmetic;
- right-aligns Market Analysis filters on desktop and adds common Refreshing / Updated / failed feedback plus stale-response protection;
- refines Replay playback-console hierarchy with explicit **RUNNING P&L** and **REPLAY TIME** labels.

Safety / persistence:

- no database migration or reset in 0.9.37;
- the additive 0.9.36 lifecycle/provenance schema remains the baseline;
- no historical engine ownership is inferred or backfilled; legacy provenance remains Legacy / Unverified;
- Engines Settled/P&L continues to derive from canonical Results settlement records;
- global SIM/LIVE ownership and strict cross-mode isolation are preserved;
- LIVE order writes remain centrally locked.

## 0.9.36 — Sports Lifecycle Alignment

0.9.36 aligned Sports Monitor, Engines, Results and Replay around one canonical lifecycle and one authoritative engine-provenance model.

Key changes:

- standardises the operator lifecycle as `Processed → Opportunities → Qualified → Executed → Settled → Realised P&L`;
- persists authoritative originating-engine provenance for new executions and carries it through position, settlement and Replay;
- marks historical rows without trustworthy origin as Legacy / Unverified instead of inferring engine ownership;
- adds engine-evaluation evidence so Processed / Opportunities / Qualified remain distinct from authoritative Executed / Settled records;
- prevents competing engines from double-claiming one execution, settlement or realised P&L result;
- makes Results the authoritative Sports settlement ledger and derives Engines Settled/P&L from the same canonical records;
- adds Monitor Stream / Engine / Status fast filters, explicit Market filtering and filter-scoped funnel counts;
- aligns Results on Stream / Engine terminology and preserves engine provenance into Replay;
- removes local SIM+LIVE mixing controls from Monitor/Results/Engines in favour of the global mode context;
- keeps LIVE evaluation distinct from centrally locked LIVE execution.

Safety / persistence:

- additive lifecycle/provenance database migration only; no destructive reset;
- no heuristic historical provenance backfill;
- SIM and LIVE lifecycle/financial records remain isolated;
- LIVE order writes remain centrally locked.

## 0.9.35 — Shared Portfolio Finance, Sports Operations & Performance Replay

0.9.35 combines the agreed Sports Overview operational redesign with the Performance timeline/filter follow-up. The release introduces one canonical current portfolio-financial scope so Sports Overview and Performance use the same current Capital / Available / Capital Deployed definition rather than maintaining separate arithmetic.

Key changes:

- adds a shared `mode + portfolio + venue` current financial-state boundary used by Sports Overview and Performance;
- derives SIM Sports capital only from authoritative Pre-match + In-play stream-wallet allocations and keeps Racing separate;
- leaves LIVE Sports/Racing capital unavailable when provider accounts lack portfolio-allocation provenance instead of guessing or filling from SIM;
- rebuilds Sports Overview as a concise operational page with five current Sports tiles, Sports scanner/stream status, independent Pre-match/In-play cards, up to three current Market Highlights, concise open Sports positions and warning-only exceptions;
- removes the redundant generic account-basis strip from Sports Overview because the page now displays canonical Sports money directly;
- keeps Market Analysis responsible for historical ranking/concentration and Performance responsible for financial analysis;
- refines Performance header filters to Period / Portfolio / Venue with labels above controls and the global SIM/LIVE switch as the only mode owner;
- adds visible Performance Refresh / Refreshing / Updated / failed feedback and preserves current data while a new scope is loading;
- adds optional Financial Timeline playback with Play / Pause / Reset, one shared playhead/cursor and one shared tooltip across Capital Position and Realised P&L panels;
- makes Capital Deployed a visible upper-panel line alongside Capital and Available, with a non-negative axis floor where all financial-position values are non-negative;
- keeps playback as a visual reveal of stored financial buckets only: no smoothing, fabricated values or settlement/exposure retiming;
- fixes duplicated current-account accumulation in the Performance current-state path so headline/current timeline values reconcile to the canonical portfolio state;
- removes the redundant generic Analytics account-basis strip from Performance; its compact finance-basis note remains.

Safety / persistence:

- no database migration or reset;
- no strategy/engine decision change;
- no provider credential or account-control ownership change;
- no settlement timing/value fabrication;
- SIM and LIVE remain strictly isolated;
- LIVE order writes remain centrally locked.

## 0.9.34 — Accounts Authoritative Total Semantics

0.9.34 refines the 0.9.33 Accounts financial view without changing page ownership or the underlying database/strategy/execution model. Money Now now makes it explicit that headline values are reconciled totals of authoritative venue-account data in the selected global SIM/LIVE context.

Key changes:

- renames the four Money Now tiles to **Total Capital | Total Available | Total Exposure | Total Utilisation**;
- aggregates each financial field only from venue accounts that authoritatively report that field; missing values are excluded rather than coerced to zero;
- preserves genuine reported zero values as `£0.00`; unavailable values remain `—`;
- keeps Betfair, Matchbook and Smarkets as the three supported venue slots while Smarkets awaiting API access remains unavailable and does not enter headline arithmetic;
- exposes reporting-account and per-field coverage metadata from the Accounts backend so the UI can state how many supported venue accounts are actually reporting;
- shows concise reporting context beneath Money Now, including field-coverage differences where a provider only reports part of the financial tuple;
- calculates Total Utilisation only when Total Capital and Total Exposure are authoritative over the same venue set and capital is greater than zero;
- refuses to aggregate unlike account currencies rather than presenting a misleading cross-currency total;
- introduces an explicit Money Now loading/skeleton state so mode changes and refreshes do not temporarily masquerade as zero or unavailable financial values;
- retains strict SIM/LIVE isolation and never fills missing values in one mode from the other mode.

Safety / persistence:

- no database migration or reset;
- no provider credential/configuration change;
- no strategy/scanner decision change;
- no settlement or transaction-attribution change;
- no LIVE order capability change.

## 0.9.33 — Accounts Feed Readiness & Transactions

0.9.33 is a focused Accounts reliability refinement over 0.9.32. It does not change the database, strategy, settlement or execution model.

Key changes:

- fixes feed readiness provenance by merging the newest available **price-scan and discovery-scan status per provider** instead of selecting one whole scan status list;
- prevents a healthy venue from appearing `UNKNOWN` merely because it was absent from the other scan type;
- reports enabled feeds with no recorded observation as **WAITING** (or OFFLINE when the scanner is not loaded) rather than ambiguous UNKNOWN;
- keeps Smarkets explicitly **AWAITING API ACCESS**;
- records feed-status provenance (`price` / `discovery`) and provider-specific observation time in operational state;
- upgrades Accounts **Account Activity** into read-only **Account Transactions**;
- adds compact **Added | Withdrawn | Net Funding | Transactions** summary metrics for the selected period;
- normalizes SIM funding-ledger rows into Funds Added, Funds Withdrawn, Balance Adjustment and Allocation transactions without inventing settlement/venue attribution;
- uses provider-native LIVE account-history classifications for deposits, withdrawals, settlements, commission and other supported movements;
- removes the redundant Mode column from the transaction table because the global SIM/LIVE context remains the single authoritative mode owner.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no settlement calculation or venue-attribution fabrication;
- no account mutation added to Accounts;
- no SIM fallback in LIVE account state/history;
- no LIVE order capability change.

## 0.9.32 — Accounts Observe / Admin Configure

0.9.32 enforces the revised Accounts/Admin responsibility boundary without changing the established database, strategy or execution model.

Key changes:

- makes **Accounts read-only**: no credentials, feed/account toggles, provider setup, nickname editing, SIM funding or other state-changing controls are rendered there;
- removes the duplicate Accounts SIM/LIVE selector; the global SIM/LIVE switch remains the single mode owner;
- rebuilds Accounts around **Operational Readiness**, **Money Now**, **Venue Status**, reconciliation warnings and **Account Activity**;
- adds readiness tiles for scanner state, selected-mode feed health, selected-mode account readiness and oldest-data freshness;
- makes Money Now exactly **Capital | Available | Exposure | Utilisation** using one canonical selected-mode account scope;
- turns venue cards into read-only status cards showing account access, selected-mode feed state, market connectivity, latency, freshness, capital, available and exposure;
- simplifies Account Activity to **Today | 7D | 30D | All** and adds explicit Mode and Balance columns;
- moves provider connections/credentials into Admin and keeps advanced account settings, SIM funding and provider enablement there;
- adds Admin venue controls for independent SIM/LIVE feed access, SIM/LIVE account access, nickname editing and LIVE execution requests while the central order-write lock remains closed;
- reuses the canonical operational-status model in the Accounts backend response so readiness/latency/freshness are not calculated by a second competing health model;
- healthy reconciliation is silent on Accounts; only warnings or unavailable reconciliation evidence are surfaced.

Safety / persistence:

- no database migration or reset;
- no strategy or settlement calculation change;
- no change to the global SIM/LIVE ownership model;
- no SIM fallback in LIVE account state;
- no LIVE order capability change; central LIVE order placement remains locked.

## 0.9.31 — Performance Finance Control View

0.9.31 implements the finance-first Performance redesign while keeping the global SIM/LIVE selector as the only economic-mode control. Performance has no local Mode, Type or Expected/Actual filter.

Key changes:

- reduces the Performance header to **Period | Portfolio | Venue**; the global SIM/LIVE context remains authoritative and is still shown by the page context badge;
- replaces the primary metrics with exactly **Net P&L | Capital | Exposure | Available | Portfolio ROI** using one selected financial scope;
- uses canonical account equity/available/reserved values for current capital state and keeps venue/portfolio scope consistent;
- adds a two-panel **Financial Timeline** with a shared bucket axis: Capital/Available/Exposure above and realised bucket P&L plus cumulative P&L below;
- changes timeline granularity adaptively: Today/24h are hourly, normal review ranges are daily, and long ranges adapt to weekly buckets;
- keeps historical capital/exposure null where authoritative account snapshots do not exist rather than reconstructing a false series;
- makes the final timeline point reconcile to current Capital, Exposure and Available where authoritative account state exists, while cumulative timeline P&L reconciles to Net P&L;
- promotes **Venue Performance** directly below the timeline with Capital, Available, Exposure, Utilisation, Settled Turnover, P&L and Return;
- moves Settled Turnover, Return on Deployed, Peak Exposure, Average Utilisation, Captured Edge and Settled Positions into supporting metrics;
- retains market/funnel/execution/recovery diagnostics only under collapsed **Deeper performance breakdown**;
- adds request-version protection and one coherent loading state so stale filter responses cannot overwrite a newer Performance selection;
- adds explicit backend mode guards: SIM Performance cannot serve LIVE requests and LIVE Performance cannot serve SIM requests.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no settlement calculation rewrite;
- no account mutation or provider-control change;
- no LIVE order capability change;
- SIM and LIVE financial data remain strictly isolated with no cross-mode fallback.

## 0.9.29 — Replay Header Filters & Stable Timeline Layout

0.9.29 is a Replay-only UI/layout refinement over 0.9.28. Replay data, settlement economics, engine provenance and SIM/LIVE ownership are unchanged.

Key changes:

- moves Replay filters into the Analytics page header, to the right of the Replay title, matching the compact Market Analysis pattern and reclaiming a full row of vertical space;
- keeps the custom replay date range attached to the header filters when **Custom period** is selected;
- permanently reserves the selected-position detail row in desktop viewport-fit mode so playback/selection cannot steal height from or push into the timeline;
- fixes the desktop detail row at a stable 72px while retaining responsive auto-flow below desktop widths;
- preserves the 0.9.28 lifecycle legend, collision-safe settlement labels and running realised P&L.

Safety / persistence:

- no database migration or reset;
- no scanner/strategy decision change;
- no account/provider or settlement semantic change;
- no SIM/LIVE data ownership change;
- no LIVE execution capability change.

## 0.9.28 — Replay Timeline Clarity & Running P&L

0.9.28 is a Replay-only operator UI refinement over 0.9.27. Stored execution/settlement data, financial calculations, engine provenance and SIM/LIVE ownership are unchanged.

Key changes:

- adds a compact **Running P&L** beside the replay clock; it starts at £0.00 and changes only when visible settled positions cross the playhead;
- replaces the ambiguous timeline note with an explicit lifecycle legend for **Opened**, **Position active**, **Settled P&L**, and **Emergency hedge**;
- makes permanently visible settlement P&L labels collision-safe while always preserving the selected settlement label;
- rebuilds the selected-position strip into five dedicated metric columns so Actual result, Capital, Returned, Final P&L and Structure cannot stack over one another;
- keeps Engine / Venue / Account / SIM-LIVE provenance in the selected-position header.

Safety / persistence:

- no database migration or reset;
- no scanner/strategy decision change;
- no account/provider or settlement semantic change;
- no LIVE execution capability change.

## 0.9.27 — Explicit SIM/LIVE Page Context

0.9.27 is a UI-only mode-awareness refinement over 0.9.26. It does not change SIM/LIVE data ownership, scanner behaviour, financial calculations or execution semantics.

Key changes:

- adds a compact mode badge directly beside the title on mode-sensitive operator pages: **Active Positions**, **Sports Monitor**, **Sports Overview**, **Racing Overview**, **Racing Monitor** and every Analytics pane including **Performance**, Results, Execution Analysis, Market Analysis, Replay and Scenarios;
- shows **SIM · VIRTUAL** in SIM and **LIVE · ACTUAL ONLY** in LIVE, making an empty LIVE page visibly different from a SIM page with missing data;
- updates the Accounts header badge to **SIM · VIRTUAL** or **LIVE · READ ONLY** while retaining the existing account safety banner;
- updates badges synchronously when the global SIM/LIVE selector changes, before asynchronous page data is loaded;
- deliberately leaves the Dashboard composition untouched so its clock/header geometry does not move; the existing top-bar SIM/LIVE selector remains authoritative there;
- preserves all existing no-fallback rules: LIVE pages continue to use isolated LIVE reads and never substitute SIM economics.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no account/provider control change;
- no Performance or settlement calculation change;
- no execution-state semantics changed;
- LIVE order writes remain disabled.

## 0.9.26 — Dashboard Latest Result Alignment

0.9.26 is a surgical visual refinement to the 0.9.25 Dashboard latest-result ticker. Dashboard composition and data ownership are unchanged.

Key changes:

- aligns the ticker left edge exactly with the **Total Profit Today** KPI column above on the four-column desktop Dashboard grid;
- keeps the ticker at the existing 30px height and absolutely positioned inside the Venue Accounts header, so no Dashboard section moves;
- increases ticker text weight and contrast for the event, market/result, outcome, P&L and settlement time while retaining green/red semantic emphasis;
- preserves strict SIM/LIVE result-source isolation with no SIM fallback in LIVE.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no account/provider control change;
- no execution-state semantics changed;
- LIVE order writes remain disabled.

## 0.9.25 — Dashboard Latest Settled Result

0.9.25 is a surgical Dashboard information-priority update. It adds one compact latest-result ticker in the existing Venue Accounts header whitespace without changing Dashboard flow or geometry.

Key changes:

- adds **Latest Result** in the unused right side of the Venue Accounts heading row;
- keeps the ticker absolutely positioned so existing Dashboard cards, spacing and vertical layout do not move;
- shows event, score when supplied (otherwise winning market/selection), realised P&L and settlement time;
- uses green/red semantic emphasis for winner/loser and realised profit/loss while keeping structural text neutral;
- reads SIM results only from the settled SIM Monitor ledger;
- reads LIVE results only from the actual LIVE results read model, with **no SIM fallback**;
- clears the ticker immediately on SIM/LIVE mode changes so stale SIM settlement text can never remain visible in LIVE while the new read is pending;
- respects Dashboard All/Sports/Racing scope when choosing the latest result source row.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no account/provider control change;
- no execution-state semantics changed;
- LIVE order writes remain disabled.

## 0.9.24 — Financial & Capital Performance

0.9.24 reframes Performance around financial results, capital use, exposure and venue economics. Market analysis remains available as a secondary breakdown rather than the purpose of the page.

Key changes:

- makes the primary Performance KPIs **Net P&L**, **Return on deployed**, **Current capital**, **Current exposure**, **Peak exposure** and **Average utilisation**;
- adds financial/capital trend choices for portfolio capital, average exposure and capital utilisation alongside P&L/return metrics;
- promotes **Capital & exposure** to a first-class section and makes **Venue capital & performance** the main comparison table;
- combines current venue capital/available/exposure with period deployment, P&L contribution, return and fill quality;
- keeps market, venue-pair, funnel and recovery evidence behind a collapsed **Deeper performance breakdown** so the default page stays decision-focused;
- moves sport, individual market and venue-pair selectors under **More filters** while keeping period, portfolio, type, venue and basis immediately accessible;
- scopes SIM venue available/reserved figures to the selected Performance portfolio/type, matching the existing scoped venue capital calculation;
- in LIVE, reads venue account capital/exposure independently from actual/expected performance evidence without treating simulated decisions as executions or P&L.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no order/execution capability change;
- LIVE order writes remain disabled.

## 0.9.23 — Operator UI Priority Pass

0.9.23 is a frontend-only operator workflow refinement over 0.9.22. It does not change database schema, strategy logic, provider/account design, archive behaviour or LIVE order safety.

Key changes:

- adds compact Active Positions type tiles for **All**, **Pre-match**, **In-play** and **Racing**, with live counts and instant client-side filtering of already-loaded positions;
- makes Opportunity Detail event scheduling materially more prominent with dedicated **Event date**, **Start time** and **Timing** fields directly below the opportunity header;
- shortens Execution Analysis' default working view so critical execution rates and the execution ledger stay primary;
- moves timing-survival, execution-path profitability and emergency-hedge economics into a collapsed **Deeper execution analysis** section without removing any evidence;
- keeps individual Execution Detail economics visible while placing the longer stored checkpoint/action timeline behind an on-demand disclosure.

Safety / persistence:

- no database migration or reset;
- no strategy/scanner decision change;
- no account/provider control change;
- no execution-state semantics changed;
- LIVE order writes remain disabled.

## 0.9.22 — LIVE Dashboard Activity Ownership

0.9.22 is a narrow Dashboard correctness hotfix over 0.9.21. It does not change database schema, strategy logic, provider/account design, archive behaviour or LIVE order safety.

Key changes:

- makes Activity Monitor ownership explicit: **Discovered**, **Matched** and **Processed** are shared provider-market pipeline facts, while **Opportunities**, **Qualified** and **Executions** are owned by the selected SIM/LIVE economic context;
- prevents the generic runtime-status renderer from repainting the LIVE Activity Monitor with SIM opportunity/qualification/execution state;
- clears stale SIM Activity Monitor values immediately when switching to LIVE;
- loads LIVE Opportunities and Qualified counts only from isolated `live_decision_evidence`, bounded to the viewer-local day;
- loads LIVE Executions only from actual `live_execution_activity` (currently zero while order writing remains structurally locked);
- keeps the shared discovery/matching/processing prefix cumulative from local midnight and refreshes the LIVE activity strip independently every ten seconds;
- makes the Dashboard refresh button mode-aware so LIVE refreshes LIVE state instead of issuing a discarded SIM dashboard read;
- extends the existing LIVE decision read helper to honour the bounds/shape options already supplied by its callers.

Safety / persistence:

- no database migration or reset;
- no strategy or scanner decision change;
- no account/provider control change;
- LIVE order writes remain disabled.

---

## 0.9.21 — Dashboard Total Economics

0.9.21 is a surgical Dashboard update over the clean 0.9.20 recomposition. Accounts, Admin, Engines, provider controls, storage, archive behaviour, portfolio layout and the two seven-day profit charts are unchanged.

Key changes:

- adds a new four-tile **Total Economics** row immediately below Activity Monitor and above Venue Accounts;
- shows **Total Capital**, **Total Capital In Play**, **Total Profit Today** and **Total Locked Profit**;
- derives all four totals from the exact same per-venue economic values used by the Betfair, Matchbook and Smarkets tiles directly below, preventing a second competing calculation path;
- keeps unavailable venue values unavailable rather than fabricating provider economics, while summing the venues that actually report the relevant measure;
- preserves the existing three-wide venue tiles, separate daily-performance row, Sports/Greyhound portfolio row and the two reconciled seven-day settled P&L charts;
- leaves all account/feed/execution controls in Accounts and keeps the Dashboard display-only.

---

## 0.9.20 — Dashboard Recomposition

0.9.20 rebuilds the Dashboard from the clean 0.9.18 release baseline and deliberately does not carry forward the discarded 0.9.19/partial-0.9.20 Dashboard layout. Accounts, Engines, Admin, provider controls, storage and archive behaviour are otherwise unchanged.

Key changes:

- keeps the existing world-clock row and provider/system status row, with no extra simulated-mode pill on the status strip;
- restores **Activity Monitor** immediately below status, using the existing discovery → matched → processed → opportunities → qualified → executions pipeline and no profit headline cards;
- adds a display-only **Venue Accounts** row with Betfair, Matchbook and Smarkets medium tiles, three across on desktop. Each tile shows Capital, Capital In Play, Profit Today and Locked Profit; all management stays in Accounts;
- adds a separate four-tile daily performance row: Best Win Today, Wins Today, Losses Today and Win Rate Today, based on the viewer-local calendar day;
- retains the Sports Portfolio and Greyhound Portfolio row;
- retains the existing seven-day settled P&L chart as the daily total across venue contributions;
- replaces the former seven-day processing chart with **7-day P&L by Venue**, grouped Betfair/Matchbook/Smarkets bars for each of the same seven days;
- derives venue settled P&L from stored exchange settlement contribution, with a deterministic deployed-stake fallback only for legacy settled rows lacking a venue split, so venue bars reconcile to the existing daily total;
- attributes open Locked Profit to venues by deployed stake for display while keeping the canonical position-level locked-profit calculation authoritative;
- keeps Smarkets visible with unavailable values until real API data exists; no fabricated provider economics are introduced;
- preserves SIM/LIVE isolation, the Accounts-only provider control model, engine/venue provenance, Greyhounds, archive/pruning state and the central LIVE order-write lock.

---

## 0.9.18 — Accounts & Administration Consolidation

0.9.18 makes Accounts the single provider/account management surface, simplifies Admin to system/storage/maintenance responsibilities, and makes venue evidence eligibility independently selectable for SIM and LIVE. It is an additive in-place upgrade over 0.9.17: engine identities/configuration, Greyhounds, SIM wallets, LIVE provider state, Results/Replay/Scenario provenance, archive pilot/runtime-gate evidence, verified Parquet and pruning configuration are preserved.

Key changes:

- Accounts owns provider credentials/session management, account nickname, commission/currency assumptions, venue controls and SIM/LIVE account state; Admin no longer duplicates provider credentials or venue-account management;
- Dashboard becomes an operational summary and renders Betfair, Matchbook and Smarkets in a strict three-column desktop venue grid, with 2-column and 1-column responsive fallbacks;
- introduces independent `sim_feed_enabled` and `live_feed_enabled` evidence gates per venue. One physical provider transport may be shared where appropriate, but SIM and LIVE eligibility never shares a user-facing enabled flag;
- keeps SIM account availability, LIVE account access and LIVE execution request as separate controls; effective LIVE order writing remains centrally locked;
- Feed OFF is non-destructive and preserves credentials, configuration, historical Results, Replay evidence and archive data;
- Accounts SIM/LIVE views and KPIs are explicitly mode-labelled. SIM virtual wallets never supply LIVE balances, and LIVE provider values never seed SIM bankrolls;
- normal user-facing provider inventory is Betfair, Matchbook and Smarkets. Architecture-only future-provider shapes do not appear as active Accounts/Dashboard providers;
- keeps Smarkets explicitly `AWAITING API ACCESS` with no network adapter, fabricated account values or order-write path;
- unifies provider/account health semantics across Dashboard and Accounts instead of showing contradictory generic states;
- removes the former third operating-mode concept from active application source, UI, API/state models and tests; persisted legacy values migrate one-way to SIM;
- preserves canonical engine identities, independent engine SIM/LIVE enablement, Engine/Venue/Account/Mode provenance and Greyhounds UI;
- preserves archive pilot, runtime gate, Parquet manifests/checksums, pruning state and central LIVE safety across the 0.9.17 → 0.9.18 upgrade;
- keeps one evergreen `README.md` plus this master release history with no per-build/hotfix documentation.

Safety defaults at release:

```text
active economic modes                         SIM / LIVE only
Betfair LIVE feed                             OFF on migrated shared-feed state
Matchbook LIVE feed                           OFF on migrated shared-feed state
Smarkets SIM/LIVE feeds                       OFF
Smarkets state                                AWAITING API ACCESS
LIVE order writes                             FALSE
archive/prune settings                        preserved on upgrade
```

---

## 0.9.17 — Engine Provenance, Venue Controls & SIM/LIVE Separation

0.9.17 makes engine, venue/account and SIM/LIVE identity explicit across operational views while keeping Scenarios as the strategy-modelling surface. It is an additive in-place upgrade over 0.9.16: installed engine/config metadata, Greyhounds UI, Betfair/Matchbook state, staged Smarkets state, archive pilot/runtime-gate evidence, verified Parquet and pruning configuration are preserved.

Key changes:

- adds editable engine **Nickname** metadata alongside Description/Notes; metadata edits do not create immutable strategy-config versions or replace engine identity/version provenance;
- adds Engine/Venue/Account/Mode columns and filters to Sports Monitor, Results and Replay; Greyhounds/Racing Monitor also carries `GREYHOUNDS_BASELINE_ARB` / **Greyhounds Base** provenance;
- deterministically attributes legacy ordinary Sports, SuperBet scaled-entry and Greyhounds records to canonical engines where safe; ambiguous historical records remain **Legacy / Unattributed** rather than being guessed;
- keeps Replay as chronological recorded-evidence review with Engine/Venue/Account/SIM-LIVE provenance and a collapsible row-level ledger; strategy modelling/comparison remains in Scenarios;
- makes per-engine **SIM** and **LIVE** enablement independent. Enabling/disabling one does not change the other, and LIVE remains centrally order-write locked;
- retires the former third operational/economic mode into SIM semantics. Legacy lifecycle values canonicalise one-way to SIM for compatibility; historical timing evidence remains readable without exposing a third active mode;
- adds persistent venue controls for Betfair, Matchbook and Smarkets: independent Market Feed, Account Access, SIM Availability and LIVE Execution-request state plus editable account nickname;
- Feed OFF is non-destructive and isolated to that venue: new evidence stops, while configuration, account metadata, Results history and Replay evidence are preserved;
- Dashboard renders all three venue accounts with separate **SIM bankroll/available/exposure** and **LIVE balance/available/exposure**; SIM and LIVE economics are never used as one funds source;
- keeps Smarkets explicitly **AWAITING API ACCESS**, with Feed/Account/SIM/LIVE unavailable until activation; no Smarkets network or order-write path is introduced;
- engines report a clean effective-state reason such as `INSUFFICIENT_COMPATIBLE_VENUE_FEEDS` when required evidence is unavailable instead of treating feed disablement as an engine crash;
- preserves Results win/loss cohort KPIs while adding the new provenance dimensions;
- preserves archive pilot, runtime gate, Parquet manifests/checksums, pruning state and central LIVE safety across the 0.9.16 → 0.9.17 upgrade;
- keeps the release tree consolidated: one evergreen `README.md`, one master `RELEASE_NOTES.md`, no per-build/hotfix documentation.

Safety defaults at release:

```text
active economic modes                         SIM / LIVE only
LIVE order writes                             FALSE
Smarkets API/network adapter                  NOT ENABLED
Smarkets state                                AWAITING API ACCESS
archive/prune settings                        preserved on upgrade
```

---

## 0.9.16 — Engine Library & Smarkets Foundation

0.9.16 productises engine management and stages Smarkets as ArbScanner's third exchange provider without depending on API activation. It is an additive in-place upgrade over 0.9.15: archive pilot/runtime-gate state, verified Parquet, pruning configuration, engine decision/config provenance and LIVE execution locks are preserved.

Key changes:

- simplifies Sports/Racing Engines into an **installed engine library** rather than a second research workspace; Experiments/Compare are removed from the Engines UI and modelling/comparison is performed in Scenarios;
- hides framework/reference engines such as `SPORTS_DEPTH_ARB_REFERENCE` and `NOOP_TEST_ENGINE` by default behind **Show research/test**, while the three real strategy families remain prominent;
- adds editable engine **Description** and **Notes** metadata; these edits do not create strategy configuration versions or alter historical decision provenance;
- adds portable `.arbengine` export/import. Imports are validated, stored under Application Support and always create a **RESEARCH + DISABLED** engine instance requiring explicit review/activation;
- supports reviewed-local custom engine packages with a restricted Python implementation, engine API/version compatibility, manifest/config schema/capability validation, forbidden import/unsafe-name checks and no provider credential/write interface;
- retains immutable configuration/version history and central lifecycle/risk authority for imported engines;
- adds Scenarios engine selection so one or more installed engines can be modelled against identical immutable recorded evidence, with engine/config/grade/intent provenance retained;
- stages **Smarkets** as a first-class third exchange provider alongside Betfair and Matchbook; Dashboard and provider/account runtime expose `AWAITING API ACCESS` while the operator's approved API access is not yet activated;
- records the current Smarkets HTTP API contract/rate-limit metadata in the provider profile, but deliberately registers **no Smarkets network adapter or order-write capability** in this release;
- Smarkets unavailability does not degrade Betfair/Matchbook or engines that do not require Smarkets; provider enable/disable remains explicit;
- keeps Greyhounds as a first-class visible Racing domain and retains canonical engine identities `SPORTS_BASELINE_ARB`, `SPORTS_SUPERBET_ARB`, `GREYHOUNDS_BASELINE_ARB`;
- preserves archive pilot, runtime-gate, Parquet, prune audit/configuration and LIVE execution safety state across the upgrade;
- keeps one evergreen `README.md` plus this master release history; no per-build README/hotfix/release-note files are introduced.

Safety defaults at release:

```text
Smarkets network/API adapter                  NOT ENABLED
Smarkets order writes                         FALSE
imported engine lifecycle                     DISABLED
imported engine grade                         RESEARCH
LIVE order writes                             FALSE
archive/prune settings                        preserved on upgrade
```

---

## 0.9.15 — General Strategy Engine Lab

0.9.15 turns the 0.9.14 engine framework into a strategy-neutral research platform and migrates the current real strategy families to canonical engine identities while preserving Greyhounds as a first-class UI/domain. It is designed as an additive in-place upgrade over a running 0.9.14 archive pilot; Application Support state, verified Parquet, runtime-gate evidence, pilot/pruning state and existing engine provenance are preserved.

Key changes:

- canonical real engine types are `SPORTS_BASELINE_ARB`, `SPORTS_SUPERBET_ARB` and `GREYHOUNDS_BASELINE_ARB`; reference/research types are `SPORTS_DEPTH_ARB_REFERENCE` and `NOOP_TEST_ENGINE`;
- old 0.9.14/early-development engine IDs/types are converged onto those canonical identities during additive schema migration so upgrade does not create parallel strategy identities;
- Greyhounds remains visible in its existing Racing/Greyhound UI; only embedded strategy special-casing moves behind `GREYHOUNDS_BASELINE_ARB`;
- SuperBet remains a genuine strategy but is no longer a global scanner primitive: legacy global SuperBet settings import once into `SPORTS_SUPERBET_ARB` immutable configuration and are retired;
- generic scanner/execution code resolves scaled-entry behavior through the `SCALED_ENTRY` engine capability rather than an engine-name branch;
- adds engine grades `RESEARCH`, `STANDARD`, `ADVANCED`, `EXTREME`, independent from lifecycle and LIVE authority;
- generalises `DecisionIntent` for `ARBITRAGE`, `OPEN_POSITION`, `CLOSE_POSITION`, `REDUCE_POSITION`, `HEDGE`, `TRADE` and `MARKET_MAKE`; non-arbitrage intents are not forced to invent guaranteed-profit semantics;
- engine capability metadata drives compatibility/routing/UI instead of hard-coded SuperBet/Greyhound checks in generic platform areas;
- adds Engine Lab experiment creation from immutable source configurations; experiments default to RESEARCH/EXPERIMENTAL without mutating the source engine;
- adds bounded parameter sweeps with preflight variant-count limits and schema validation before any variants are created;
- adds strategy-neutral engine comparison metrics including decisions, expected/net P&L, requested/deployed capital, turnover, ROI/profit-on-capital and decision rate;
- engine Replay comparison feeds every selected engine the same canonical verified-Parquet + hot-SQLite evidence cohort, in deterministic timestamp order with the existing no-look-ahead barrier;
- experiment runs persist engine/config identity and a deterministic evidence-cohort hash so unchanged research runs are reproducible;
- existing Scenario engine comparison retains engine grade/config provenance and remains forced into research SIM;
- Sports/Racing Config threshold edits synchronise into new immutable primary engine configs rather than becoming a second mutable strategy source;
- archive/pruning configuration, runtime-gate evidence, verified Parquet and LIVE execution locks remain independent and are preserved on upgrade.

Safety defaults at release:

```text
LIVE order writes                           FALSE
engine grade                               does not grant execution authority
archive/prune settings                     preserved on upgrade
matched_market_archive_required_before_prune FALSE unless explicitly enabled previously
```

---

## 0.9.14 — Engine Framework

0.9.14 introduces the first-class strategy engine platform while preserving the trusted provider, risk/execution, SIM/LIVE and archive/pruning boundaries established by earlier releases. The release is designed as an additive in-place upgrade over a running 0.9.13 archive pilot; Application Support state, verified Parquet, runtime-gate evidence, archive pilot state and pruning settings are not reset or enabled by installation.

Key changes:

- adds immutable canonical `MarketEvidence` and frozen `EngineEvaluationContext` contracts;
- adds a standard provider-blind `DecisionIntent`; engines have no provider credential or order-write interface;
- adds `EngineRegistry`, explicit engine type/version/config schema validation, engine instances and immutable configuration history;
- adds requested/effective lifecycle with `LIVE_APPROVED` degrading to `SIM` while LIVE execution remains locked;
- adds external `EngineRouter` rules for Sports/Racing, sport, competition and market type;
- migrates established strategy selection through `LEGACY_SIMPLE_ARB` instead of maintaining a permanent embedded parallel path;
- ships `DEPTH_ARB_REFERENCE` as a genuinely different reference implementation and `NOOP_TEST_ENGINE` for lifecycle/isolation tests;
- allows multiple isolated engines to consume the identical frozen market snapshot while the legacy engine preserves the established scanner opportunity pipeline;
- adds isolated engine decision, error, SIM, SIM and Scenario provenance stores;
- SIM records `WOULD_HAVE_PLACED` evidence and cannot create provider orders;
- adds Sports → Engines and Racing → Engines in the order `Overview → Engines → Monitor → Results → Config`;
- engine UI exposes requested/effective lifecycle, routing, immutable configuration versions/hashes, health/activity, SIM/SIM metrics, recent decisions, clone/configure/routing and research Scenario actions;
- Scenarios can run explicitly selected engines in research SIM without changing their operational lifecycle and persist scenario/engine/config provenance;
- Replay compares engines against verified-Parquet + hot-SQLite canonical evidence in deterministic timestamp order and evaluates one historical snapshot at a time to prevent future-data leakage;
- adds platform-controlled engine fan-out protection (`engine_max_concurrent_runtimes`, default 100);
- archive pilot/pruning configuration and LIVE execution locks remain independent of the engine framework.

Safety defaults at release:

```text
engine_max_concurrent_runtimes              100
LIVE order writes                           FALSE
archive/prune settings                      preserved on upgrade
matched_market_archive_required_before_prune FALSE unless explicitly enabled previously
```

---

## 0.9.13 — Controlled Archive-Gated Pruning

0.9.13 completes the storage lifecycle plumbing without enabling destructive pruning by default. It is designed for an in-place upgrade over a running 0.9.12 archive pilot: SQLite, verified Parquet, manifests, runtime-gate evidence, pilot state and settings remain under Application Support and are preserved across the worker restart.

Key changes:

- the 0.9.12 dry-run planner remains the single authority for prune eligibility;
- adds a real archive-gated executor that can delete only planner-approved retention-expired hours;
- deletion is performed in bounded SQLite batches with source-shape rechecks before each batch;
- partial progress is recorded atomically and can resume only when the recorded archive checksum/row count exactly explains the remaining SQLite rows;
- unrecorded partial deletion fails closed;
- every completed/failed prune appends a durable audit record with hour, cutoff, archive checksum/schema/row count, deleted rows, SQLite integrity result and scanner guard;
- post-prune verification confirms the Parquet hour is still checksum-valid and independently queryable;
- scanner-health failure pauses further archive/prune activity through the persisted safety state;
- when the archive pilot is ON but archive-gated pruning is OFF, retention-expired hours are finalized only and raw rows are retained; this prevents the legacy 48-hour deletion path from bypassing the new archive gate during the soak period;
- fresh/non-archive installs retain the existing 48-hour raw matched-market lifecycle;
- `scripts/archive_admin.py` remains the single operator utility and adds explicit `prune status|enable|disable|run-once` controls; destructive operations require exact confirmation tokens;
- Admin reports **PRUNING CAPABLE · OFF** until the archive-gated switch is explicitly armed; no destructive Admin UI control is added;
- no install/upgrade path enables pruning, rewrites archives or deletes historical data;
- LIVE order writes remain structurally disabled.

Safety defaults at release:

```text
matched_market_archive_enabled               FALSE for fresh installs; existing upgraded setting preserved
matched_market_archive_runtime_gate_required TRUE
matched_market_archive_required_before_prune FALSE
archive pilot ON + prune OFF                  finalize only / no raw deletion
LIVE order writes                            FALSE
```

---

## 0.9.12 — Release Consolidation & Archive-Prune Dry Run

0.9.12 consolidates the accumulated hotfix/patch packaging into the normal product tree and introduces a strictly read-only archive-gated prune planner. The currently running verified archive pilot survives an in-place 0.9.11 → 0.9.12 upgrade because operational state, runtime-gate evidence, manifests, Parquet files and SQLite data remain under Application Support and are not replaced by the installer.

Key changes:

- one evergreen `README.md` and this single `RELEASE_NOTES.md`;
- standalone hotfix/patch/performance documents and per-version release files removed from the release package;
- temporary archive scripts consolidated into `scripts/archive_admin.py`;
- 0.9.12 Admin exposes an archive-gated prune **DRY RUN** with eligible/blocked hours, row counts and fail-closed reasons;
- the planner requires finalized compact history, a compatible runtime gate, DuckDB availability, no archive safety block, VERIFIED manifest/checksum, matching schema, row count and id range;
- no 0.9.12 path enables archive-required pruning or invokes a new destructive archive-gated deletion;
- existing 48-hour matched-market lifecycle remains unchanged;
- continuous archival remains compatible with the 0.9.11 pilot state and resumes oldest-gap-first after worker restart;
- LIVE order writes remain structurally disabled.

Safety defaults at release:

```text
matched_market_archive_enabled               FALSE for fresh installs; existing upgraded setting preserved
matched_market_archive_runtime_gate_required TRUE
matched_market_archive_required_before_prune FALSE
LIVE order writes                            FALSE
archive prune planner                        DRY_RUN only
```

---

## 0.9.11

## Release type

Full release. Baseline: packaged 0.9.9.

## Objective

0.9.11 separates long-period analytics from short-lived raw market storage. Month-scale summaries use compact hourly evidence, while detailed historical drilldown can transparently combine verified Parquet archive hours with the current hot SQLite tail.

The release also removes silent long-history truncation from Replay/Scenarios and adds fail-closed archive safety controls. Archive conversion is present but remains disabled by default; archive-gated pruning remains disabled by default; LIVE order transmission remains structurally disabled.

## Changes

### Historical analytics architecture

- Added `AnalyticsStore` as the month-scale history planner.
- Summary history combines finalized hourly rollups with the current/unfinalized SQLite tail.
- Detailed history combines verified Parquet archive hours with hot SQLite rows.
- Coverage is ledger-based and strict: missing historical hours are surfaced as gaps instead of being inferred from MIN/MAX timestamps.
- Market Analysis reports independent summary and detailed-history completeness/gaps.
- `All history` resolves the true finalized-ledger + hot-tail range rather than treating the approximately 48-hour raw table as the historical boundary.
- Removed an unused Market Analysis query that regrouped large raw `matched_markets` JSON columns on every request.

### Replay / Scenario long-period correctness

- Period selection is pushed into SQLite and restricted to settled opportunities.
- Requests support the selected `detected_at` or `settled_at` time basis.
- The old silent 10,000-opportunity cutoff is removed from normal and direct Replay paths.
- A 250,000-opportunity safety ceiling plus sentinel row now fails explicitly and asks for a narrower period instead of returning partial history.
- Execution evidence is queried only for the already-selected opportunity cohort in bounded chunks.
- Replay position `Market evidence` uses the same detailed-history planner and is storage-tier agnostic.
- Partial detailed history is refused by default rather than silently presented as complete.

### Verified Parquet archive

- Added hourly Parquet conversion for closed `matched_markets` hours.
- SQLite source access is read-only/query-only.
- Every archive hour has a manifest with row counts, ID/time bounds, schema version and SHA-256 checksum.
- Independent DuckDB readback verifies Parquet content.
- Archive status includes continuity, checksum health, safety pause/backoff and next missing-hour target.
- Missing pilot hours are recovered oldest-first with bounded catch-up pacing.
- DuckDB is a declared runtime dependency (`duckdb>=1.1,<2`).

### Runtime safety interlocks

- `matched_market_archive_enabled = False` by default.
- `matched_market_archive_runtime_gate_required = True` by default.
- `matched_market_archive_required_before_prune = False` by default.
- Continuous archival refuses to start without a protocol/schema-compatible one-hour runtime-gate PASS.
- Continuous archival also refuses to start if DuckDB is unavailable.
- Archive failures and scanner-health failures use persisted backoff/pause state.
- Archive conversion runs separately at reduced process priority and is not part of the 15-second UI heartbeat.
- The Admin archive panel is read-only; it cannot arm archival or pruning.
- A standalone live-pilot utility is included for optional extended observation without changing application settings or invoking pruning.

## Real runtime gate

The reconstructed path was exercised against a running ArbScanner database on macOS with DuckDB 1.4.5:

- closed hour: `2026-08-13T13:00:00Z`;
- SQLite source rows: `9,244`;
- Parquet rows: `9,244`;
- Parquet size: `1,180,324` bytes;
- archive/source row and ID/time ranges matched;
- manifest SHA-256 verified;
- independent DuckDB readback succeeded;
- source remained stable during the gate;
- SQLite opened read-only/query-only;
- settings changed: false;
- pruning invoked: false;
- gate status: **PASS**.

This proves the release archive path can produce and verify a real Parquet hour. Extended multi-hour continuous observation is not required to install 0.9.11 because archival remains disabled by default.

## Safety boundary

0.9.11 does not add provider order placement, cancel/replace, real fills, real positions, real settlement or bankroll mutation.

Required invariants:

- `orders_write_capability = false`;
- `live_execution_allowed = false`;
- provider order writes remain unavailable;
- archive enabled by default = false;
- archive-required-before-prune = false.

The existing SQLite retention/pruning behaviour is unchanged unless the independent archive-required gate is explicitly enabled in a future operational stage.

## Packaging

The installer validates source/frontend version 0.9.11 before building, requires this release note, verifies the built and installed frontend version, preserves the standard `arbscanner-poc/` package root, and supports `--verify-only`.

The source package includes DuckDB in `requirements.txt`; the macOS build script installs declared requirements into its build virtual environment.

## Release validation

Validation was rerun after the final 0.9.11 version stamp:

- pytest collection: **571 tests**;
- result: **550 passed, 21 skipped, 0 failed**;
- dedicated release-stamp/safety tests: **3 passed**;
- Chromium rendered audit: **55/55 checks passed**;
- page JavaScript errors: **0**;
- frontend JavaScript syntax: **6/6 script blocks passed**;
- Python compile: **PASS**;
- shell/build syntax: **PASS**;
- installer `--verify-only`: **PASS** for source/frontend 0.9.11;
- runtime defaults: archive OFF, runtime gate required, archive-required pruning OFF.

The release ZIP is a macOS build/install source package. A macOS executable is intentionally not cross-compiled in the Linux validation sandbox; `BUILD_AND_INSTALL.command` performs the target-Mac build, verifies the embedded frontend version, installs the app and reinstalls the worker when applicable.

---

## 0.9.9

## Release type

Full release. Baseline: packaged 0.9.8.

## Objective

0.9.9 completes the page-native LIVE analytics wiring and removes remaining sources of SIM/LIVE presentation contamination and avoidable runtime latency.

The release preserves the core data boundary:

- provider-derived market/reference evidence is shared;
- SIM economic/execution state is SIM-only;
- LIVE account/economic/execution state is LIVE-only;
- LIVE decision evidence is isolated simulated evidence and is never an order, fill, position, settlement or account P&L;
- LIVE order transmission remains structurally disabled.

## Changes

### Performance — LIVE wiring

- Actual basis renders actual LIVE execution/settlement performance only and remains honestly empty while there are no LIVE executions.
- Expected basis renders isolated LIVE decision evidence using explicit simulated terminology.
- Expected profit, executable stake, simulated attempts/fills and execution-grade evidence are not presented as real LIVE P&L or execution.
- Provider-pair decision evidence remains separate from venue execution contribution.

### Market Analysis — LIVE wiring

- Market Analysis remains global.
- LIVE uses shared provider-derived market/reference/liquidity history.
- LIVE qualification overlays come only from isolated LIVE decision evidence.
- Actual LIVE attempted/executed/settled/P&L/deployed fields remain zero until real LIVE execution exists.
- SIM economic tables are not queried for the LIVE market-analysis path.
- LIVE weekly heatmap preserves shared observations/liquidity while suppressing SIM financial rollups.

### SIM/LIVE mode integrity

- The periodic 15-second frontend heartbeat now reads `runtime_state`, not the heavyweight `get_state` payload.
- The heartbeat cannot rehydrate SIM Dashboard/economic rows into LIVE pages.
- Header mode is owned by the selected global data context.
- Mode switches synchronously clear/prime mode-owned page shells before asynchronous reads finish.
- Sports, Racing, Monitor, Results, Replay, Execution Analysis, Scenarios and Market Analysis loaders reject late responses from the wrong mode/route/domain.
- Shared account-context strips now read the selected mode instead of always requesting SIM accounts.
- Market heatmap caches are explicitly namespaced by mode and render only after their mode token remains current.

### Runtime performance

- Scenarios prepares historical evidence once per request.
- SIM/monitor evidence is bulk-loaded instead of queried per opportunity.
- PRE-MATCH, IN-PLAY, COMBINED and capital-comparison variants reuse one prepared dataset.
- Duplicate Scenario requests are suppressed and stale generations cannot replace newer results.
- Scenario loading has an explicit preparing state.
- LIVE decision reads request only the summary/latest representations required by each page.
- LIVE Market Analysis skips SIM economic history queries; LIVE heatmap skips financial rollup work.
- Periodic runtime state is lightweight and no longer performs Dashboard/history/config hydration.

## Safety boundary

0.9.9 does not add order placement, cancel/replace, real fills, real positions, real settlement or bankroll mutation.

Required invariants:

- `orders_write_capability = false`
- real provider order calls = 0
- SIM financial mutations from LIVE = 0
- LIVE account mutations from simulated decision evidence = 0

## Packaging

The installer validates source/frontend version 0.9.9 before build, verifies the built and installed app, terminates an already-running ArbScanner process before replacement, preserves the standard `arbscanner-poc/` package root, and supports `--verify-only`.

---

## 0.9.8

**Baseline:** 0.9.7  
**Release type:** UI/runtime correction and navigation refactor  
**Global application modes:** `SIM | LIVE`  
**LIVE order transmission:** Structurally disabled  
**Order-write capability:** `false`

## Objective

0.9.8 corrects four UI/state defects on the packaged 0.9.7 baseline without changing provider market acquisition, decision mathematics, account ownership, Racing execution logic or LIVE execution safety.

The release:

1. removes the generic page-wide LIVE Decision Evidence panel from normal routes;
2. protects unsaved Racing Config drafts from periodic background hydration;
3. standardises Performance, Results and Replay on one shared date/time range controller and removes Replay Custom layout shift;
4. moves Results from global Analytics navigation into Sports and Racing while enforcing domain isolation at the backend boundary.

## Native LIVE pages

The 0.9.7 `live-mode-panel` compatibility layer no longer injects a second page above native LIVE content. Normal routes remain authoritative and may consume shared market evidence or isolated LIVE decision evidence through page-native components only.

Removing the generic panel does not remove or relabel shared provider market/reference data, LIVE decision evidence, Manual Scan, LIVE Accounts or Betfair feed status.

## Racing Config draft ownership

The global state refresh runs periodically and previously re-applied persisted Racing settings to editable controls. `loadRacing()` provided a second hydration path. Both now respect explicit Racing draft ownership.

Once an editable Racing setting changes, the form is dirty and background state/provider/monitor refreshes cannot replace the draft. A successful awaited save clears dirty state and re-hydrates from confirmed persisted state. Missing/unloaded values are not converted into false numeric zeroes.

## Shared date/time range control

Performance, Results and Replay use one shared date/time controller for preset selection, Custom range state, validation and timezone presentation. The Custom range is anchored outside normal document flow so opening it does not move unrelated Replay content.

## Results domain navigation

Results is no longer a global Analytics child. The navigation now exposes:

- Sports → Results
- Racing → Results

Market Analysis remains global.

Both routes reuse the shared Results pane. The frontend passes an explicit Results domain and `settled_positions` enforces it server-side:

- `sports` includes Sports pre-match/in-play records and excludes Racing;
- `racing` includes Racing records only.

LIVE Results remain honest actual-LIVE empty datasets until real settled LIVE positions exist; there is no SIM fallback.

## Safety and regression boundary

0.9.8 does not alter:

- canonical provider market matching;
- provider market acquisition;
- Betfair requested/effective feed integrity;
- Manual LIVE scan generation/frozen-context guards;
- commission, staking or decision mathematics;
- SIM wallet ownership;
- read-only LIVE Accounts;
- 0.9.3 Racing re-arm/storage semantics;
- execution architecture or provider order transport.

LIVE order placement remains structurally unreachable and `orders_write_capability` remains false.

## Validation

Test collection: **522 tests**.

Executed in isolated batches to avoid the long-running aggregate suite timeout:

- **501 passed**
- **21 skipped**
- **0 failed**

Additional validation:

- 20/20 focused 0.9.7–0.9.8 tests passed;
- all 0.9.8 focused bugfix tests passed;
- Python compile check passed;
- Chromium rendered audit passed all checks;
- generic LIVE panel absent on normal LIVE routes;
- Racing dirty-draft refresh regression passed;
- Replay Custom no-layout-shift regression passed;
- Results domain navigation/render regression passed;
- page JavaScript errors: 0.

## Release boundary

ArbScanner 0.9.8 is a contained UI/runtime correction release. It keeps normal native pages authoritative in LIVE, protects operator-owned Racing configuration drafts, centralises common date/time range behaviour, and gives Sports/Racing explicit Results routes with server-side domain isolation. Shared provider market data, SIM/LIVE economic isolation and the LIVE execution lock are unchanged.

## Installer/version preflight correction

The 0.9.8 distribution installer now validates the source and embedded frontend version before installation, terminates any already-running ArbScanner process before replacing `/Applications/ArbScanner.app`, verifies the built and installed frontend are 0.9.8, and launches a fresh process. This prevents an already-running 0.9.7 process from being reactivated after the application bundle is replaced and makes running the installer from an older source folder fail visibly instead of silently building the wrong version.

## Scenarios performance hotfix

- Scenario history is now prepared once per `analytics_replay` request and reused by PRE-MATCH, IN-PLAY, COMBINED and all comparison-bankroll variants.
- Monitor/sim evidence is bulk-loaded in bounded SQLite batches instead of using per-opportunity N+1 lookups on every replay pass.
- Stored legs and Monitor observations are decoded once during request preparation and reused by all scenario variants.
- Scenario diagnostics now expose preparation, modelling, comparison and total timings plus selected/run/observation counts.
- Analytics navigation no longer performs an intermediate pane load before opening the requested pane.
- Identical in-flight Scenario requests are de-duplicated and newer Scenario settings own the visible result via a generation guard.
- Scenarios now shows an explicit historical-loading state instead of appearing empty while modelling is in progress.
- Scenario mathematics, comparison bankrolls, history depth, SIM/LIVE isolation and the LIVE execution lock are unchanged.

---

## 0.9.7

0.9.7 restores the complete ArbScanner application UI in LIVE context, formalises provider-derived market/reference data as shared between SIM and LIVE, and separates Betfair's configured market-feed request from the runtime-effective feed provenance used by decision evidence.

## What changed

- Every normal application route remains available in LIVE. The 0.9.6 global page-hiding/generic LIVE replacement behaviour has been removed.
- Shared provider-derived market/reference evidence (canonical markets, quotes, liquidity, mappings, status and timing provenance) can be consumed in both SIM and LIVE without being reclassified as SIM economic data.
- LIVE economic/execution pages are now mode-aware and fail honestly: real LIVE Execution Analysis, Results, Performance and Replay remain valid empty datasets until real activity exists, with no SIM economic/execution fallback.
- 0.9.6 LIVE decision simulations remain separate from real execution/order/fill/position/settlement evidence.
- LIVE Dashboard, Sports/Racing Overview and Monitor, Market Analysis, Execution Analysis, Performance, Results, Replay and Admin now retain their normal page structure and load the datasets appropriate to their ownership boundary.
- Manual Scan is permitted in LIVE for provider reads, canonical market updates and isolated LIVE decision evidence. The frontend synchronises the backend data context first and passes a monotonic generation; the scanner then freezes that requested context for the scan so a late mode transition cannot route evidence into the wrong sink.
- Betfair Admin configuration now supports a `DELAYED | LIVE` requested market-feed selector plus separate Delayed and Live App Key references.
- Betfair provider/runtime state distinguishes `requested_feed_entitlement` from `effective_feed_entitlement`. Selecting LIVE never by itself labels data LIVE.
- Runtime-effective Betfair feed quality is derived from fresh MarketBook evidence (`isMarketDataDelayed`) where available. Requested LIVE may therefore remain `UNKNOWN`, become `DELAYED`, or become `LIVE` according to provider evidence.
- A Betfair feed change increments a feed generation, marks the current runtime state transitional, and invalidates only bounded current Betfair quote/depth caches so an old delayed quote cannot be relabelled under a newly requested LIVE feed.
- Betfair market-feed configuration remains independent from global SIM/LIVE economic context and from the read-only LIVE Accounts session.
- Live App Key values are secret-store only and are redacted from provider diagnostics.

## Safety boundary

0.9.7 changes market-data presentation/provenance and UI data ownership only. It does not add an ExecutionProvider, provider-native order intent, order placement, cancel/replace, real position/fill/settlement, SIM policy, Betfair streaming architecture, Smarkets/BETDAQ integration or LIVE P&L authority.

`orders_write_capability` remains false and global LIVE execution remains locked.

## Validation

- Complete Python regression collection, run in isolated batches: **495 passed, 21 skipped** across **516 collected tests**.
- Dedicated 0.9.7 UI/feed-integrity suite: **14/14 passed**, covering requested/effective feed separation, delayed/live MarketBook evidence, bounded cache invalidation, secret redaction, full economic isolation and generation-safe manual LIVE scans.
- Frontend inline JavaScript syntax: **5/5 blocks passed**.
- Chromium rendered audit: **50/50 checks passed**, **0 page JavaScript errors**, covering full LIVE route availability, honest empty LIVE economic states, Betfair feed configuration, SIM/LIVE integrity, Dashboard runtime, Market Analysis, Replay, Performance and desktop viewport behaviour.
- ZIP integrity and extracted-package regressions are verified before release.

---

## 0.9.6

0.9.6 adds an isolated LIVE-context decision-evidence pipeline around ArbScanner's existing canonical market matching, commission-aware economics, executable-liquidity and strategy/risk logic.

## What changed

- The global presentation/data context (`SIM | LIVE`) is mirrored safely to the backend without changing the economic execution mode or unlocking LIVE.
- LIVE-context scanner decisions are routed to dedicated `live_decision_*` storage and do not create SIM opportunities, SIM positions, settlements or financial mutations.
- Decision evidence is revision-deduplicated: unchanged canonical books update bounded latest state instead of repeatedly accumulating simulated profit.
- Material decisions are retained separately from compact hourly rollups.
- Every decision records `application_mode=live`, `decision_type=simulated`, provider/venue legs, actual per-leg feed entitlement, timing provenance, quote age, receipt/source spread, executable stake, limiting leg, commission/economics, strategy/risk gates and decision compute time.
- Evidence is explicitly classified `OBSERVATIONAL` or `EXECUTION_GRADE`.
- Delayed Betfair legs remain observational. Zero execution-grade decisions is a valid result.
- The existing `simulate_equal_return` engine supplies hypothetical fill/profit evidence; no duplicate LIVE-specific stake/commission engine was introduced.
- LIVE Accounts remain provider-derived and read-only. Simulated outcomes never enter real account P&L or balances.
- Dashboard/Monitor/Analytics/Admin LIVE shells can display compact decision evidence with explicit `REAL ORDERS SENT = 0` messaging.
- Backend data-context writes carry a monotonic generation guard so late UI mode-sync calls cannot leave the scanner in the wrong context after rapid SIM/LIVE switching.

## Safety boundary

0.9.6 does not instantiate or require an ExecutionProvider for decision simulation and adds no order transmission path. Provider order-write capability remains false; no real position, fill, settlement or account mutation is created.

No Betfair Live App Key, streaming feed, Smarkets/BETDAQ integration, event-driven decision queue, SIM policy or provider-native order-intent dry-run is included.

## Validation

- Python regression suite, run in isolated batches: **481 passed, 21 skipped**.
- Dedicated 0.9.6 decision-evidence suite: **13 passed**.
- Frontend inline JavaScript syntax: **5/5 blocks passed**.
- Chromium rendered audit: **45/45 checks passed**, **0 page JavaScript errors**.
- Existing 0.9.5 clock/startup, 0.9.4 mode/account, 0.9.3 Racing/storage and earlier analytics/replay regressions remain green.
- ZIP/package validation is performed on the final packaged build before release.

---

## 0.9.5

0.9.5 decouples frontend-critical runtime behaviour from asynchronous backend startup and makes Dashboard read failures bounded and recoverable.

## Delivered

- Dashboard/world clocks now start before any backend await and use exactly one retained 1-second timer.
- Clock rendering is DOM-only and derives every tick from a fresh `Date`; date-rollover data refreshes are separated from clock rendering.
- The poll-countdown visual timer starts alongside the clock and is idempotent.
- Scanner/application startup now runs asynchronously after the frontend shell and UI timers are live.
- Dashboard core data is no longer blocked by optional analytics/Racing/results panels; optional panels launch independently.
- UI-facing Dashboard reads use bounded wait semantics and single-flight protection so a hung Python bridge call cannot accumulate duplicate requests.
- Read-side timeout means only that the UI stopped waiting; the generic bridge/mutation semantics are unchanged.
- Mode-generation guards are applied to bounded Dashboard reads so late opposite-mode responses cannot render into the current mode.
- Manual Dashboard and Sports Monitor refresh controls now always clear their busy state in `finally`.
- Backend-related periodic timers use retained/idempotent handles rather than being installed behind the long startup await chain.
- Visible LIVE Accounts refresh uses a retained idempotent timer.

## Safety boundary

- No provider polling was added to clock code.
- No SIM/LIVE financial architecture was changed.
- No Racing, storage, matching, staking, qualification or execution behaviour changed.
- LIVE account providers remain read-only.
- LIVE order placement remains structurally locked.

## Regression boundary

0.9.5 preserves the 0.9.4 single global SIM/LIVE authority and async mode guards, the 0.9.3 Greyhound/storage foundation, and the 0.9.2 read-only LIVE Accounts boundary.

## Validation

- Complete regression suite, executed in isolated batches: **468 passed, 21 skipped**.
- Dedicated 0.9.5 Dashboard runtime suite: **8 passed**.
- All **5/5** inline frontend JavaScript blocks pass `node --check`.
- Chromium UI harness: **43/43** checks passed with **0 page JavaScript errors**.
- Continuous rendered clock regression ran for **6 minutes**; digital minute display and analog hands advanced continuously with one retained clock/countdown timer and no page JavaScript errors.
- Rendered checks cover clock timer idempotency, clock advancement independent of backend reads, bounded single-flight reads, manual refresh recovery, SIM/LIVE mode integrity, Accounts, Market Analysis, Replay and Performance viewport behaviour.
- LIVE order placement remains structurally locked.

---

## 0.9.4

0.9.4 makes the global SIM/LIVE header control the single economic mode authority across the application.

## Delivered

- Removed the duplicate Dashboard and Admin mode-changing controls; both now show passive current-context badges.
- Global mode changes preserve the active route and update the shell synchronously before provider/account reads complete.
- Restored the saved mode before the first mode-sensitive startup load, eliminating the SIM-first/LIVE-later startup flash.
- Added mode-generation request guards so late SIM/LIVE account responses cannot render into the opposite mode.
- Separated SIM account aggregation from LIVE provider connectivity/freshness rules.
- SIM Accounts now aggregate the canonical virtual venue wallets and label them as SIM venue accounts rather than connected providers.
- LIVE account aggregation remains restricted to fresh connected real-provider snapshots and never falls back to SIM.
- Removed the recursive Sports/Racing LIVE Config account-load loop; config visibility sync is now a pure UI operation.
- Reduced duplicate LIVE placeholder/account calls by centralising route/mode orchestration.
- Added a monotonic SIM financial revision across wallet creation/reset, manual funding changes, budget reallocation, position reservation and settlement.
- SIM account/budget/reset mutations return/propagate canonical account state so Accounts, Dashboard, Admin and strategy views can refresh consistently.
- Background Dashboard SIM polling is suppressed while LIVE is selected.

## Safety boundary

- LIVE account providers remain read-only.
- No LIVE scanner/execution provider is wired through this release.
- No LIVE order placement/cancellation path is introduced.
- SIM and LIVE financial persistence remain isolated.
- The global LIVE execution lock remains authoritative.

## Regression boundary

0.9.4 preserves 0.9.3 Greyhound re-arm, Racing configuration PATCH semantics, timing evidence, bounded matched-market storage and retention behaviour.

## Validation

Validation was repeated against the extracted packaged release:

- complete 481-test regression collection executed in isolated batches: **460 passed, 21 skipped**;
- dedicated 0.9.4 SIM/LIVE mode/account integrity suite: **8 passed**;
- all **5/5** inline frontend JavaScript blocks pass `node --check`;
- Chromium UI harness: **39/39** checks passed with **0 page JavaScript errors**;
- explicit render checks cover the single global mode control, route preservation, rapid mode switching, and late SIM/LIVE response rejection;
- ZIP integrity and packaged version checks passed;
- LIVE order placement remains structurally locked.

---

## 0.9.3

## Release boundary

0.9.3 is a Racing reliability, evidence and persistence release built on 0.9.2. It does not add a new provider, Betfair live streaming, N-venue Racing optimisation or any LIVE execution path.

## Racing configuration integrity

- Racing settings use backend PATCH semantics: a missing field is not changed.
- Blank/uninitialised Racing controls cannot silently become numeric zero.
- Explicit operator-entered zero remains valid where the setting permits it.
- Racing execution numeric fields are validated before persistence.
- Racing controls are hydrated from persisted application state without requiring the Racing page to have been opened first.
- Racing Overview exposes a read-only configuration health state and warnings; warnings never mutate configured values.

## Greyhound execution evidence

- Racing Overview exposes a stable execution funnel: observed, complete book, positive, liquidity-capable, qualified, attempted, opened and missed.
- Historical qualification is sourced from canonical opportunity evidence rather than a single mutable matched-market status.
- Racing execution analysis can retain normalised miss reasons and timing evidence.
- A qualified Racing opportunity that cannot create its configured SIM MONITOR measurement is given a durable terminal execution record rather than being left unresolved indefinitely.

## Controlled Racing re-arm

Greyhound SIM MONITOR may re-arm after a transient miss only when all safeguards pass:

- the previous result is a re-arm-eligible transient miss;
- the cooldown has elapsed;
- the executable book revision has materially changed;
- the opportunity requalifies from scratch;
- no Racing MONITOR position is already open;
- no unresolved attempt exists;
- the configured maximum attempts has not been reached.

The retry gate is Racing-specific. Existing Sports pre-match and in-play retry/suppression behaviour is unchanged.

## Timing and provenance

- Canonical quote evidence now distinguishes provider-source timestamps from local receipt timestamps through `timestamp_quality`.
- Local capture time is no longer fabricated as a provider source timestamp.
- Racing qualification can record oldest/newest quote age, local cross-leg receipt spread, source-time spread where truly comparable, feed entitlement and transport.
- No universal sub-second freshness claim is made for delayed feeds.

## Bounded matched-market storage

`matched_markets` previously mixed latest state, high-frequency diagnostics and historical analytics. 0.9.3 separates those responsibilities:

- `matched_market_latest` stores bounded current state with observation counts and material fingerprints;
- repetitive identical evaluations update current state without appending another large verbose row;
- material state changes and optional low-frequency heartbeats may still create diagnostic history;
- hourly market, rejection, liquidity and Racing funnel rollups preserve compact historical analytics;
- per-scan qualification counters preserve scan diagnostics even when verbose rows are suppressed;
- legacy raw hours are finalised into canonical compact rollups before becoming prune-safe;
- default raw diagnostic retention is 48 hours and remains configurable;
- pruning is incremental, batched, restart-safe and performs no automatic `VACUUM`.

Deleting old raw rows primarily creates reusable SQLite pages. The physical database file may remain near its previous high-water mark until an explicitly requested manual compaction, while subsequent writes can reuse freed pages.

## Market Analysis compatibility

Long-period Market Analysis uses a hybrid compact/legacy read path during migration. Finalised hours are read from compact rollups; unfinalised legacy hours continue to read their raw evidence. A raw hour is not deleted until its required compact history has been finalised.

## Storage health

Database diagnostics now expose matched-market retention state including raw/latest row counts, oldest raw evidence, eligible rows, rows deleted, prune-safe horizon, SQLite page/freelist information and reusable bytes. Matched-market maintenance is independent from legacy snapshot maintenance so one cleanup failure cannot stop scanning or the other maintenance task.

## Unchanged safety and strategy behaviour

- SIM and LIVE economic data separation remains intact.
- 0.9.2 read-only LIVE account connectivity remains available.
- Global LIVE execution remains locked.
- No real external order path is added.
- No Racing ROI/liquidity threshold is lowered.
- No arb, commission, staking, recovery, hedge or settlement mathematics changes are included.
- No Smarkets or BETDAQ network calls are added.
- No Betfair live Stream implementation is included.

## Validation

Working-tree validation before packaging:

- complete 473-test regression collection executed in isolated batches: **452 passed, 21 skipped**;
- dedicated 0.9.3 Racing/storage suite: **7 passed**;
- all **5/5** inline frontend JavaScript blocks pass `node --check`;
- Chromium UI harness: **34/34** boolean checks passed with **0 page JavaScript errors**.

The same core regression/version/package checks are repeated against the extracted release archive before delivery.

---

## 0.9.2

## Release goal

Introduce ArbScanner's first real read-only LIVE account layer while preserving the strict SIM/LIVE financial boundary and keeping real-money execution structurally unavailable.

> **0.9.2 may observe real money, but it cannot move or trade real money.**

## Included

- New top-level **Accounts** section that follows the global SIM/LIVE context without changing route.
- **SIM Accounts** presents virtual provider wallets, virtual funding activity, SIM exposure, settled trading P&L and simulated commission evidence.
- **LIVE Accounts** reads real account state through a dedicated read-only `AccountProvider` contract with no order-placement methods.
- Betfair read-only account adapter uses the existing configured delayed-key/session credentials. Account state remains real account data while its market-data provenance remains `DELAYED`.
- Matchbook read-only account adapter uses the existing configured account/session credentials and normalises wallet/account activity where provider semantics are sufficiently clear.
- BETDAQ and Smarkets are registry-driven `pending_api` providers in this release: visible as awaiting API access, with no network client or fabricated values.
- Canonical `AccountSnapshot` includes account/provider identity, currency, available/balance/exposure fields, data quality, semantics, timestamps, connection state and safe provenance.
- Canonical `AccountActivity` retains provider-native classification while mapping only sufficiently clear activity into deposit, withdrawal, settlement, commission, adjustment or other categories.
- Provider-declared accounting metric support prevents generic analytics from hard-coding Betfair/Matchbook financial semantics.
- Current account KPIs are separated from period KPIs. Unsupported provider history is shown as unavailable rather than inferred.
- Reconciliation is calculated only when anchored balance history and all required transaction classifications are available.
- Multi-currency account values are never silently summed without an FX layer.
- Failed account reads retain the last valid snapshot, mark it stale/error and never substitute a false zero balance.
- Provider runtime owns account refresh cadence; Accounts/UI consumes cached runtime state and may refresh visible LIVE context every 30 seconds.
- Account snapshot health and account-history health are tracked independently.
- Compact read-side LIVE account audit events record refresh/failure/reconciliation diagnostics without credentials or session tokens.
- Provider-scoped secret values are redacted from account diagnostics; credential/session values are never intentionally returned to the frontend.
- Dashboard receives compact mode-aware account context; Sports/Racing Config show read-only LIVE account context while hiding SIM wallet/reset controls in LIVE mode.
- Admin remains provider/configuration owner and exposes account refresh/history-cache controls without duplicating the Accounts financial view.
- Dedicated LIVE account persistence (`live_accounts`, snapshot history, activity and audit evidence) remains physically/logically separate from SIM wallets and ledgers.

## Safety boundary

- `AccountProvider` has no place/update/cancel/replace order methods.
- Accounts is not given an execution provider.
- LIVE account reads do not unlock LIVE execution.
- `orders_write_capability` remains false for every provisioned provider.
- BETDAQ/Smarkets pending providers perform no network I/O.
- No LIVE order, cancellation, replacement, hedge or bankroll-transfer path is added.
- The global LIVE execution lock remains authoritative.

## Explicitly out of scope

- Betfair Live App Key activation or realtime market feed.
- Smarkets API implementation.
- BETDAQ API implementation.
- LIVE market scanning or LIVE positions/results/performance.
- Order placement, cancellation, replacement, partial-fill handling or execution reconciliation against a real venue.
- Deposits/withdrawals initiated by ArbScanner.
- Cross-currency FX aggregation.

## Compatibility

0.9.1 provider/liquidity analytics, strategy behaviour, SIM execution, settlement, replay and the 0.8.45 viewport/CSS contract are preserved. Existing local `secrets.json` remains the credential source; no new frontend/database credential store is introduced.

---

## 0.9.1

## Release goal

Make Market Analysis automatically reflect enabled provider-registry venues and add liquidity evidence without introducing provider-specific UI branches or unbounded raw depth history.

## Included

- Provider-registry-driven Market Analysis venue summary with `active`, `pending`, `stale`, and `disconnected` analytics states. Disabled venues are omitted.
- Current fresh top-of-book/top-3 venue depth with quote/feed provenance; stale current depth is excluded from executable totals.
- Canonical top-3 `DepthLevel` support in quotes/legs. Betfair and Matchbook populate up to three BACK/LAY levels where available.
- Matchbook market-price requests move from depth 1 to depth 3.
- Bounded `latest_depth_snapshots` storage: current levels replace prior levels for the same provider/market/selection.
- Compact hourly depth and liquidity-opportunity rollups; no append-only raw depth history.
- Per-opportunity liquidity evidence: maximum executable stake, limiting provider/selection/side, liquidity-capable flag/reason, depth snapshot, and quote age at qualification.
- Market leaderboard adds active venue count, average executable stake, and average available top-3 depth.
- Weekly heatmap adds available depth, average executable stake, liquidity-capable opportunities, and liquidity rejection rate.
- Market funnel is now `Observed → Positive → Liquidity-capable → Qualified → Attempted → Executed → Settled`. Existing Qualified semantics are unchanged.
- SIM liquidity retains its true feed provenance; SIM execution does not relabel observed market liquidity as synthetic/LIVE.
- Greyhound discovery summary is venue-generic rather than Betfair/Matchbook-specific.

## Explicitly unchanged

- Opportunity strategy and detection rules.
- Existing liquidity qualification thresholds/policy.
- Staking, SUPERBET, recovery and settlement logic.
- Provider integrations: no Smarkets or BETDAQ network calls.
- LIVE order placement: remains locked/unavailable.

## Storage principle

Operational depth is bounded current state. Historical analytics use compact hourly aggregates, while opportunity records preserve the specific liquidity evidence that informed qualification. This avoids rebuilding the pre-0.8.34 unbounded snapshot-growth problem.

---

## 0.9.0

## Release intent

0.9.0 prepares ArbScanner for additional exchanges and eventual controlled LIVE execution without integrating a new provider or enabling real-money trading. It is deliberately a foundation release: adding Smarkets or BETDAQ after this point should be an adapter/integration task rather than another cross-application redesign.

## Canonical SIM / LIVE boundary

- Economic operating modes are **SIM** and **LIVE** only.
- Legacy `monitor`, `watch`, `paper`, `simulate` and historical research-sim economic values remain readable as SIM compatibility aliases.
- New economic writes use canonical `sim` or `live`.
- Existing research/timed-recheck `sim_runs` may remain research evidence, but there is no SIM wallet/account/execution mode.
- SIM and LIVE may observe the same market but cannot share mutable balances, reservations, orders, fills, positions, recovery actions, settlements or P&L.
- Dedicated LIVE tables provide physical separation in addition to logical mode attribution.
- Reset/cleanup operations preserve LIVE persistence and legacy LIVE audit rows.

## Market-data provenance

Market data is no longer conflated with economic execution mode. Canonical quote/market/leg records can retain provider, venue, delayed/live/replay entitlement, transport, source timestamp, receive timestamp/age and source state/version. Future LIVE eligibility must fail closed for delayed, replayed, stale or unhealthy evidence.

## N-venue market matching

The historical primary-provider + one-counterpart matcher has been replaced by deterministic canonical clustering across 2..N compatible venues.

- one canonical market can retain Betfair, Matchbook, Smarkets, BETDAQ and future providers together;
- provider input/registration order does not determine canonical identity;
- compatible provider markets are not silently discarded when a third/fourth venue exists;
- settlement compatibility remains part of matching;
- the existing opportunity engine can evaluate all retained venue combinations and select the best eligible economics.

Existing Betfair/Matchbook two-provider behaviour remains covered by regression tests.

## Provider runtime

A provider runtime registry now owns provider registration, runtime profile, adapter construction, static capability, session state and health. Scanner market-data discovery is registry-driven rather than directly constructing exactly Betfair and Matchbook.

Runtime profiles separate concepts such as:

- feed entitlement;
- market-data transport;
- trading access;
- execution enablement;
- credential profile;
- stale-quote threshold;
- request timeout/rate-limit metadata;
- stream/domain enablement and fallback policy.

Transport selection remains an adapter responsibility rather than a normal operator control.

## Accounts, Replay and analytics

- SIM allocation wallets can represent arbitrary venues without redistributing existing capital when a provider is added.
- New providers receive only explicitly configured SIM capital (including zero).
- Replay accepts canonical `venue_balances`; the older `exchange_balances` field remains a compatibility alias.
- Performance account/capital aggregation and Market Analysis venue coverage can consume arbitrary venue IDs.
- Sports/Racing execution analytics continue using canonical venue/pair identities.
- UI provider/account rendering and SIM budget controls are venue-driven while current Betfair/Matchbook-specific credential forms remain intentionally provider-specific.

## Credentials and sessions

Provider-scoped credential/profile storage is available alongside the existing Betfair/Matchbook compatibility projection. Long-lived credentials and runtime session/health state are separate concepts so future adapters can own login, expiry, refresh, keepalive, heartbeat and reconnect behaviour independently.

## LIVE order safety foundation

0.9.0 does **not** submit external orders. It establishes the boundary future LIVE adapters must use:

1. create immutable `position_id / leg_id / attempt_id / client_order_id` before I/O;
2. durably persist a LIVE order intent before transmission;
3. record submission attempt/result state;
4. treat post-transmission timeout/disconnect as `UNKNOWN`, never as an assumed failure;
5. reconcile provider state before any retry or further unsafe execution.

Dedicated LIVE persistence includes accounts, order attempts/orders, fills, positions, recovery, settlements, account movements and reconciliations.

LIVE preflight is fail-closed and checks provider/runtime readiness below the global master lock. 0.9.0 reports `allow_new_positions = false` while retaining the conceptual distinction that future runtime shutdown/degradation may still need to manage existing exposure.

## Service-boundary readiness

Provider/core contracts are designed so they can later cross an RPC boundary without changing economic semantics:

- only canonical serialisable request/response data crosses the provider contract;
- provider-native clients/SDK objects remain inside provider adapters;
- provider sessions/transports are provider-owned;
- database handles are not contract arguments;
- stable provider/venue/order IDs are used;
- canonical health/account/order-reconciliation structures are JSON-safe.

The runtime remains in-process in 0.9.0. **No gRPC, gRPC-Web, Protobuf or service decomposition is introduced.**

## Synthetic future-provider shapes

Smarkets- and BETDAQ-shaped provider specifications are used only for architectural/regression tests. They make no network calls, require no credentials and are not part of the default runtime provider set.

## Migration / compatibility

- Existing 0.8.45 SIM data is preserved.
- Historical Monitor-era economic records are interpreted as SIM on read rather than destructively rewritten.
- Legacy `exchange` field names remain compatibility names where a mass schema rename would add migration risk; canonical interfaces use provider/venue identity.
- Existing Betfair/Matchbook credential/UI paths remain operational.
- The 0.8.45 canonical Analytics viewport CSS contract is retained rather than layered over.

## Explicitly not included

- no Smarkets API/network integration;
- no BETDAQ API/network integration;
- no Smarkets/BETDAQ production credentials;
- no new strategy/qualification/staking/SUPERBET/recovery behaviour;
- no new market/Asian Handicap settlement logic;
- no real LIVE balances/orders/fills/settlements;
- no gRPC/Protobuf dependency;
- no AWS/cloud architecture.

## Release boundary

**0.9.0 converts ArbScanner from a two-provider SIM-era runtime into an N-venue, provider-driven, service-boundary-ready architecture with strict SIM/LIVE economic separation and fail-closed LIVE-order/reconciliation foundations. Existing 0.8.45 Betfair/Matchbook SIM strategy behaviour and Analytics viewport integrity are preserved. LIVE remains locked and no external real-money order path exists.**

---

## 0.8.45

## Release intent

0.8.45 fixes the recurring Market Analysis and Replay desktop-fit problem at its source. The frontend had accumulated several generations of viewport-specific CSS from 0.8.36 through 0.8.44, including competing `calc(100dvh - Npx)` rules and fixed Market/Replay canvas heights. Those rules happened to fit the original acceptance viewport but left dead space on taller desktops and could clip content on shorter desktop windows.

No opportunity detection, qualification, execution, staking, scaling, recovery, settlement, commission, provider integration, account logic or LIVE behaviour changes. LIVE remains locked.

## Root-cause cleanup

- Removed obsolete Replay/Market height patch layers rather than adding another override on top.
- Removed the legacy v0.8.42 Replay-density and Market-discovery standalone style patches.
- Removed obsolete 225px/215px Replay canvas sizing and the historical fixed Replay review row template.
- Removed the old 300px global timeline height and normalised the compact discovery baseline to 158px.
- Removed the stale `!important` Replay review-shell sizing that prevented later layouts from owning the available height.

## Canonical desktop viewport contract

Market Analysis and Replay now use one desktop sizing owner:

- the active Analytics page measures the real application header height;
- the Analytics page owns the remaining `100dvh` height;
- the Market/Replay pane owns the remaining page area;
- fixed information sections keep stable minimum sizes;
- the Market weekly heatmap and Replay timeline are the elastic regions that absorb extra vertical space;
- short desktop windows reflow vertically instead of clipping content behind `overflow:hidden`.

The contract is activated only for Market Analysis and Replay, so Performance/Results/Execution/Scenarios retain their existing layout behaviour.

## Market Analysis

- The 10-row leaderboard remains compact and stable.
- Discovery cards remain 158px and fully visible.
- The weekly heatmap expands to use available height on tall desktop windows instead of leaving a large blank area below the page.
- At the standard 1568x959 acceptance viewport, the existing compact heatmap/discovery proportions remain intact.
- At 2048x1249, the page fills the viewport cleanly with the heatmap taking the additional space.
- Short desktop windows fall back to vertical page scrolling rather than clipping the discovery cards.

## Replay

- The 132px timeline remains the minimum/standard compact height.
- On taller desktop windows the timeline expands to use the available page height.
- Timeline marker lanes, settlement lanes, axis, tick labels and playhead geometry now derive from the live canvas height instead of fixed pixel coordinates.
- Replay re-renders marker geometry after a window resize.
- The review shell is contained on fitted desktops and reflows normally on shorter desktops.

## Regression / acceptance

0.8.45 extends rendered Chromium acceptance to cover:

- existing 1568x959 desktop fit;
- 2048x1249 tall-desktop Market Analysis fill;
- 2048x1249 tall-desktop Replay fill and elastic timeline;
- 1280x780 short-desktop Market Analysis vertical reflow;
- 1280x780 short-desktop Replay vertical reflow;
- 1120x800 compact horizontal containment;
- no page JavaScript errors.

Source-level regression checks also protect the canonical viewport owner and prevent the removed legacy fixed-height patch tokens from being reintroduced.

## Release boundary

This is a frontend layout/CSS cleanup only. Financial metrics and the 0.8.44 Performance decision dashboard are unchanged. Betfair/Matchbook behaviour is unchanged. LIVE remains locked.

---

## 0.8.44

## Release intent

0.8.44 turns Analytics → Performance into ArbScanner's portfolio-level decision dashboard while preserving all existing trading and financial behaviour. It also completes the latest Market Analysis and Replay spacing work requested after 0.8.43.

No opportunity-detection, qualification, execution, staking, scaling, recovery, settlement, commission, provider-integration or LIVE-activation behaviour is changed. LIVE remains locked.

## Performance dashboard redesign

Performance now answers portfolio-level questions about profitability, capital efficiency, edge capture, domains, markets and venues without becoming an execution-debugging screen.

### Core KPIs

The primary strip is now:

- Net P&L;
- Portfolio ROI — realised settled P&L / selected-period starting capital;
- Capital deployed — settled position capital actually committed;
- Return on deployed — realised settled P&L / settled capital deployed;
- Captured edge — realised settled P&L / expected profit recorded at execution for the same settled positions;
- Positions executed with attempted count as supporting context.

Captured edge is value-weighted through aggregate realised/expected economic value rather than a naïve average of percentages.

### Time and analytical filters

Performance supports Today, 7 days, 30 days, 90 days, All history and Custom range plus Domain, Type, Sport, Market, Venue and Venue-pair filters. Actual/Expected remains an explicit basis switch. SIM/LIVE separation is unchanged; LIVE Performance uses the isolated LIVE provider boundary and never falls back to SIM evidence.

### Main trend

One primary trend is shown at a time to keep the page readable. It can switch between:

- Cumulative P&L;
- Period P&L;
- Portfolio ROI;
- Return on deployed;
- Captured edge.

### Sports vs Racing

A compact domain comparison shows realised P&L, positions, capital, return on deployed, qualified edge, captured edge, attempt→execution conversion, recovery rate and supporting funnel counts. Clicking a domain opens the matching domain-specific Execution Analysis.

### Market performance

Canonical sport/market groups are ranked by realised performance and expose Observed, Qualified, Attempts, Executed, Settled, Conversion, Capital, Net P&L, ROI, Captured Edge and Recovery. Every rate is presented alongside its underlying settled/sample count. Market rows can drill toward Market Analysis.

### Venue performance

Venue analytics use the generic Venue concept introduced in 0.8.43. Current Betfair/Matchbook rows expose only meaningful stored evidence such as positions involving the venue, submitted/executed legs, fill/partial/rejection rates where recorded, average executed stake, capital deployed, settlement P&L contribution and recovery events.

Venue P&L is explicitly a settlement contribution. It is not presented as independent arb-position profit; canonical position P&L remains the portfolio source of truth.

### Venue-pair and directional performance

Venue-pair economics are position-level so a two-leg Betfair↔Matchbook position is counted once for capital and P&L. Directional evidence such as BACK Betfair → LAY Matchbook remains distinguishable where planned-leg evidence is available.

### Performance funnel

A consistent opportunity-ID cohort reports:

Observed → Positive → Qualified → Attempted → Executed → Settled

with previous-stage conversion and overall Observed→Executed conversion. Detailed execution failure reasons remain in Sports/Racing Execution Analysis.

### Capital efficiency and recovery

Performance now surfaces available/reserved capital, average capital per settled position, peak deployed, average utilisation, profit per £1,000 deployed, recovery positions/rate, recovery qualified-edge value, final recovery P&L, edge lost and execution leakage.

## Market Analysis spacing

- Discovery cards reduced from 190px to 158px on the standard desktop layout.
- All three remain equal height and retain their full metrics without clipping.
- Reclaimed vertical space is given to the weekly heatmap; its rendered analytics card is approximately 250px high on the acceptance viewport.
- The full Market Analysis page continues to fit the standard desktop viewport without horizontal or vertical page scroll in the rendered acceptance fixture.

## Replay spacing

- Top filter padding and stream-card density are reduced.
- Spacing between stream summaries, Sports in this period, timeline controls, period highlights and selected-position detail is made more consistent.
- Timeline canvas is 132px on the standard desktop layout, giving the chronological review more useful visual room while keeping the complete Replay review inside the viewport.
- Sport-only period indicators and timeline width containment from 0.8.43 are preserved.

## Data integrity

- Performance financial metrics continue to use canonical settlement-time Monitor evidence.
- Results totals remain the position-level financial source of truth.
- Sports/Racing, market and venue-pair attribution are derived without duplicating position capital/P&L.
- Venue settlement contributions use the canonical `realized_by_exchange_json` produced by Monitor settlement.
- Existing exchange-account history remains available as supporting canonical evidence even though it is no longer a primary chart.
- SIM and LIVE remain isolated end-to-end.

## Regression / acceptance

0.8.44 adds coverage proving:

- realised P&L and deployed capital reconcile across Sports + Racing;
- captured edge uses aggregate economic values;
- venue-pair capital/P&L does not double-count a multi-leg position;
- Betfair and Matchbook settlement contributions reconcile to canonical position P&L in controlled fixtures;
- 90-day and venue-pair filters are accepted;
- Market discovery panels render at 158px and remain unclipped;
- the weekly heatmap receives the reclaimed height;
- Replay timeline renders at 132px and remains viewport-contained;
- Performance renders six KPIs, one primary metric chart, domain cards, market/venue/pair evidence, funnel and capital/recovery summaries without horizontal overflow.

## Release boundary

This is an analytics/dashboard and layout release only. It introduces no new venue integration, no new strategy, no execution/staking/recovery/settlement change and no LIVE activation.

---

## 0.8.43

## Release intent

0.8.43 prepares ArbScanner's shared trading and analytics models for future trading venues while preserving the existing Betfair/Matchbook behaviour and keeping LIVE execution locked. It also moves Execution Analysis into the Sports/Racing domains and completes the latest Dashboard, Market Analysis and Replay viewport/clarity work.

No new bookmaker, broker or exchange integration is included. No qualification, staking, SUPERBET, hedge/recovery or settlement algorithm is changed.

## Domain-specific Execution Analysis

- Removed the global Execution Analysis entry from Analytics navigation.
- Added Execution Analysis under Sports and Racing.
- Both navigation entries use one shared Execution Analysis implementation; the browser supplies an explicit domain context rather than maintaining duplicate analytical pages.
- The analytics API accepts `domain=all|sports|racing` and enforces domain separation server-side:
  - Sports = pre-match + in-play.
  - Racing = racing only.
- Sports Execution Analysis excludes Racing evidence; Racing Execution Analysis excludes Sports evidence.
- Replay/deep-link execution detail resolves the execution domain and opens the shared view in the correct Sports/Racing context.
- Existing execution evidence and metric definitions are preserved.

## Venue/provider-neutral trading architecture

Added `arbscanner.venues` as the authoritative provider/venue contract layer.

### Venue identity and classification

- Stable `venue_id`, `venue_name`, `venue_type` and `provider_id` concepts.
- Venue types currently supported by the canonical model: `EXCHANGE`, `BOOKMAKER`, `BROKER`.
- Optional `underlying_venue_id` preserves the destination behind a broker where that information is available.
- Betfair and Matchbook register as `EXCHANGE` providers.

### Provider capabilities

Providers expose explicit capabilities rather than requiring shared core code to infer behaviour from provider names or from an Exchange lifecycle. The capability model can represent market discovery, streaming/polling prices, BACK/LAY support, cancellation, partial fill/FOK behaviour, price constraints, stake limits, executable capacity, commission/fees, account/reserved balance, order status, settlement and Sports/Racing phase support.

Synthetic BOOKMAKER/BROKER regression providers prove that a provider can legitimately omit LAY, cancellation and partial-fill capabilities.

### Canonical models

0.8.43 introduces/extends provider-neutral structures for:

- canonical venue/provider identity;
- canonical event/market/selection identity above provider-native IDs;
- quotes with venue/provider identity, side, displayed/executable price semantics, executable capacity and capacity source;
- venue-neutral position legs with optional economic-exposure representation;
- provider-neutral order intent;
- provider-neutral execution result/status;
- provider-neutral settlement status/result;
- venue/provider account identity;
- optional broker underlying-venue identity.

Existing `Quote`, `ExchangeMarket`, `Leg`, execution-plan and fill structures retain their legacy fields for compatibility while also carrying the new canonical metadata. Existing Betfair/Matchbook constructors and operational paths therefore do not require a data migration or strategy rewrite.

### Price and capacity semantics

- Canonical quote structures distinguish displayed price from executable price.
- Executable capacity is explicit and does not require every future provider to expose an exchange order-book depth.
- The capacity source remains available so an exchange available size and a bookmaker/broker maximum accepted stake can share a canonical capacity concept without losing their native semantics.

### Position/execution compatibility

- Generic position-leg records do not require an Exchange-specific lifecycle and include optional economic exposure so the canonical model does not require every future arb topology to be BACK/LAY.
- Current Betfair/Matchbook opportunity algorithms remain unchanged in 0.8.43.
- Paper fills and hedge instructions preserve venue/provider/underlying-venue identity.
- Venue-level capital and outcome-P&L helpers use explicit canonical venue IDs; broker/bookmaker records are not collapsed through the legacy exchange display field.
- Legacy exchange-level helpers remain available for current wallet/account compatibility.

## Analytics and Replay compatibility

- Execution rows expose canonical venue identities in addition to legacy exchange information.
- Execution detail and Replay use provider-neutral venue display fallback where generic venue metadata exists.
- The API exposes a read-only provider/venue capability manifest; it contains no order-placement surface and explicitly reports LIVE execution disabled.
- Replay continues to operate from canonical ArbScanner position/event evidence rather than using provider-specific records as its basic structure.

## Dashboard clarity

- The 24-hour win-rate formula remains `wins / (wins + losses)`.
- Supporting text now exposes the denominator directly, e.g. `98 wins · 32 losses · 130 decided`.
- The helper explains that break-even, void and open positions are excluded from the win-rate denominator.
- The same decided-position semantics are retained across Dashboard, Results and Replay.

## Market Analysis viewport fit

- Market filters now live in the main Analytics header between the Market Analysis title/subtitle and Refresh button.
- The redundant full-width filter band is removed from the active layout, recovering vertical space without shrinking the discovery cards.
- Desktop layout fits both KPI rows, ten-row leaderboard, weekly heatmap and three 190px discovery panels inside the standard rendered viewport.
- Compact layouts reflow without horizontal page overflow.
- Existing multi-metric heatmap, sport selector, exchange comparator and discovery analytics remain unchanged.

## Replay period-review fit

- The main activity indicator is now Sports in this period only; the market/event tile rail has been removed from the primary layout.
- The compact backend period-activity index still retains market/event evidence for timeline/detail use.
- Timeline movement highlights the relevant sport tile(s); sport selection focuses the period review and All restores the complete period.
- Market/event information remains available through timeline events and selected-position forensic detail.
- Timeline/canvas width is constrained to the page container so long market names cannot expand the Replay page horizontally.

## Safety / release boundary

Explicitly not included in 0.8.43:

- Sportmarket, Pinnacle, SBOBet, Singbet or any other new venue integration;
- new API credentials or real-money accounts;
- LIVE trading;
- Asian Handicap/Totals implementation;
- new bookmaker-vs-exchange or bookmaker-vs-bookmaker opportunity algorithms;
- Greyhound execution changes or Horse Racing implementation;
- new staking, recovery or settlement algorithms;
- cloud/AWS infrastructure work.

SIM/LIVE separation is unchanged and LIVE remains locked.

## Regression coverage

0.8.43 adds coverage for:

- Sports/Racing server-enforced Execution Analysis domains;
- one shared Execution Analysis implementation/navigation contract;
- Betfair and Matchbook provider registration/capabilities;
- synthetic BOOKMAKER and BROKER provider representation;
- providers without LAY/cancellation/partial fills;
- broker and underlying-venue identity coexistence;
- canonical market IDs independent of provider-native IDs;
- venue-neutral position/account/order-intent models;
- unchanged legacy Betfair/Matchbook execution-plan stakes/sides/capital requirements;
- explicit venue identity in venue-level P&L;
- venue metadata on hedge instructions/fills;
- read-only provider manifest with LIVE disabled;
- Dashboard win-rate wording;
- Market filters in the header and full desktop Market Analysis fit;
- Replay sports-only period activity and timeline containment.

---

## 0.8.42

## Market Analysis

- Weekly heatmap now switches between Observations, Qualified opportunities, Executed positions, Settled profit, Return on deployed and Capital deployed.
- Heatmap sport selector is populated from the week data and metric/sport switching is client-side after one compact weekly payload.
- Hour tooltips carry the supporting observation, qualification, execution, deployment, realised P&L, ROI and most-active-sport context.
- Future/unobserved cells remain distinct from stored zero values and the current hour remains highlighted.
- Added compact financial hourly rollups so historical heatmap reads do not regroup raw quote/snapshot history. The current hour is the only live bounded canonical regroup.

## Exchange comparator integrity

- Betfair markets and Matchbook markets now measure distinct exchange-native market IDs discovered in the selected period, rather than the downstream matched-market cohort.
- Market overlap uses canonical event/market mappings observed on both venues.
- Repeated scans cannot inflate unique market coverage, and all-phase coverage deduplicates the same native market across pre-match/in-play observations.
- Scanner discovery now retains exchange-native identities before cross-exchange narrowing, including unmatched markets.
- Greyhound discovery additionally retains the stored racing catalogue evidence, including Betfair catalogue races that have incomplete/missing executable books and therefore never become matched markets.
- Custom-period comparator boundaries use exact first/last-seen timestamps rather than whole-hour inclusion.

Historical note: releases before 0.8.42 did not retain every unmatched Matchbook native market ID. 0.8.42 performs a best-available backfill from matched-market source IDs and persisted Racing discovery diagnostics. Exact full native discovery coverage is retained from 0.8.42 onward; missing historical native IDs are not invented.

## Replay period review

- Replay remains a period-review workspace with exactly: 7 days, 24 hours, Today and Custom period.
- Added dynamic sport and market activity tiles for the selected period, including position count, W/L and realised P&L context.
- The timeline playhead highlights the sport/market tiles active at that moment entirely client-side.
- Selecting a sport focuses the review; selecting a market focuses/jumps the timeline to that market; All activity restores the complete period.
- Replay API returns a compact period activity index with sport/market time ranges, position IDs and settled W/L/break-even/P&L summaries; the tile UI, filtering, jumping and playhead highlighting consume that index end-to-end.
- Timeline height is reduced and the complete desktop review fits the standard rendered viewport.
- Selected settled positions now show the actual canonical event result/winner where available.

## Results integrity

- Results now has an explicit Event result column. Canonical settlement outcome is displayed as the winner/result and is never inferred from whether ArbScanner made money.
- The current canonical settlement store retains the winner/outcome selection but not a reliable final score or full finishing order. 0.8.42 shows the real stored winner and deliberately does not fabricate score/placing detail that the source did not retain.
- If no canonical outcome is stored, the UI shows Result unavailable.
- Win / loss % is now calculated from the same visible Results cohort and the same active period/search/outcome/hedge filters.
- Break-even positions are excluded from the W/L percentage denominator and shown separately.
- The previous independent 24-hour dashboard cohort has been removed from the Results helper entirely, so there is one canonical W/L path rather than a later runtime override.

## UI polish

- Market Analysis discovery cards are now 190px equal-height summary panels with more breathing room.
- Replay KPI, stream, activity, timeline and detail sections were vertically rebalanced to keep the richer period review readable without unnecessary scrolling on the standard desktop viewport.

## Safety / non-scope

- No changes to qualification thresholds, staking, liquidity checks, market budgets, hedge reserve, SUPERBET sequencing, balance/recovery, emergency hedging or settlement calculations.
- SIM/LIVE isolation is unchanged.
- LIVE remains locked.

---

## 0.8.41

0.8.41 is a focused analytics usability build on top of 0.8.40. It does not alter qualification, staking, execution, SUPERBET, settlement, hedge reserve, SIM account behaviour, storage architecture, or LIVE safety boundaries.

## Market Analysis

- Adds a second five-tile exchange-comparator row: Betfair markets, Betfair opportunities, Matchbook markets, Matchbook opportunities, and Market overlap.
- Market counts use distinct event + market identities with stored source-exchange evidence; repeated price scans and the same market moving from pre-match to in-play do not inflate all-stream figures.
- Opportunity counts use stored non-demo opportunity records where the exchange contributes a planned leg.
- Market overlap is shown as overlap / union plus percentage.
- Re-expands the three discovery cards with equal height, readable two-column label/value rows, consistent padding, and anchored actions.
- Weekly heatmap gets a wider day/date gutter, taller cells, stronger current-hour treatment, and explicit future/unobserved states instead of misleading zeroes.

## Replay / Period Review

Replay is now designed to answer “what happened over this period?” rather than treating playback itself as the main product.

- Time filters are exactly: 7 days, 24 hours, Today, Custom period.
- Primary review: Positions, Won, Lost, Net P&L.
- Secondary review: Capital deployed, Return on deployed, Best position, Worst position.
- Adds Pre-match / In-play / Racing period breakdown cards with opened, won/lost, settled, deployed, ROI and emergency hedge counts.
- Removes Legs placed from the primary UI.
- Adds period highlights for most active market, largest deployment, emergency hedges and SUPERBET positions.
- Reduces the timeline vertically and keeps it as chronological evidence with open/settlement markers and P&L labels.
- Playback cursor no longer mutates the period headline totals.

## Safety

- SIM/LIVE data boundaries remain unchanged.
- Racing LIVE remains hard-locked.
- No execution or financial-accounting behaviour changed.

---

## 0.8.40

## Purpose

0.8.40 is a focused Performance-page redesign on top of the 0.8.39 baseline. It does not alter execution logic, storage architecture, settlement logic, SIM/LIVE isolation, SUPERBET behavior or Racing safety boundaries.

## Performance layout

The page is now structured as:

1. Four primary KPIs: Current capital, Period P&L, Current deployed, Return on deployed.
2. A compact canonical account-basis strip.
3. Three equal charts in one row: Portfolio equity, Exchange capital, Daily performance.
4. Four supporting efficiency metrics: Average deployed, Peak deployed, Average utilisation, Settled bets.

The old Trend Summary section and dedicated Capital Deployed chart are removed.

## Exchange capital

- Betfair and Matchbook history comes from canonical SIM account snapshots.
- All portfolios / all streams displays total exchange equity.
- Sports, Racing or single-stream filters display that selected allocation by exchange.
- The chart never fabricates balance history before account snapshots existed.
- Current available and reserved balances are shown beneath the chart as account context.
- Overall position P&L remains portfolio-level; the UI does not invent per-exchange profit attribution.

## Chart treatment

- Portfolio equity: restrained line chart, sparse endpoint marker, hover detail.
- Exchange capital: two clean exchange lines with hover detail.
- Daily performance: green/red daily P&L bars plus one cumulative P&L line.
- Removed heavy area fills, decorative plot surfaces, floating callouts and peak-vs-average bands.

## Validation

- Full regression suite: 366 passed, 21 skipped.
- Python compilation: passed.
- Inline JavaScript syntax: passed via `node --check`.
- Chromium rendered-page audit: passed, including equal three-column Performance layout and viewport fit.
- No database or wallet reset required.

---

## 0.8.39

0.8.39 is a focused visual correction to the Performance analytics page. The previous 0.8.38 change modified SVG structure but remained too visually similar to the old charts. This release replaces that treatment with an explicitly different chart system and verifies the rendered result in Chromium.

## Performance chart redesign

- Capital/equity chart now uses a stronger filled area, a 4px primary trend, a dedicated plot surface, and visible START/CURRENT callouts.
- Capital deployed chart now separates average deployed from peak deployed with a filled peak-to-average band, strong average line, dashed peak line, and current-average callout.
- Profit Trend is now full-width beneath the two primary charts.
- Profit Trend combines daily positive/negative bars with a strong cumulative P&L area/line and cumulative callout.
- Daily bar values are displayed when the selected range is compact enough to remain legible.
- Chart headers now use a compact highlighted summary pill.
- Custom in-app hover tooltips show the date and exact chart values instead of relying only on native SVG title tooltips.
- Light and dark chart surfaces are explicitly styled.
- The old desktop rule that caused Profit Trend to occupy only one half-column is overridden; the third chart now spans the full analytics width.

## Release validation

- Chromium render audit checks three Performance SVGs, filled plot areas, plot surfaces, endpoint/cumulative callouts, daily bars, and the full-width Profit Trend layout.
- Existing 0.8.38 weekly heatmap, Replay simplification, bounded storage, SIM/LIVE isolation, execution and account behavior are unchanged.
- No database or wallet reset is required.

---

## 0.8.38

## Focus
0.8.38 is a focused UI-polish release built on the 0.8.37 stabilization baseline. It does not change execution, account, storage, feed, SUPERBET, or LIVE safety behavior.

## Market Analysis
- Replaced the single-day hourly strip with a full **Monday -> Sunday / 24-hours-per-day** weekly heatmap.
- The weekly matrix contains 168 local-time hourly cells and supports previous/current-week navigation.
- Kept the compact hourly-rollup backend path so changing week or metric does not query the raw historical quote store.
- Heatmap metrics remain explicit: scan observations, unique markets, net-positive opportunities, qualified opportunities, executed positions, and settled P&L.
- Split Sports discovery into compact **Pre-match** and **In-play** cards and retained a matching compact **Greyhound discovery** card.
- Reduced leaderboard/discovery/heatmap vertical density so all three discovery summaries remain visible in the desktop Market Analysis workspace.

## Performance
- Modernized all three Performance charts with clearer hierarchy and less visual flatness.
- Added subtle area fills, stronger primary series, cleaner grids, focused endpoints, and useful peak/reference annotations.
- Capital deployed now distinguishes the average and peak reference more clearly.
- Profit trend uses cleaner positive/negative bars plus cumulative-profit emphasis.
- The rendered audit verifies three charts, area layers, focus points, and daily bars.

## Replay
- Removed the **Running P&L chart** completely.
- Replay now focuses on playback, the position/settlement timeline, timeline P&L labels, and selected execution detail.
- Removed the obsolete Running-P&L stylesheet and DOM-update code rather than leaving dead UI generations behind.

## Stabilization fixes found during 0.8.38 verification
- Restored the canonical Results loader and 24-hour Results integrity loader that had been accidentally removed during earlier duplicate-function cleanup.
- Restored one canonical fill-role audit helper used by Results and Replay, preserving Base / SUPERBET / Balance-recovery / Emergency-hedge distinctions.
- Removed obsolete single-day heatmap CSS and retired Replay-P&L CSS families.
- Made a historical Results analytics fixture deterministic across wall-clock dates; no production settlement semantics were changed.

## Safety / compatibility
- SIM and LIVE remain isolated.
- LIVE remains read-only/integration-pending.
- Racing LIVE remains hard-locked.
- SUPERBET remains MONITOR-first.
- Bounded quote storage, account reconciliation, database compaction, and feed connectivity architecture are unchanged.
- No database reset or wallet reset is required.

## Verification
- 361 tests passed, 21 skipped.
- Python compilation passed.
- All inline browser JavaScript passes `node --check`.
- Chromium rendered-page audit passes Dashboard, Results, Market Analysis, Replay, Performance, active-position badge, and compact-width checks.
- Weekly Market Analysis render verifies 168 cells, 7 days, 24 hour headers, 10 leaderboard rows, and three discovery cards.

---

## 0.8.37

0.8.37 is a stabilization-only release. It does not add betting or execution features.

## Root causes fixed
- Closed a missing `@media (max-width:1050px)` brace that had caused a large part of the modern desktop stylesheet to apply only at narrow widths.
- Removed malformed `n...` selector prefixes from the prior CSS cleanup.
- Removed exact duplicate CSS rules and known dead styles for retired display/profile, world-clock and Replay account-card UI.
- Reduced redundant CSS declarations while preserving the existing visual cascade.
- Consolidated Market Analysis to one canonical loader and the dedicated lightweight heatmap path.
- Removed duplicate named JavaScript function declarations from accumulated patch layers.
- Removed dead Replay account-timeline calls after account cards were removed from Replay.

## Rendered UI verification
A headless Chromium render audit now verifies at 1568x959 and a compact desktop viewport:
- Dashboard fits without horizontal/vertical overflow and maintains consistent major-section gaps.
- Active Positions badge shows the canonical count and hides correctly at zero.
- Results Best/Worst values use positive/negative semantic colours.
- Market Analysis shows all 10 leaderboard rows, helper icons in the tile top-right and paired Sports/Greyhound discovery cards.
- Replay has no removed account/P&L KPI tiles, retains the P&L chart and fits the desktop viewport.
- Compact Dashboard does not horizontally overflow.

## Safety
Backend storage, account, SIM/LIVE isolation, SUPERBET, Racing MONITOR and connectivity behavior are unchanged except for the version identifier.

## Validation
- 358 tests passed; 21 skipped.
- Python compilation passed.
- All inline browser scripts passed `node --check`.
- CSS audit: one style block, zero malformed `n...` selectors, zero exact duplicate qualified rules.
- Chromium rendered-page audit passed Dashboard, Active Positions badge, Results colours, Market Analysis, Replay and compact-width overflow checks.

---

## 0.8.36

- Market Analysis leaderboard now shows the top 10 rows by the active sort.
- Removed the redundant Conversion / drop-off panel; added matched Sports and Greyhound discovery cards.
- Results Best / Worst values use positive/negative semantic colouring.
- Replay removes the redundant Running P&L KPI while retaining the full Running P&L chart.
- Market Analysis helper icons are pinned consistently to the top-right of KPI tiles.
- Dashboard spacing is redistributed across the available viewport rather than squeezing cards together.
- Dashboard account copy points funding changes to Admin; reconciliation delta is displayed at normal currency precision.
- Active Positions navigation count now honours its hidden zero state and uses the canonical Dashboard count.
- CSS consolidated to a single stylesheet; obsolete display-profile and dashboard fit/full selectors removed; exact duplicate rules eliminated.
- 0.8.35 storage, account, connectivity, SUPERBET and Racing safety boundaries are unchanged.

---

## 0.8.35

## Viewport and analytics integrity

### Dashboard
- Hard viewport-fit target for a normal maximised MacBook window.
- Dynamic vertical compaction and chart height allocation; no manual layout mode.
- Active Positions sidebar badge now uses the same canonical open-position count as Dashboard and Active Positions.

### Replay
- Removed large Betfair/Matchbook account-history tiles from the primary replay canvas.
- Important settlement P&L values are labelled directly on the timeline in green/red.
- Selected/current replay events always expose their financial outcome; dense labels remain available by hover.
- Replay timeline, Running P&L and compact position detail share the usable viewport dynamically.

### Market Analysis
- Added a dedicated hourly heatmap API and compact rollup tables.
- Today/Yesterday switching is cached and no longer reruns the complete Market Analysis workload.
- Heatmap metrics are explicitly separated into scan observations, unique markets, net-positive opportunities, qualified opportunities, executed positions and settled P&L.
- Funnel stages use one canonical opportunity cohort: observed, net-positive, qualified, execution attempted and position opened.

### Results
- Removed the standalone Break-even KPI; break-even count is supporting Settled context.
- Added Hedged Positions with Emergency Hedge supporting count.
- Hedging is derived from canonical fill roles, including balance/recovery and emergency hedge activity.

### Safety and compatibility
- Bounded quote storage and completed legacy cleanup architecture retained.
- SIM/LIVE data isolation retained.
- SUPERBET remains MONITOR-first.
- Racing MONITOR remains enabled; Racing LIVE remains hard-locked.
- No database, wallet or history reset is required.

---

## 0.8.34

## Scope

- Hedge reserve is explicitly a ring-fenced subset of each SIM market allocation. Admin shows total allocation, hedge reserve and normal deployable capital; normal entries cannot spend the reserve while hedge/recovery capital may use it.
- Dashboard account cards are read-only. Funding controls remain under Admin only; the Dashboard Performance block and manual Auto/MacBook/16:9/Fit/Full layout controls are removed in favour of automatic responsive layout.
- Betfair and Matchbook feed-enable flags are explicit operator controls and are protected from unrelated Admin saves. Disabled feeds are reported venue-by-venue rather than as generic connectivity failures.
- Replay identifies the first available SIM account checkpoint and clearly marks playback before account history began without inventing balances.
- Scenarios use Current SIM accounts by default, support Current market budget or Custom capital, align calendar presets with Performance, support detection-time or settlement-time selection, and show Actual SIM Performance plus selection-funnel/exclusion context.
- Controlled Admin database compaction is available after legacy snapshot cleanup completes. It checks integrity/free space, pauses the worker, creates a pre-VACUUM backup, compacts, verifies integrity again and resumes the worker.
- The bounded current-quote storage architecture introduced previously remains unchanged; compaction reclaims historical free pages but does not reintroduce unbounded raw quote retention.

## Safety boundaries

- No wallet/history reset is required.
- SIM and LIVE datasets/providers remain isolated.
- LIVE orders remain locked; Racing LIVE remains hard-locked.
- Sports/Racing accounting isolation, SUPERBET and existing execution safeguards are preserved.

---

## 0.8.33

0.8.33 is an operational scaling release prompted by a live database reaching ~9.6 GB with millions of append-only raw quote rows. It changes raw price persistence from unbounded history to a bounded current-quote store while preserving the canonical long-term research and financial ledgers.

## Bounded quote storage

- Fast price scans no longer append every runner quote to the legacy `snapshots` table.
- `latest_snapshots` keeps one current row per exchange / market / selection / side using UPSERT semantics.
- `snapshot_rollups` stores compact hourly observation counts for scanner-volume diagnostics.
- Opportunities, matched-market research, execution runs, Monitor positions, settlements, Results, Performance, Replay and account history remain in their existing canonical tables and are not pruned by this change.

## Legacy recovery

- The background worker incrementally removes old rows from the legacy append-only `snapshots` table in bounded batches.
- The newest 100,000 legacy rows are retained by default as migration-era forensic evidence.
- Cleanup reclaims SQLite pages for reuse without resetting the database or deleting trading history.
- The on-disk SQLite file may remain physically large until a future explicit compaction operation; reclaimed pages are reusable immediately inside the existing database.

## Scanner resilience

- A raw quote-storage failure no longer erases already-known Betfair / Matchbook feed status.
- Price evaluation can continue from successfully fetched venue data even if current-quote persistence reports a storage warning.
- Discovery and price-scan exception paths preserve venue-specific status instead of collapsing both feeds to unexplained `UNKNOWN`.
- Admin System Health shows the actual scan exception, bounded storage state, legacy cleanup progress, reusable page capacity and any current quote-storage warning.

## Upgrade safety

The new storage tables use a targeted additive migration. An otherwise-current 0.8.32 database does not rerun the full historical migration chain, which is important for very large existing databases.

No database reset or wallet reset is required.

---

## 0.8.32

0.8.32 completes the operator-side SIM bankroll controls introduced in 0.8.31.

## SIM account editing

- Replaces native browser `prompt()` / `confirm()` account actions with an in-app editor that works reliably in the packaged macOS pywebview application.
- Betfair and Matchbook SIM accounts support Add funds, Withdraw, Set balance and Reset funding adjustments.
- Funding changes remain audited and cannot withdraw capital reserved by open positions.
- Positive funding changes preserve the current portfolio allocation proportions rather than reverting to historic opening proportions.

## Market budgets and hedge reserve

Admin > Exchange Accounts now includes a SIM market-budget editor for:

- Pre-match Sports
- In-play Sports
- Greyhound Racing

Each market exposes Betfair and Matchbook budgets plus a monetary hedge reserve. Applying budgets reallocates current unreserved SIM equity between portfolio wallets without changing the total Betfair/Matchbook account equity or rewriting historical P&L. Open-position reservations are protected.

The hedge-reserve amount is converted to the existing per-market reserve percentage used by the execution engine, so the displayed pound reserve and executable free balance remain consistent.

## Safety boundary

SIM/LIVE provider isolation from 0.8.31 is retained. LIVE remains read-only/integration-pending and never falls back to SIM data. Racing LIVE remains hard locked.

---

## 0.8.31

0.8.31 separates the operator UI into explicit **SIM** and **LIVE** data contexts across the entire application. LIVE remains read-only/locked and is intentionally backed by dedicated stub provider contracts until real exchange integrations are implemented.

## SIM accounts

- Betfair and Matchbook SIM exchange accounts are editable from the account cards.
- Supported actions: Add funds, Withdraw, Set balance, Reset funding adjustments.
- Funding changes are distributed to the existing Sports/Racing portfolio wallets without rewriting opening balances or historical P&L.
- Every funding change is written to `sim_account_adjustments` and creates account snapshots for audit/replay.
- Reserved capital is never consumed by a withdrawal.
- Account reconciliation includes cumulative funding adjustments.

## LIVE isolation

- Global **SIM | LIVE** context is available in the application header and is synchronized with account views.
- Dashboard, Active Positions, Performance, Results, Execution Analysis, Market Analysis, Replay, Scenarios, Sports, Racing and Admin all obey the selected data context.
- LIVE pages bind only to `LiveProviderRegistry` contracts in `arbscanner/live_providers.py`.
- The initial LIVE providers are intentionally `UNAVAILABLE`/integration-pending stubs.
- No LIVE view may fall back to SIM balances, scanner caches, positions, executions, settlements, performance or replay history.
- No LIVE numeric snapshots are invented when a provider is unavailable.
- LIVE order placement remains disabled.

`arbscanner/live_providers.py` is the explicit future integration point for dedicated LIVE balance, market-feed, order, execution and settlement clients. It must not be replaced with a SIM fallback.

## Clock geometry

- Dashboard clock faces now have an invariant 1:1 aspect ratio.
- Responsive layouts may shrink/hide/reflow clock widgets, but the face itself cannot stretch into an oval.

## Compatibility

- Existing monitor database mode labels are retained internally as a storage compatibility alias for historical SIM snapshots.
- The UI/API provider context is now `sim` or `live`.
- No database reset or wallet reset is required.
- Existing Sports, Racing MONITOR, SUPERBET, settlement and analytics history are preserved.
- Racing LIVE remains hard-locked.

---

## 0.8.30

0.8.30 is an accounting-architecture and LIVE-readiness release. It does **not** unlock LIVE betting.

## Canonical account model

The UI now consumes a common exchange-account shape for MONITOR and future LIVE sources. MONITOR values come from the virtual ledger. LIVE account reads, where credentials permit, are read-only diagnostics and are never used to enable order placement in this release.

Each exchange account carries explicit currency, available balance, reserved/exposure state, source, freshness, last update and reconciliation metadata. Sports and Racing remain allocation/attribution views over the account layer rather than being modelled as separate real exchange accounts.

## Financial audit history

Two additive SQLite tables record account checkpoints and reconciliation outcomes. MONITOR checkpoints are written at startup, manual refresh, wallet resets, immediately before and after execution reservation, and immediately before and after settlement release. Both stream-level allocation snapshots and aggregate exchange-account snapshots are retained so Replay can reconstruct account state without double counting portfolios.

## Reconciliation

Admin now checks:

- open-position stake reservations against canonical exchange reserved balances;
- Sports/Racing allocation equity against exchange-account equity;
- ledger equity against opening capital plus realised P&L.

Discrepancies are surfaced explicitly rather than silently hidden.

## LIVE boundary

Betfair and Matchbook account panels support a LIVE read-only diagnostic view. Account failures/staleness are visible, but `live_execution_allowed` and order placement remain false. Racing LIVE remains hard-locked.

## Compatibility

The migration is additive. Existing databases, wallets, positions, results, SUPERBET history and Racing research/Monitor data are preserved. No reset is required.

---

## 0.8.29

0.8.29 is a UI/analytics integrity release built on the 0.8.28 SUPERBET execution baseline. It does not loosen LIVE execution boundaries.

## Responsive workspace

- Replaces forced whole-page Dashboard scaling with readable responsive reflow.
- Fit keeps normal zoom and reflows cards/charts; constrained windows may scroll vertically rather than miniaturising the UI.
- MacBook and 16:9 remain density/layout targets, not fixed-resolution canvases.
- Header clocks reduce before core content, KPI/portfolio grids reflow, chart grids stack, and position rows become single-column when required.

## Analytics

- Performance gains Today, Yesterday, 24h, 7d, 30d, This week, This month, All and Custom periods.
- Custom Performance ranges use From/To date+time controls and report the exact timezone-aware range used by the backend.
- Performance charts use larger readable axes, smarter tick density, stronger series treatment and native exact-value hover details.
- Market Analysis summary tiles now expose `?` metric definitions.
- Hourly opportunity heatmap can switch between Today and Yesterday without changing the wider Market Analysis period.
- Replay Running P&L is resized/re-scaled with readable axes, a playback cursor/current value and hoverable settlement values.
- Replay marker values use progressive disclosure on hover/selection/playback focus.

## Reconciliation and navigation

- Sidebar Active Positions has a canonical live count badge.
- Results adds Win rate · 24h using the same `dashboard_results_24h` settled ledger as Dashboard.
- Results adds Superbets placed using the same canonical SUPERBET summary as Dashboard.

## Execution structure audit

- Active Positions no longer calls every stored fill a planned leg.
- Base/planned legs, SUPERBET scaled fills, balancing/recovery fills and emergency hedges are kept distinct in the UI.
- The role distinction is carried into Results, Execution Analysis event detail and Replay position structure.
- Financial calculations and settlement logic are unchanged by this presentation/audit fix.

## Safety boundary

- SUPERBET remains MONITOR/paper only and disabled by default unless configured.
- Greyhound Racing MONITOR remains enabled.
- Racing LIVE order placement remains hard-locked.
- No database or wallet reset is required.

## Verification

- 317 tests passed, 21 skipped.
- Python compilation passed.
- Complete browser JavaScript passed `node --check`.
- Release ZIP integrity checked after packaging.

---

## 0.8.28

## Execution

SUPERBET adds controlled multi-tranche MONITOR scaling. The normal base tranche is executed first. A later tranche is prohibited until the preceding tranche has reached a balanced COMPLETE/HEDGED state. Each later tranche uses a fresh targeted exchange read, remaining paper depth and a fresh post-commission qualification.

Configuration includes enable/disable, arbitrary finite or unlimited tranche count, base/fixed tranche sizing, maximum total stake, minimum fresh net edge, minimum depth multiplier and recheck delay. `unlimited` is never unlimited risk: wallet availability, global bankroll percentage, event exposure and all other execution safeguards remain binding.

Each parent stores its child tranches with fresh price/depth snapshots, fills, hedge snapshots, locked P&L, stake-weighted fill rate and outcome-by-venue P&L. Settlement adds base, incremental and total realized P&L. Scaling stop reason is explicit.

SUPERBET is disabled by default and remains MONITOR/paper only. No LIVE order path is enabled. Racing LIVE remains hard-locked.

## UI and reporting

Dashboard reports Superbet parents placed, child-tranche count, additional scaled stake and incremental settled/expected profit. SUPERBET parents are highlighted consistently across position, result and execution views and expand to tranche-level audit detail. Sports and Racing overview position lists reuse the Active Positions visual component.

Dashboard Fit is viewport-driven: actual available width and height determine the largest readable fit. MacBook and 16:9 selections are density targets, while Full remains unconstrained. Compact density rules also apply across the other major workspaces.

Greyhound research/execution presentation now labels Theoretical best book separately from Deployable selected book and exposes the liquidity-limiting quote. Theoretical pricing remains diagnostic-only.

## Upgrade

No database reset, wallet reset or destructive migration is required. Existing 0.8.27 Monitor data is preserved.

---

## 0.8.26

## Greyhound / Matchbook diagnostics

- Canonicalises Matchbook `back` / `win` as diagnostic BACK and `lay` / `lose` as diagnostic LAY while preserving the exact raw source-side label.
- If an already-matched Greyhound race is still missing a complete BACK or LAY side after normal discovery, the discovery worker performs a batched, side-specific Matchbook probe only for the missing side(s).
- Probe evidence is tagged `side_probe`, timestamped, and carried into the cached matched-race snapshot so Racing Monitor can show it during fast price refreshes.
- Side-probe evidence is diagnostic only. It cannot replace the Matchbook quote used by the arbitrage engine and does not unlock Racing execution.
- Racing Monitor runner detail now labels the raw Matchbook side (`BACK/LAY/WIN/LOSE`) and whether a value came from the side probe.

## Active Positions financial semantics

- Renamed the top capital card to **Wallet committed capital** to make clear it represents reserved wallet capital.
- Renamed the aggregate percentage to **Locked return on balanced capital**.
- The API now exposes the exact balanced-position deployed-capital denominator, count of balanced positions, and any deployed capital that is not locked/balanced.
- The UI shows that denominator directly under the aggregate locked-return percentage.

## Best Win integrity

- Dashboard Best Win remains settled-only and is explicitly tagged in the API as using `realized_pnl` with `settled_at` as the time basis.
- Regression coverage proves expected/locked profit cannot substitute for realized P&L and that the 24-hour boundary excludes older larger wins.
- The selected Dashboard Best Win is reconciled against the settled-position ledger.

## Safety / unchanged behaviour

- Racing remains research-only; no Racing Monitor positions, hedges or LIVE orders are enabled.
- Matchbook executable-back interpretation is unchanged in this release.
- Sports strategy, execution and settlement logic are unchanged.
- No database reset, wallet reset, destructive migration or threshold reduction.
- Includes the 0.8.25 database-lock, analysis-runtime, frontend-startup and Racing Monitor selection hotfixes.

## Verification

- Full test suite: **303 passed, 21 skipped**.
- Complete browser JavaScript payload passes `node --check`.
- Python modules compile successfully.

---

