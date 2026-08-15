# Stage 12.2 — Global stale-render closure

## Trigger
Real-environment QA showed a very brief previous-render flash when entering the heaviest pages: Performance, Sports Overview and Racing Overview. Stage 12.1 already closed the Sports Monitor SIM→LIVE lifecycle leak; Stage 12.2 closes the remaining visual stale-render window without changing backend projection semantics.

## Boundary
Frontend only. The frozen Stage 12.1 Candidate is the parent.

## Change
- Prime Performance, Sports Overview and Racing Overview synchronously before route-loader dispatch.
- Prime the active heavy route synchronously on SIM/LIVE mode changes before the queued reload.
- Prime Performance before activating/loading the Performance analytics pane.
- Prime the same surfaces on direct refresh/filter reloads.
- Replace prior visible values with neutral loading placeholders; do not retain prior mode/scope economics while asynchronous reads are in flight.

## Non-goals
- No backend/API/DB/provider changes.
- No caching.
- No change to economic calculations or route payloads.
- No LIVE execution change; central order writing remains locked.

## Acceptance
1. Deliberately injected stale values disappear synchronously on all three surfaces.
2. Stage 12.1 Monitor integrity remains intact.
3. Cumulative refactor safety tests pass.
4. 29/29 Stage 12.1→12.2 same-DB projection comparison remains fingerprint-identical with zero blockers/warnings and zero authority writes.
