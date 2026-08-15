# Stage 03 — Frontend Mode and Route Ownership

Status: implementation checkpoint built from user-verified Stage 02.

## Purpose

Stage 03 tightens frontend ownership only. It does not change API, DB, provider, scanner, settlement, staking, account or LIVE execution semantics.

The objective is to make one current UI context authoritative for asynchronous page reads so an older SIM/LIVE route or Analytics-pane response cannot render after the operator has moved elsewhere.

## Changes

### 1. Route generation joins mode generation

`modeRequestToken()` now captures:

- selected economic mode;
- mode epoch;
- route epoch;
- owning page;
- Analytics pane when the owner is Analytics.

`modeRequestCurrent()` is the common response gate. A response is stale when any captured generation/owner no longer matches the current UI context.

The historical `monitor-last-detected` component key is explicitly mapped to its owning `monitor` page so component naming does not defeat route ownership.

### 2. Explicit SIM/LIVE route-loader registry

`routeLoadersStage03` is the single table describing which loader owns each top-level route for SIM and LIVE.

This preserves existing 0.9.57 semantics, including:

- SIM Active Positions continuing to hydrate from the Dashboard projection;
- LIVE Active Positions using actual LIVE execution activity only;
- LIVE Sports/Racing Monitor using isolated LIVE evidence;
- SIM/LIVE config routes retaining their existing account/config ownership;
- Analytics delegating to the currently selected pane without creating a second route generation.

### 3. Analytics pane generation

Direct pane changes increment `routeEpoch`. Route orchestration can re-enter the current Analytics pane with `reuseRouteEpoch=true`, avoiding a double logical route transition while still ensuring loaders create tokens after the current route generation is established.

### 4. Mode-owned shell priming

Mode-switch shell priming is called directly from the authoritative data-mode transition before route work is queued. This preserves the synchronous anti-flash behaviour while retiring the older 0.9.36 `setGlobalDataMode` / `initialiseDataModeShell` wrapper layer.

The later 0.9.44 Active Positions LIVE guard is intentionally retained for compatibility with its established regression contract.

## Invariants

- SIM and LIVE economic/lifecycle data never fall back to each other.
- Old mode/route/pane reads are discarded before render.
- LIVE order writing remains centrally locked.
- No backend read/write paths are changed by this stage.
- The Stage 02 23-projection reference/candidate benchmark must remain 23/23 PASS with zero blockers/warnings.

## Validation

Stage 03 adds `tests/test_stage03_frontend_mode_route_ownership.py` to freeze the route-generation token, explicit loader registry, Analytics-pane invalidation, synchronous mode-shell priming and retained Active Positions guard.
