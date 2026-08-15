# Stage 10 — Performance and Data-Load Optimisation

## Scope

Stage 10 optimises measured interactive read work only. The user-verified Stage 09 Candidate is the immutable parent.

The Stage 09 benchmark identified Racing Monitor as the clearest duplicate-load hot path: each `racing_monitor` projection executed 100 read queries because it called the full SIM `racing_overview`, even though Monitor only needs the matched Racing row projection used to enrich discovery rows.

## Change

The matched Racing row projection is now isolated in `API._racing_matched_rows()` and is reused by `racing_overview`.

`racing_monitor` still resolves matched detail through `racing_overview`, preserving the established compatibility seam, but requests the private `_matched_rows_only` projection. That path returns only the projected matched rows and skips unrelated SIM portfolio, Dashboard, lifecycle summary, funnel, discovery-summary and operational-status work.

This is intentionally not a new public UI contract: the flag is an internal in-process optimisation used only by `racing_monitor`.

## Safety properties

- Full `racing_overview` output remains unchanged.
- `racing_monitor` output remains fingerprint-identical to Stage 09.
- Existing tests that monkeypatch `racing_overview` as the matched-detail seam continue to work.
- No lifecycle/account authority is written by the optimised read.
- No SIM/LIVE ownership rule changes.
- LIVE order writing remains locked.
- No stateful cache is added, so there is no new financial-revision/mode/scope invalidation risk.

## Measured acceptance targets

The Stage 09 same-DB harness baseline is 29 projections and 855 total read queries. Racing Monitor is 100 queries per benchmark projection.

Stage 10 requires:

- Racing Monitor <= 10 queries per projection on the refactor fixture;
- full 29-projection comparison with identical output fingerprints;
- 0 blockers and 0 warnings;
- 0 authority writes;
- total benchmark queries <= 665;
- no increase in read-path write statements.

Wall-clock elapsed time is recorded as supporting evidence, not a sole pass/fail criterion because sub-millisecond/local SQLite timings are environment-sensitive.
