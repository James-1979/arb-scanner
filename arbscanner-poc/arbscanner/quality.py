from __future__ import annotations
from datetime import datetime, timezone
from statistics import median
from typing import Any


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def assess_data_quality(legs: list[Any], match_score: float = 0.0, observed_at: str | None = None,
                        stale_after_seconds: float = 90.0) -> dict[str, Any]:
    """Explain confidence limits without pretending delayed prices are live.

    `captured_at` is when ArbScanner received a quote, not the exchange's true
    underlying price timestamp. Betfair Delayed is therefore always called out
    separately even when the local capture is recent.
    """
    observed = _parse_iso(observed_at) or datetime.now(timezone.utc)
    warnings: list[str] = []
    penalty = 0.0
    ages: list[float] = []
    latencies: list[int] = []
    delayed = False
    fallback_commission = False

    for leg in legs or []:
        get = leg.get if isinstance(leg, dict) else lambda k, d=None: getattr(leg, k, d)
        captured = _parse_iso(get("captured_at"))
        if captured:
            ages.append(max(0.0, (observed - captured).total_seconds()))
        latency = int(get("source_latency_ms", 0) or 0)
        latencies.append(max(0, latency))
        exchange = str(get("exchange", "") or "")
        source = str(get("commission_source", "") or "")
        if "delayed" in exchange.lower():
            delayed = True
        if "fallback" in source.lower() or (source.lower() == "configured" and "betfair" in exchange.lower()):
            fallback_commission = True

    max_age = max(ages) if ages else 0.0
    max_latency = max(latencies) if latencies else 0
    if delayed:
        warnings.append("Betfair contributes delayed development data; this is research evidence, not a live executable quote.")
        penalty += 8.0
    if max_age > stale_after_seconds:
        warnings.append(f"At least one locally captured quote was {max_age:.0f}s old when this observation was evaluated.")
        penalty += 15.0
    if max_latency > 5000:
        warnings.append(f"A market-data request took {max_latency/1000:.1f}s, increasing timing uncertainty.")
        penalty += 5.0
    if fallback_commission:
        warnings.append("At least one Betfair leg used the configured commission fallback rather than an API market rate.")
        penalty += 5.0
    match = min(1.0, max(0.0, float(match_score or 0.0)))
    if match < 0.80:
        warnings.append(f"Market-match confidence is only {match*100:.0f}%.")
        penalty += 5.0

    if penalty >= 20:
        trust_band = "Low"
    elif penalty >= 8:
        trust_band = "Caution"
    else:
        trust_band = "Good"
    return {
        "trust_band": trust_band,
        "penalty_points": round(min(30.0, penalty), 1),
        "warnings": warnings,
        "uses_delayed_feed": delayed,
        "max_local_quote_age_seconds": round(max_age, 1),
        "max_api_latency_ms": max_latency,
        "fallback_commission": fallback_commission,
    }


def quality_profile(sim: dict[str, Any], match_score: float = 0.0, reference_bankroll: float = 500.0,
                    data_quality: dict[str, Any] | None = None) -> dict[str, Any]:
    bankroll = max(0.0, float(reference_bankroll or 0.0))
    if not sim or not sim.get("executable") or bankroll <= 0:
        return {"quality_score": 0.0, "quality_band": "Invalid",
                "quality_reason": str((sim or {}).get("reason") or "Not executable at the reference bankroll"),
                "reference_bankroll": bankroll, "deployed": 0.0, "unused_bankroll": bankroll,
                "expected_profit": 0.0, "deployed_roi_pct": 0.0, "bankroll_roi_pct": 0.0, "capital_used_pct": 0.0,
                "gross_profit": 0.0, "commission_cost": 0.0, "bankroll_after": bankroll,
                "data_quality": data_quality or {}}

    deployed = max(0.0, float(sim.get("deployed") or 0.0))
    profit = float(sim.get("expected_profit") or 0.0)
    deployed_roi = float(sim.get("expected_roi_pct") or 0.0)
    bankroll_roi = (profit / bankroll) * 100.0 if bankroll > 0 else 0.0
    capital_used = (deployed / bankroll) * 100.0 if bankroll > 0 else 0.0
    match = min(1.0, max(0.0, float(match_score or 0.0)))

    capacity_points = min(capital_used / 50.0, 1.0) * 50.0
    bankroll_points = min(max(bankroll_roi, 0.0) / 0.50, 1.0) * 30.0
    deployed_roi_points = min(max(deployed_roi, 0.0) / 1.00, 1.0) * 15.0
    match_points = match * 5.0
    raw_score = capacity_points + bankroll_points + deployed_roi_points + match_points
    penalty = float((data_quality or {}).get("penalty_points") or 0.0)
    score = max(0.0, min(100.0, raw_score - penalty))

    if capital_used < 2.0:
        band = "Tiny"
    elif score >= 80: band = "Excellent"
    elif score >= 60: band = "Strong"
    elif score >= 40: band = "Usable"
    elif score >= 20: band = "Thin"
    else: band = "Tiny"

    if capital_used < 2.0: capacity_label = "tiny liquidity"
    elif capital_used < 10.0: capacity_label = "thin capacity"
    elif capital_used < 50.0: capacity_label = "usable capacity"
    else: capacity_label = "high capacity"

    reason = (f"{capacity_label}: {capital_used:.2f}% of £{bankroll:,.0f} deployable; "
              f"{bankroll_roi:.4f}% bankroll ROI; {deployed_roi:.3f}% ROI on deployed capital")
    if penalty:
        reason += f"; data-confidence adjustment −{penalty:.0f} points"
    return {"quality_score": round(score, 1), "raw_quality_score": round(raw_score, 1), "quality_band": band, "quality_reason": reason,
            "reference_bankroll": round(bankroll, 2), "deployed": round(deployed, 4),
            "unused_bankroll": round(max(0.0, bankroll - deployed), 4), "expected_profit": round(profit, 4),
            "gross_profit": round(float(sim.get("gross_profit") or 0.0), 4),
            "commission_cost": round(float(sim.get("commission_cost") or 0.0), 4),
            "bankroll_after": round(bankroll + profit, 4),
            "deployed_roi_pct": round(deployed_roi, 4), "bankroll_roi_pct": round(bankroll_roi, 6),
            "capital_used_pct": round(capital_used, 4), "limiting_leg": sim.get("limiting_leg"),
            "data_quality": data_quality or {}}


def beginner_explanation(profile: dict[str, Any], uses_delayed_feed: bool = False) -> str:
    if profile.get("quality_band") == "Invalid":
        return "This price combination is not a usable paper arbitrage after the current rules and commission model."
    bankroll = float(profile.get("reference_bankroll") or 0.0)
    deployed = float(profile.get("deployed") or 0.0)
    profit = float(profile.get("expected_profit") or 0.0)
    broi = float(profile.get("bankroll_roi_pct") or 0.0)
    droi = float(profile.get("deployed_roi_pct") or 0.0)
    used = float(profile.get("capital_used_pct") or 0.0)
    capacity = "Liquidity is the main limitation." if used < 20 else "A meaningful share of the simulated bankroll could be used."
    delayed = " Betfair contributes delayed data, so this is research evidence rather than a live executable quote." if uses_delayed_feed else ""
    return (f"£{bankroll:,.2f} would become about £{bankroll + profit:,.2f} in this paper calculation: +£{profit:,.2f}. "
            f"Only £{deployed:,.2f} ({used:.1f}%) of the bankroll could actually be used. "
            f"That is {droi:.3f}% on the money used and {broi:.4f}% on the whole bankroll. {capacity}{delayed}")


def history_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "strong_or_better": 0, "average_score": 0.0, "median_bankroll_roi_pct": 0.0,
                "expected_profit_total": 0.0, "settled_count": 0, "realized_pnl_total": 0.0}
    scores = [float(r.get("quality_score") or 0.0) for r in rows]
    brois = [float(r.get("bankroll_roi_pct") or 0.0) for r in rows]
    return {"count": len(rows), "strong_or_better": sum(1 for r in rows if r.get("quality_band") in {"Excellent", "Strong"}),
            "average_score": round(sum(scores) / len(scores), 1), "median_bankroll_roi_pct": round(median(brois), 6),
            "expected_profit_total": round(sum(float(r.get("expected_profit") or 0.0) for r in rows), 4),
            "settled_count": sum(1 for r in rows if r.get("outcome")),
            "realized_pnl_total": round(sum(float(r.get("realized_pnl") or 0.0) for r in rows if r.get("realized_pnl") is not None), 4)}
