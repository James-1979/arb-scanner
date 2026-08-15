# ArbScanner v1.0 Release Promotion

Parent: verified Stage 12.3 Candidate.

Scope: release identity/package promotion only. Runtime behavior remains the verified Stage 12.3 behavior.

Release invariants:
- Scenarios is GLOBAL research state across SIM/LIVE.
- Sports Monitor LIVE cannot consume SIM lifecycle totals.
- Heavy route entry clears stale visible data synchronously.
- Read projections do not mutate lifecycle/account authority.
- Canonical settlement remains atomic.
- LIVE order writing remains centrally locked.

The v1.0 release must pass the cumulative Stage 02-12.3 targeted suite, the 29-projection comparison gate against Stage 12.3, frontend/browser integrity checks, and release-identity verification before packaging.
