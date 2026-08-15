# Stage 12.3 — Scenarios global research exception

## Trigger
Real-environment review confirmed that Scenarios is intentionally different from the operational SIM/LIVE pages. It is one modelling/research workspace, not an operational economic projection. The same scenario controls, assumptions, recorded evidence and model output should therefore be available identically while the application header is in SIM or LIVE.

## Boundary
Frontend-only behaviour change on top of the frozen Stage 12.2 Candidate. Scenario calculations continue to use the existing recorded SIM research evidence and existing `scenario_capital_sources` / `analytics_replay` backend routes. No backend, database, provider, lifecycle, financial-authority or order-writing code changes.

## Change
- Scenarios is labelled `GLOBAL · RESEARCH` in either application mode.
- The Scenario Console remains visible in both SIM and LIVE.
- The old LIVE blank/isolation panel is removed and replaced with the same global research notice in both modes.
- Switching SIM ↔ LIVE while Scenarios is open does not clear, reload, recalculate or replace Scenario controls/results/chart state.
- Scenario route/request validity ignores application-mode generation and follows only the active Scenarios route/pane, so a mode switch cannot discard a valid modelling result.
- `Run Scenario`, Refresh evidence and Engine → Scenario entry work identically in either application mode.
- The evidence contract is unchanged: scenarios replay recorded SIM research evidence and never mutate operational settings.

## Non-goals
- Scenarios does not become a LIVE execution/economic surface.
- No LIVE orders or lifecycle writes are enabled.
- No scenario result is sourced from operational LIVE account/economic state.
- No changes to Sports/Racing/Performance/Market/Replay mode ownership.

## Acceptance
1. Run/populate Scenarios in SIM, switch to LIVE, and require controls, result KPIs, summary, chart and JS scenario state to remain byte-for-byte/DOM-identical.
2. Switch back LIVE → SIM and require the same state to remain unchanged.
3. `GLOBAL · RESEARCH` badge is shown in both modes.
4. A scenario can be run while application mode is LIVE and uses the same existing `analytics_replay` contract.
5. Stage 12.1 Sports Monitor mode isolation and Stage 12.2 stale-render protections remain passing.
6. Full cumulative refactor tests, architecture/syntax/browser gates and 29-projection same-DB comparison remain clean.
