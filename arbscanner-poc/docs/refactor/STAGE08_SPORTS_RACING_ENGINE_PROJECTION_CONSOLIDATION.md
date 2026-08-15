# Stage 08 — Sports / Racing / Engine Projection Consolidation

## Goal

Reduce projection duplication only where Sports and Racing are semantically identical. Keep domain-specific market, lifecycle and account ownership intact.

## Consolidated contract

`arbscanner.operator_projection` is a pure projection module. It owns:

- operator-domain normalisation (`sports` / `racing`) without an `all` widening path;
- engine catalogue decoration and reference/test visibility rules;
- multi-sport lifecycle aggregation by immutable `engine_instance_id`;
- common engine filter/totals projection;
- LIVE engine qualification sanitisation: decision-qualified evidence remains diagnostic while operator-facing `qualified` is zero.

The module has no DB, provider or scanner dependency. Acquisition remains in API/DB boundaries.

## Deliberately not merged

Sports and Racing overviews still own different evidence and presentation semantics:

- Sports: pre-match/in-play streams, sports market highlights and sports monitor positions.
- Racing: future race schedule, runner/book diagnostics, race matching, racing monitor execution evidence and racing-specific guardrails.
- Racing LIVE remains hard locked. Provider schedule/matching facts do not become LIVE lifecycle authority.

Stage 08 does not consolidate RPC routes; that is Stage 09.

## Benchmark expansion

The Stage 02 manifest is expanded from 23 to 29 projections so the gate now directly measures:

- Sports engine catalogue;
- Racing engine catalogue;
- Sports engine lifecycle in SIM and LIVE;
- Racing engine lifecycle in SIM and LIVE.

All previous projections remain present.
