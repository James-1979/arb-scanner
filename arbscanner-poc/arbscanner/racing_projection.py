from __future__ import annotations

from dataclasses import asdict

from .engine import strategy_book_analysis
from .models import Leg
from .racing import normalize_runner_name, runner_match_score

def racing_book_analysis_from_sources(source_markets: list[dict], minimum_liquidity: float = 0.0) -> dict:
    """Rebuild an auditable Greyhound price book from exact stored source quotes.

    v0.8.27 carries the *strict runner alignment* into the economic layer.  A
    Betfair ``trap:1|dog`` key and a Matchbook ``name:dog`` key are two source
    identifiers for one race outcome, not two outcomes.  The reconstruction
    therefore builds one canonical slot per runner first and only then adds each
    exchange's prices to that slot.

    Matchbook's raw BACK/LAY sides remain diagnostic only; the shared adapter
    interpretation is deliberately unchanged until the captured evidence proves
    which side is executable for a back instruction.
    """
    raw_sources = [x for x in (source_markets or []) if isinstance(x, dict) and (x.get("runner_prices") or [])]
    if len(raw_sources) < 2:
        return {"valid": False, "reason": "Exact runner price snapshots are not available for this matched race."}

    # Prefer Betfair as the canonical field because it normally carries trap
    # metadata.  The identity is still name-aware and works when trap metadata is
    # absent on one exchange.
    base_source = next((x for x in raw_sources if str(x.get("exchange") or "").lower().startswith("betfair")), raw_sources[0])
    base_rows = list(base_source.get("runner_prices") or [])
    expected = len(base_rows)
    if expected < 2:
        return {"valid": False, "reason": "Racing runner field is incomplete."}

    def trap_value(raw: dict) -> int | None:
        value = raw.get("trap_number")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def display_name(raw: dict) -> str:
        return str(raw.get("selection") or raw.get("canonical_selection_key") or "Runner").strip()

    def canonical_slot_key(raw: dict, index: int) -> str:
        trap = trap_value(raw)
        name = normalize_runner_name(display_name(raw))
        # The slot key is an internal race-local identity.  Trap is useful when
        # available but the normalized runner name makes the mapping auditable.
        if trap is not None:
            return f"runner:{index + 1}|trap:{trap}|{name}"
        return f"runner:{index + 1}|name:{name}"

    slots: list[dict] = []
    for i, raw in enumerate(base_rows):
        slots.append({
            "key": canonical_slot_key(raw, i),
            "display": display_name(raw),
            "trap_number": trap_value(raw),
            "base": raw,
            "prices": {},
            "matchbook_raw_sides": {},
            "source_keys": {},
        })

    def best_raw_side(items: list[dict], side: str) -> dict | None:
        rows = []
        for item in items or []:
            raw_side = str(item.get("side") or item.get("source_side") or "").lower()
            canonical_side = "back" if raw_side in {"back", "win"} else "lay" if raw_side in {"lay", "lose"} else raw_side
            if canonical_side != side:
                continue
            try:
                odds = float(item.get("odds") or 0.0)
                liquidity = float(item.get("available_amount") or 0.0)
            except (TypeError, ValueError):
                continue
            if odds > 1.0 and liquidity > 0.0:
                rows.append({
                    "side": side, "odds": odds, "liquidity": liquidity,
                    "source_side": item.get("source_side"),
                    "requested_side": item.get("requested_side"),
                    "source": item.get("source") or "event_feed",
                    "observed_at": item.get("observed_at"),
                    "side_inferred_from_request": bool(item.get("side_inferred_from_request")),
                })
        return max(rows, key=lambda x: x["odds"]) if rows else None

    # Align every source field onto the same canonical slots with the same strict
    # runner scorer used by Racing matching.  If a stored source cannot be mapped
    # one-to-one, fail closed instead of calculating a misleading N*2-outcome book.
    for source in raw_sources:
        exchange = str(source.get("exchange") or "")
        rows = list(source.get("runner_prices") or [])
        if len(rows) != expected:
            return {
                "valid": False, "mapping_error": True, "expected_outcomes": expected,
                "economic_outcomes": len(rows),
                "reason": f"RUNNER MAPPING ERROR · {len(rows)} source runners for {expected}-runner field ({exchange or 'exchange'}).",
            }
        used: set[int] = set()
        for raw in rows:
            best = None
            for idx, slot in enumerate(slots):
                if idx in used:
                    continue
                base = slot["base"]
                score = float(runner_match_score(
                    display_name(base), display_name(raw), trap_value(base), trap_value(raw)
                ))
                if best is None or score > best[0]:
                    best = (score, idx)
            if best is None or best[0] < 0.92:
                return {
                    "valid": False, "mapping_error": True, "expected_outcomes": expected,
                    "economic_outcomes": expected,
                    "reason": f"RUNNER MAPPING ERROR · could not align {display_name(raw) or 'runner'} from {exchange or 'exchange'} to the strict {expected}-runner field.",
                }
            _, idx = best
            used.add(idx)
            slot = slots[idx]
            try:
                odds = float(raw.get("odds") or 0.0)
                liquidity = float(raw.get("liquidity") or 0.0)
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            canonical_key = slot["key"]
            slot["source_keys"][exchange] = str(raw.get("canonical_selection_key") or "") or None
            slot["prices"][exchange] = {
                "selection": display_name(raw), "odds": odds, "liquidity": liquidity,
                "commission_pct": float(raw.get("commission_pct") or 0.0),
                "commission_source": str(raw.get("commission_source") or "captured"),
                "interpreted_source_side": raw.get("interpreted_source_side"),
            }
            if exchange == "Matchbook":
                for side in ("back", "lay"):
                    side_row = best_raw_side(raw.get("raw_prices") or [], side)
                    if side_row:
                        slot["matchbook_raw_sides"][side] = side_row

    candidates: dict[str, list[Leg]] = {}
    audit: dict[str, dict] = {}
    for slot in slots:
        key = slot["key"]
        audit[key] = {
            "key": key, "trap_number": slot.get("trap_number"), "display": slot.get("display"),
            "prices": dict(slot.get("prices") or {}),
            "matchbook_raw_sides": dict(slot.get("matchbook_raw_sides") or {}),
            "source_keys": dict(slot.get("source_keys") or {}),
        }
        legs = []
        for source in raw_sources:
            exchange = str(source.get("exchange") or "")
            price = (slot.get("prices") or {}).get(exchange)
            if not price:
                continue
            legs.append(Leg(
                exchange=exchange, selection=key, odds=float(price.get("odds") or 0.0),
                liquidity=float(price.get("liquidity") or 0.0),
                commission_pct=float(price.get("commission_pct") or 0.0),
                commission_source=str(price.get("commission_source") or "captured"),
                event_id=str(source.get("event_id") or "") or None,
                market_id=str(source.get("market_id") or "") or None,
                market_type="win", strategy="multi_runner_win", sport="Greyhounds",
                in_play=source.get("in_play"), market_status=source.get("status"), section="racing",
                trap_number=slot.get("trap_number"), canonical_selection_key=key, runner_status="ACTIVE",
            ))
        if len(legs) != len(raw_sources):
            return {
                "valid": False, "mapping_error": True, "expected_outcomes": expected,
                "economic_outcomes": len(slots),
                "reason": f"RUNNER MAPPING ERROR · canonical runner {slot.get('display') or key} does not have a price from every matched exchange.",
            }
        candidates[key] = legs

    if len(candidates) != expected:
        return {
            "valid": False, "mapping_error": True, "expected_outcomes": expected,
            "economic_outcomes": len(candidates),
            "reason": f"RUNNER MAPPING ERROR · {len(candidates)} economic outcomes for {expected}-runner field.",
        }

    analysis = strategy_book_analysis(
        candidates, minimum_liquidity=max(0.0, float(minimum_liquidity or 0.0)),
        require_cross_exchange=True,
    )
    if not analysis.get("valid"):
        return {"valid": False, "reason": str((analysis.get("selected_diagnostic") or {}).get("reason") or analysis.get("reason") or "No complete cross-exchange price combination")}

    selected_legs = list(analysis.get("selected_legs") or [])
    best_combined_legs = list(analysis.get("best_combined_legs") or [])
    selected_by_key = {str(leg.selection): leg for leg in selected_legs}
    best_by_key = {str(leg.selection): leg for leg in best_combined_legs}
    runner_rows = []
    for key, slot in audit.items():
        selected = selected_by_key.get(key)
        best = best_by_key.get(key)
        row = dict(slot)
        row["selected_exchange"] = selected.exchange if selected else None
        row["selected_odds"] = selected.odds if selected else None
        row["best_exchange"] = best.exchange if best else None
        row["best_odds"] = best.odds if best else None
        runner_rows.append(row)
    runner_rows.sort(key=lambda r: (r.get("trap_number") is None, r.get("trap_number") or 999, str(r.get("display") or "")))

    def book_for_values(values: list[float]) -> float | None:
        if len(values) != expected or any(float(x or 0) <= 1.0 for x in values):
            return None
        return round(sum(100.0 / float(x) for x in values), 6)

    raw_side_books, combined_by_side, raw_side_complete = {}, {}, {}
    for side in ("back", "lay"):
        mb_values, combined_values = [], []
        complete = True
        for row in runner_rows:
            mb = (row.get("matchbook_raw_sides") or {}).get(side)
            bf = (row.get("prices") or {}).get("Betfair delayed") or (row.get("prices") or {}).get("Betfair")
            if not mb:
                complete = False
                break
            mb_values.append(float(mb.get("odds") or 0.0))
            bf_odds = float((bf or {}).get("odds") or 0.0)
            combined_values.append(max(bf_odds, float(mb.get("odds") or 0.0)) if bf_odds > 1.0 else float(mb.get("odds") or 0.0))
        raw_side_complete[side] = complete
        raw_side_books[side] = book_for_values(mb_values) if complete else None
        combined_by_side[side] = book_for_values(combined_values) if complete else None

    limiter_candidates = []
    liquidity_floor = max(0.0, float(minimum_liquidity or 0.0))
    for row in runner_rows:
        if row.get("best_exchange") == row.get("selected_exchange") and row.get("best_odds") == row.get("selected_odds"):
            continue
        best_price = (row.get("prices") or {}).get(str(row.get("best_exchange") or "")) or {}
        liquidity = max(0.0, float(best_price.get("liquidity") or 0.0))
        limiter_candidates.append({
            "runner": row.get("display"),
            "trap_number": row.get("trap_number"),
            "exchange": row.get("best_exchange"),
            "odds": row.get("best_odds"),
            "liquidity": liquidity,
            "minimum_liquidity": liquidity_floor,
            "selected_exchange": row.get("selected_exchange"),
            "selected_odds": row.get("selected_odds"),
            "reason": "best_price_below_liquidity_floor" if liquidity < liquidity_floor else "best_price_not_in_deployable_selection",
        })
    liquidity_limiter = min(limiter_candidates, key=lambda x: float(x.get("liquidity") or 0.0), default=None)

    current_side = "back"
    current_raw_book = raw_side_books.get(current_side)
    alternate_raw_book = raw_side_books.get("lay")
    suspicious = bool(
        current_raw_book is not None and alternate_raw_book is not None
        and current_raw_book >= 200.0 and alternate_raw_book + 20.0 < current_raw_book
    )
    diag = dict(analysis.get("selected_diagnostic") or {})
    return {
        "valid": True,
        "expected_outcomes": expected,
        "economic_outcomes": len(candidates),
        "runner_mapping_valid": len(candidates) == expected,
        "selection_basis": analysis.get("selection_basis"),
        "selected_cross_exchange_book_pct": analysis.get("selected_cross_exchange_book_pct"),
        "best_combined_book_pct": analysis.get("best_combined_book_pct"),
        "exchange_books_pct": analysis.get("exchange_books_pct") or {},
        "selected_diagnostic": diag,
        "selected_legs": [asdict(leg) for leg in selected_legs],
        "best_combined_legs": [asdict(leg) for leg in best_combined_legs],
        "runner_prices": runner_rows,
        "liquidity_limiter": liquidity_limiter,
        "matchbook_side_audit": {
            "available": bool(raw_side_complete.get("back") or raw_side_complete.get("lay")),
            "current_interpretation": current_side,
            "raw_books_pct": raw_side_books,
            "best_combined_books_pct": combined_by_side,
            "complete": raw_side_complete,
            "suspicious_current_interpretation": suspicious,
            "note": "Diagnostic only: Matchbook win/lose aliases are canonicalised and missing sides may be probed; executable BACK interpretation is unchanged in v0.8.44.",
        },
    }
