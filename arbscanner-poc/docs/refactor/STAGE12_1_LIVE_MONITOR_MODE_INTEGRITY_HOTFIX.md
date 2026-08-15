# Stage 12.1 — LIVE Sports Monitor mode-integrity hotfix

## Field defect

During real Stage 12 deployment, Sports Monitor was visibly in `LIVE · ACTUAL ONLY` while its funnel showed SIM lifecycle totals, including `Qualified 80` and `Executed 4`. The live decision rows themselves were LIVE evidence, but the funnel renderer reused the global `sportsEngineLifecycleRows0936` array last populated in SIM.

## Root cause

The LIVE route loads `loadLiveMonitor('sports')` directly. Unlike the SIM `loadMonitor()` path, that LIVE route intentionally does not call the engine lifecycle refresh. The shared renderer nevertheless preferred any already-populated engine lifecycle array. After a SIM → LIVE transition, that array therefore remained SIM-owned and was rendered into the LIVE funnel.

A related route-entry issue left previous monitor DOM visible until the new asynchronous route read returned, producing the brief stale-data flicker reported on populated pages.

## Fix

- Prime the Sports Monitor route synchronously before its async loader: clear row state, active positions, lifecycle totals, funnel metrics and table content.
- Clear the lifecycle array at the start of the LIVE Sports Monitor loader.
- Make the funnel renderer fail closed in LIVE: it never consumes the SIM/global lifecycle array; `Qualified` and `Executed` remain zero while LIVE execution is centrally locked.
- Preserve SIM behavior: SIM continues to refresh and use authoritative engine lifecycle rows.

No backend, DB, provider, lifecycle-write, financial, execution, or order-writing code is changed.
