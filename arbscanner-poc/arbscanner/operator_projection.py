from __future__ import annotations

from collections.abc import Iterable


OPERATOR_DOMAINS = frozenset({"sports", "racing"})
ENGINE_INTEGER_FIELDS = ("processed", "opportunities", "qualified", "executed", "settled", "errors")
ENGINE_TOTAL_FIELDS = (*ENGINE_INTEGER_FIELDS, "realised_pnl")


def operator_domain(value, *, default: str = "sports") -> str:
    """Return the supported operator domain without ever widening across domains."""
    domain = str(value or default).strip().lower()
    return domain if domain in OPERATOR_DOMAINS else default


def engine_catalog_row(engine: dict, type_meta: dict | None, performance: dict | None) -> dict:
    """Pure catalogue projection shared by Sports/Racing engine surfaces."""
    row = dict(engine or {})
    meta = dict(type_meta or {})
    row["reference_only"] = bool(meta.get("reference_only"))
    row["package_origin"] = meta.get("package_origin") or row.get("package_source")
    row["performance"] = dict(performance or {})
    return row


def engine_catalog_visible(row: dict, *, include_reference: bool = False) -> bool:
    if include_reference:
        return True
    capabilities = {str(value).upper() for value in (row.get("capabilities") or [])}
    return not bool(row.get("reference_only")) and "TEST_ONLY" not in capabilities


def merge_engine_lifecycle_groups(groups: Iterable[Iterable[dict]]) -> list[dict]:
    """Merge per-sport lifecycle rows by immutable engine instance identity."""
    merged: dict[str, dict] = {}
    for group in groups:
        for source in group:
            row = dict(source or {})
            iid = str(row.get("engine_instance_id") or "")
            if iid not in merged:
                merged[iid] = row
                continue
            out = merged[iid]
            for key in ENGINE_INTEGER_FIELDS:
                out[key] = int(out.get(key) or 0) + int(row.get(key) or 0)
            out["realised_pnl"] = round(float(out.get("realised_pnl") or 0.0) + float(row.get("realised_pnl") or 0.0), 4)
            if str(row.get("last_activity") or "") > str(out.get("last_activity") or ""):
                out["last_activity"] = row.get("last_activity")
    return list(merged.values())


def project_engine_lifecycle(rows: Iterable[dict], *, mode: str, engine_filter: str = "all") -> tuple[list[dict], dict]:
    """Project operator-facing engine lifecycle without mutating source rows.

    LIVE engine qualification is decision evidence only until an authoritative LIVE
    lifecycle exists. It remains visible as ``decision_qualified_evidence`` while
    the operator-facing Qualified stage fails closed to zero.
    """
    selected = [dict(row or {}) for row in rows]
    requested = str(engine_filter or "all").strip()
    if requested and requested.lower() not in {"all", "all engines"}:
        selected = [row for row in selected if str(row.get("engine_instance_id") or "") == requested]

    live = str(mode or "sim").strip().lower() == "live"
    if live:
        for row in selected:
            row["decision_qualified_evidence"] = int(row.get("qualified") or 0)
            row["qualified"] = 0

    totals = {
        key: sum(float(row.get(key) or 0) for row in selected)
        for key in ENGINE_TOTAL_FIELDS
    }
    if live:
        totals["decision_qualified_evidence"] = int(
            sum(int(row.get("decision_qualified_evidence") or 0) for row in selected)
        )
    for key in ENGINE_INTEGER_FIELDS:
        totals[key] = int(totals[key])
    totals["realised_pnl"] = round(float(totals["realised_pnl"]), 4)
    return selected, totals
