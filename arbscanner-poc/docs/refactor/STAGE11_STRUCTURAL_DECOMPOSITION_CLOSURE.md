# Stage 11 — Structural Decomposition and Closure

## Scope

Stage 11 is the final local structural refactor stage. The user-verified Stage 10 Candidate is the immutable parent. This stage does not change provider acquisition, SIM/LIVE authority, lifecycle writes, account economics, frontend behaviour, public RPC contracts or LIVE execution safety.

## Decomposition

Three static/pure responsibilities are moved out of oversized runtime modules:

1. `arbscanner.config`
   - owns `DEFAULT_CONFIG` and `OPERATING_MODES`;
   - has no runtime-service or DB dependency;
   - `arbscanner.api` re-exports both names for compatibility.

2. `arbscanner.db_schema`
   - owns the exact SQLite `SCHEMA` declaration;
   - has no DB runtime dependency;
   - `arbscanner.db` imports/re-exports the same string.

3. `arbscanner.racing_projection`
   - owns the pure `racing_book_analysis_from_sources` reconstruction;
   - depends only on Racing normalisation, models and strategy book maths;
   - `arbscanner.api._racing_book_analysis_from_sources` remains as a compatibility alias for historical tests/callers.

The extraction reduces `api.py` from 7,638 to 7,181 lines and `db.py` from 8,784 to 8,016 lines without changing the frontend source.

## Closure rules

Stage 11 requires all of the following before packaging:

- static structure gate passes;
- Stage 02–11 cumulative targeted tests pass;
- full 29-projection Stage 10 → Stage 11 same-DB comparison passes with identical fingerprints;
- no authority writes and no query/write regressions;
- historical tests around the moved Racing/config/schema contracts have no new failures compared with Stage 10;
- Python compilation, inline JavaScript syntax and Dashboard browser integrity pass;
- packaged Candidate is rebuilt from a fresh Stage 10 extraction and retested;
- exact Stage 10 → Stage 11 patch and SHA-256 evidence are produced.

## Stage 12 boundary

Passing Stage 11 is **local refactor closure only**, not final product sign-off. Stage 12 still requires the external same-copied-deployed-DB Reference-vs-Candidate benchmark and connected Betfair/Matchbook live-feed soak with central LIVE order writing locked.
