from __future__ import annotations
from dataclasses import asdict
from itertools import product
from typing import Iterable, Mapping, Any
import hashlib
import json
from .models import Leg, Scenario


def arb_edge(legs: list[Leg]) -> float:
    if not legs or any(l.odds <= 1.0 for l in legs):
        return -100.0
    inv = sum(1.0 / l.odds for l in legs)
    return (1.0 - inv) * 100.0


def _exchange_commission_rates(legs: list[Leg]) -> dict[str, float]:
    """Return the settlement commission rate used for each exchange.

    Execution settlement already applies the most conservative captured rate on a
    venue.  The pre-trade solver uses the same rule so its guaranteed net result
    agrees with the paper execution engine rather than using a different model.
    """
    rates: dict[str, float] = {}
    for leg in legs:
        exchange = str(leg.exchange or "")
        rates[exchange] = max(rates.get(exchange, 0.0), max(0.0, float(leg.commission_pct or 0.0)) / 100.0)
    return rates


def _outcome_breakdown_raw(legs: list[Leg], stakes: list[float]) -> dict[str, dict]:
    if len(legs) != len(stakes):
        return {}
    rates = _exchange_commission_rates(legs)
    exchanges = sorted(rates)
    out: dict[str, dict] = {}
    for winner_idx, winner in enumerate(legs):
        gross_by_exchange = {exchange: 0.0 for exchange in exchanges}
        for idx, leg in enumerate(legs):
            stake = max(0.0, float(stakes[idx] or 0.0))
            if idx == winner_idx:
                pnl = stake * (float(leg.odds) - 1.0)
            else:
                pnl = -stake
            gross_by_exchange[str(leg.exchange or "")] = gross_by_exchange.get(str(leg.exchange or ""), 0.0) + pnl
        commission_by_exchange = {
            exchange: max(0.0, gross) * rates.get(exchange, 0.0)
            for exchange, gross in gross_by_exchange.items()
        }
        net_by_exchange = {
            exchange: gross_by_exchange[exchange] - commission_by_exchange.get(exchange, 0.0)
            for exchange in gross_by_exchange
        }
        gross_total = sum(gross_by_exchange.values())
        commission_total = sum(commission_by_exchange.values())
        out[str(winner.selection)] = {
            "gross_pnl": gross_total,
            "commission": commission_total,
            "net_pnl": gross_total - commission_total,
            "gross_by_exchange": gross_by_exchange,
            "commission_by_exchange": commission_by_exchange,
            "net_by_exchange": net_by_exchange,
            "winner_exchange": str(winner.exchange or ""),
            "winner_exchange_gross": gross_by_exchange.get(str(winner.exchange or ""), 0.0),
        }
    return out


def outcome_pnl_breakdown(legs: list[Leg], stakes: list[float]) -> dict[str, dict]:
    """Outcome-aware gross, commission and net P&L for a complete BACK basket."""
    raw = _outcome_breakdown_raw(legs, stakes)
    return {
        outcome: {
            "gross_pnl": round(float(row["gross_pnl"]), 8),
            "commission": round(float(row["commission"]), 8),
            "net_pnl": round(float(row["net_pnl"]), 8),
            "gross_by_exchange": {k: round(float(v), 8) for k, v in row["gross_by_exchange"].items()},
            "commission_by_exchange": {k: round(float(v), 8) for k, v in row["commission_by_exchange"].items()},
            "net_by_exchange": {k: round(float(v), 8) for k, v in row["net_by_exchange"].items()},
        }
        for outcome, row in raw.items()
    }


def outcome_pnls(legs: list[Leg], stakes: list[float]) -> dict[str, float]:
    """Return post-commission P&L for every mutually exclusive outcome."""
    return {
        outcome: round(float(row["net_pnl"]), 8)
        for outcome, row in _outcome_breakdown_raw(legs, stakes).items()
    }


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Small dependency-free Gaussian solver used by the N-outcome stake engine."""
    n = len(vector)
    if n == 0 or len(matrix) != n or any(len(row) != n for row in matrix):
        return None
    aug = [[float(x) for x in matrix[i]] + [float(vector[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        aug[col] = [x / div for x in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if abs(factor) < 1e-15:
                continue
            aug[row] = [aug[row][j] - factor * aug[col][j] for j in range(n + 1)]
    return [aug[i][-1] for i in range(n)]


def _gross_equal_weights(legs: list[Leg]) -> list[float] | None:
    if len(legs) < 2 or any(float(l.odds) <= 1.0 for l in legs):
        return None
    inv = sum(1.0 / float(l.odds) for l in legs)
    if inv <= 0.0:
        return None
    return [(1.0 / float(l.odds)) / inv for l in legs]


def _commission_aware_weights(legs: list[Leg]) -> list[float] | None:
    """Solve stake proportions that equalise *net* P&L after venue commission.

    For a positive BACK-only arb, the exchange carrying the winning selection is
    the only venue that can have positive market P&L for that outcome.  Commission
    is therefore a linear term once the venue is known.  Equating those linear
    outcome P&Ls plus ``sum(stakes)=1`` gives an exact N-runner solution without an
    external optimiser.

    The result is validated against the same piecewise settlement calculation used
    by :func:`outcome_pnls`; if the assumed commission branch does not hold, the
    solver fails closed instead of returning misleading "equal" stakes.
    """
    n = len(legs)
    if n < 2 or any(float(l.odds) <= 1.0 for l in legs):
        return None
    rates = _exchange_commission_rates(legs)
    coeffs: list[list[float]] = []
    for i, winner in enumerate(legs):
        exchange = str(winner.exchange or "")
        c = rates.get(exchange, 0.0)
        row = []
        for j, leg in enumerate(legs):
            value = -1.0 + (c if str(leg.exchange or "") == exchange else 0.0)
            if i == j:
                value += (1.0 - c) * float(winner.odds)
            row.append(value)
        coeffs.append(row)

    matrix: list[list[float]] = []
    vector: list[float] = []
    for i in range(1, n):
        matrix.append([coeffs[i][j] - coeffs[0][j] for j in range(n)])
        vector.append(0.0)
    matrix.append([1.0] * n)
    vector.append(1.0)
    weights = _solve_linear_system(matrix, vector)
    if weights is None:
        return None
    cleaned = []
    for value in weights:
        if value < -1e-9:
            return None
        cleaned.append(max(0.0, float(value)))
    total = sum(cleaned)
    if total <= 0.0:
        return None
    cleaned = [value / total for value in cleaned]

    check = _outcome_breakdown_raw(legs, cleaned)
    if len(check) != n:
        return None
    net = [float(row["net_pnl"]) for row in check.values()]
    if max(net) - min(net) > 1e-7:
        return None
    # Validate the linear branch used above.  At a useful arb scale the winning
    # exchange must be the venue with positive market P&L before commission.
    if any(float(row["winner_exchange_gross"]) < -1e-9 for row in check.values()):
        return None
    return cleaned


def _stake_plan(legs: list[Leg], deployed_limit: float, *, commission_aware: bool) -> dict:
    inv = sum(1.0 / float(l.odds) for l in legs)
    weights = _commission_aware_weights(legs) if commission_aware else None
    method = "commission_aware_net_equal_return" if weights is not None else "gross_equal_return"
    if weights is None:
        weights = _gross_equal_weights(legs)
    if not weights:
        return {"valid": False, "reason": "Unable to solve stake proportions"}

    caps = []
    for weight, leg in zip(weights, legs):
        if weight <= 1e-12:
            caps.append((float("inf"), leg))
        else:
            caps.append((max(0.0, float(leg.liquidity or 0.0)) / weight, leg))
    liquidity_cap_total, limiting_leg = min(caps, key=lambda item: item[0])
    deployed = max(0.0, min(float(deployed_limit), liquidity_cap_total))
    if deployed <= 0.0:
        return {"valid": False, "reason": "No liquidity"}
    stakes = [weight * deployed for weight in weights]
    raw = _outcome_breakdown_raw(legs, stakes)
    pnls = {outcome: float(row["net_pnl"]) for outcome, row in raw.items()}
    gross_pnls = {outcome: float(row["gross_pnl"]) for outcome, row in raw.items()}
    commissions = {outcome: float(row["commission"]) for outcome, row in raw.items()}
    guaranteed_profit = min(pnls.values())
    net_spread = max(pnls.values()) - guaranteed_profit
    gross_roi_pct = ((1.0 / inv) - 1.0) * 100.0
    gross_reference_profit = deployed * gross_roi_pct / 100.0
    commission_impact_pct = gross_roi_pct - ((guaranteed_profit / deployed) * 100.0)
    return {
        "valid": True,
        "deployed": deployed,
        "expected_profit": guaranteed_profit,
        "gross_profit": gross_reference_profit,
        "commission_cost": max(0.0, gross_reference_profit - guaranteed_profit),
        "expected_roi_pct": (guaranteed_profit / deployed) * 100.0,
        "gross_roi_pct": gross_roi_pct,
        "commission_impact_pct": max(0.0, commission_impact_pct),
        "limited_by": "liquidity" if liquidity_cap_total + 1e-9 < deployed_limit else "nominal",
        "limiting_leg": limiting_leg if liquidity_cap_total + 1e-9 < deployed_limit else None,
        "stakes": stakes,
        "outcome_pnls": pnls,
        "gross_outcome_pnls": gross_pnls,
        "commission_by_outcome": commissions,
        "commission_by_outcome_exchange": {
            outcome: dict(row["commission_by_exchange"]) for outcome, row in raw.items()
        },
        "gross_by_outcome_exchange": {
            outcome: dict(row["gross_by_exchange"]) for outcome, row in raw.items()
        },
        "net_by_outcome_exchange": {
            outcome: dict(row["net_by_exchange"]) for outcome, row in raw.items()
        },
        "staking_method": method,
        "net_equalized": method == "commission_aware_net_equal_return" and net_spread <= 1e-6,
        "net_pnl_spread": net_spread,
    }


def _rounded_plan(plan: dict, legs: list[Leg]) -> dict:
    out = dict(plan)
    out["deployed"] = round(float(plan.get("deployed") or 0.0), 4)
    for key in ("expected_profit", "gross_profit", "commission_cost"):
        out[key] = round(float(plan.get(key) or 0.0), 4)
    out["expected_roi_pct"] = round(float(plan.get("expected_roi_pct") or 0.0), 6)
    for key in ("gross_roi_pct", "commission_impact_pct"):
        out[key] = round(float(plan.get(key) or 0.0), 4)
    out["net_pnl_spread"] = round(float(plan.get("net_pnl_spread") or 0.0), 8)
    out["stakes"] = [
        {**asdict(leg), "stake": round(float(stake), 4)}
        for leg, stake in zip(legs, plan.get("stakes") or [])
    ]
    for key in ("outcome_pnls", "gross_outcome_pnls", "commission_by_outcome"):
        out[key] = {str(k): round(float(v), 4) for k, v in (plan.get(key) or {}).items()}
    for key in ("commission_by_outcome_exchange", "gross_by_outcome_exchange", "net_by_outcome_exchange"):
        out[key] = {
            str(outcome): {str(exchange): round(float(value), 4) for exchange, value in values.items()}
            for outcome, values in (plan.get(key) or {}).items()
        }
    limiting_leg = plan.get("limiting_leg")
    out["limiting_leg"] = asdict(limiting_leg) if isinstance(limiting_leg, Leg) else limiting_leg
    return out


def diagnose_equal_return(legs: list[Leg], nominal_deployed: float = 1000.0) -> dict:
    """Evaluate a complete market with auditable post-commission outcome P&L.

    When the raw book is below 100%, stake proportions are solved to equalise net
    P&L after venue commission.  Non-arbitrage books remain useful diagnostics and
    retain gross-equal staking rather than pretending a negative book is executable.
    """
    if len(legs) < 2 or any(l.odds <= 1.0 for l in legs):
        return {"valid": False, "reason": "Invalid or incomplete odds"}
    inv = sum(1.0 / l.odds for l in legs)
    theoretical = (1.0 - inv) * 100.0
    plan = _stake_plan(legs, float(nominal_deployed), commission_aware=inv < 1.0)
    if not plan.get("valid"):
        return {"valid": False, "reason": plan.get("reason") or "Unable to price market", "theoretical_edge_pct": round(theoretical, 4)}
    out = _rounded_plan(plan, legs)
    out["theoretical_edge_pct"] = round(theoretical, 4)
    return out


def simulate_equal_return(legs: list[Leg], scenario: Scenario) -> dict:
    if len(legs) < 2:
        raise ValueError("At least two mutually exclusive outcome legs required")
    if any(l.odds <= 1.0 for l in legs):
        return {"executable": False, "reason": "Invalid odds"}

    inv = sum(1.0 / l.odds for l in legs)
    if inv >= 1.0:
        return {"executable": False, "reason": "No theoretical arbitrage"}

    bankroll_cap = scenario.bankroll * min(
        max(0.0, scenario.max_bankroll_pct),
        max(0.0, scenario.max_event_exposure_pct),
    ) / 100.0
    plan = _stake_plan(legs, bankroll_cap, commission_aware=True)
    if not plan.get("valid"):
        return {"executable": False, "reason": plan.get("reason") or "Commission-aware stake solver unavailable"}
    # A theoretical arb is not execution-eligible unless every possible winner is
    # positive after exchange commission and the solver actually equalised net P&L.
    guaranteed_profit = float(plan.get("expected_profit") or 0.0)
    equalized = bool(plan.get("net_equalized"))
    executable = guaranteed_profit > 0.0 and equalized
    out = _rounded_plan(plan, legs)
    out.update({
        "executable": executable,
        "reason": None if executable else (
            "Commission-aware net P&L is not positive across every outcome"
            if equalized else "Commission-aware stake equalisation failed"
        ),
        "bankroll_roi_pct": min(
            round((guaranteed_profit / scenario.bankroll) * 100.0, 6),
            float(out.get("expected_roi_pct") or 0.0),
        ) if scenario.bankroll > 0 else 0.0,
        "capital_used_pct": round((float(plan.get("deployed") or 0.0) / scenario.bankroll) * 100.0, 4) if scenario.bankroll > 0 else 0.0,
        "theoretical_edge_pct": round((1.0 - inv) * 100.0, 4),
    })
    if out.get("limited_by") == "nominal":
        out["limited_by"] = "bankroll"
    return out


def best_back_legs(quotes_by_selection: dict[str, Iterable[Leg]], minimum_liquidity: float = 0.0) -> list[Leg]:
    """Return the highest displayed back price for each selection."""
    legs: list[Leg] = []
    for _, quotes in quotes_by_selection.items():
        valid = [q for q in quotes if q.odds > 1.0 and q.liquidity >= minimum_liquidity]
        if not valid:
            continue
        legs.append(max(valid, key=lambda q: (q.odds, q.liquidity)))
    return legs


def _routing_venue_key(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if "betfair" in text:
        return "betfair"
    if "matchbook" in text:
        return "matchbook"
    if "smarkets" in text:
        return "smarkets"
    return text.replace(" ", "_")


def _wallet_balance_score(legs: list[Leg], diagnosis: Mapping[str, Any], venue_wallets: Mapping[str, Mapping[str, Any]] | None) -> tuple[float, float, float]:
    """Higher-is-better wallet-utilisation score for economically tied books.

    It deliberately uses *post-placement utilisation*, not provider name/order.
    Missing wallet data is neutral so LIVE/research routing remains deterministic
    without inventing balances.
    """
    wallets = dict(venue_wallets or {})
    if not wallets:
        return (0.0, 0.0, 0.0)
    canonical = {_routing_venue_key(k): dict(v or {}) for k, v in wallets.items()}
    stake_by_venue: dict[str, float] = {}
    for row in diagnosis.get("stakes") or []:
        key = _routing_venue_key(row.get("venue_id") or row.get("exchange"))
        stake_by_venue[key] = stake_by_venue.get(key, 0.0) + max(0.0, float(row.get("stake") or 0.0))
    utilisations: list[float] = []
    projected_free_shares: list[float] = []
    for key, stake in stake_by_venue.items():
        wallet = canonical.get(key) or {}
        available = max(0.0, float(wallet.get("available", wallet.get("available_balance", 0.0)) or 0.0))
        reserved = max(0.0, float(wallet.get("reserved", wallet.get("reserved_balance", 0.0)) or 0.0))
        equity = max(0.0, float(wallet.get("equity", available + reserved) or 0.0))
        if equity <= 1e-9:
            continue
        utilisations.append((reserved + stake) / equity)
        projected_free_shares.append(max(0.0, available - stake) / equity)
    if not utilisations:
        return (0.0, 0.0, 0.0)
    max_util = max(utilisations)
    util_spread = max(utilisations) - min(utilisations) if len(utilisations) > 1 else max_util
    free_spread = max(projected_free_shares) - min(projected_free_shares) if len(projected_free_shares) > 1 else 0.0
    return (-max_util, -util_spread, -free_spread)


def _route_neutral_score(legs: list[Leg]) -> tuple[float, float, float]:
    """Provider-order-independent final tie-break.

    Prefer a more even leg distribution, then usable depth.  If still tied, use a
    stable hash of selection->venue routing so reversing quote/provider enumeration
    cannot change the result and neither venue is privileged as 'first'.
    """
    counts: dict[str, int] = {}
    for leg in legs:
        key = _routing_venue_key(leg.resolved_venue_id or leg.exchange)
        counts[key] = counts.get(key, 0) + 1
    count_spread = (max(counts.values()) - min(counts.values())) if counts else 0
    depth = sum(max(0.0, float(l.liquidity or 0.0)) for l in legs)
    signature = "|".join(sorted(f"{str(l.selection).strip().lower()}->{_routing_venue_key(l.resolved_venue_id or l.exchange)}" for l in legs))
    digest = int(hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16], 16)
    return (-float(count_spread), depth, -float(digest))


def _strategy_candidates(
    quotes_by_selection: dict[str, Iterable[Leg]],
    minimum_liquidity: float,
    require_cross_exchange: bool,
) -> list[dict[str, Any]]:
    selections = list(quotes_by_selection.keys())
    if len(selections) < 2:
        return []
    choices: list[list[Leg]] = []
    for selection in selections:
        valid = [q for q in quotes_by_selection[selection] if q.odds > 1.0 and q.liquidity >= minimum_liquidity]
        if not valid:
            return []
        # Canonicalise candidate order before product enumeration.  Selection
        # behaviour therefore cannot depend on Betfair-first/Matchbook-first input.
        valid.sort(key=lambda q: (_routing_venue_key(q.resolved_venue_id or q.exchange), str(q.selection_id or q.selection), -float(q.odds), -float(q.liquidity)))
        choices.append(valid)
    rows: list[dict[str, Any]] = []
    for combo in product(*choices):
        legs = list(combo)
        if require_cross_exchange and len({l.resolved_venue_id for l in legs}) < 2:
            continue
        diag = diagnose_equal_return(legs, 1000.0)
        if not diag.get("valid"):
            continue
        rows.append({
            "legs": legs, "diagnosis": diag,
            "expected_profit": float(diag.get("expected_profit", -1e9)),
            "expected_roi": float(diag.get("expected_roi_pct", -1e9)),
            "deployed": float(diag.get("deployed", 0.0)),
        })
    return rows


def best_strategy_legs(
    quotes_by_selection: dict[str, Iterable[Leg]],
    minimum_liquidity: float = 0.0,
    require_cross_exchange: bool = True,
    venue_wallets: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Leg]:
    """Choose the best complete strategy without provider-order routing bias.

    Guaranteed portfolio profit remains primary.  Only books that are effectively
    economically equivalent (within one penny of guaranteed profit and 0.001 ROI
    percentage point) enter the wallet-balancing tie-break.
    """
    rows = _strategy_candidates(quotes_by_selection, minimum_liquidity, require_cross_exchange)
    if not rows:
        return []
    positive = [r for r in rows if r["expected_profit"] > 0.0]
    if not positive:
        # Preserve the established diagnostic behaviour for non-arbitrage books.
        return max(rows, key=lambda r: (r["expected_roi"], r["deployed"], r["expected_profit"], _route_neutral_score(r["legs"])))["legs"]

    economic_best = max(positive, key=lambda r: (r["expected_profit"], r["expected_roi"], r["deployed"]))
    profit_tol = max(0.01, abs(economic_best["expected_profit"]) * 1e-6)
    roi_tol = 0.001
    tied = [r for r in positive if economic_best["expected_profit"] - r["expected_profit"] <= profit_tol and economic_best["expected_roi"] - r["expected_roi"] <= roi_tol]
    return max(
        tied,
        key=lambda r: (
            _wallet_balance_score(r["legs"], r["diagnosis"], venue_wallets),
            _route_neutral_score(r["legs"]),
            r["expected_profit"], r["expected_roi"], r["deployed"],
        ),
    )["legs"]


def strategy_routing_diagnostics(
    quotes_by_selection: dict[str, Iterable[Leg]],
    selected_legs: list[Leg],
    minimum_liquidity: float = 0.0,
    require_cross_exchange: bool = True,
    venue_wallets: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Auditable routing explanation for the selected strategy."""
    rows = [r for r in _strategy_candidates(quotes_by_selection, minimum_liquidity, require_cross_exchange) if r["expected_profit"] > 0.0]
    if not rows or not selected_legs:
        return {"economic_tie": False, "reason": "no_positive_complete_route", "alternatives": []}
    selected_sig = sorted((str(l.selection), _routing_venue_key(l.resolved_venue_id or l.exchange)) for l in selected_legs)
    selected = next((r for r in rows if sorted((str(l.selection), _routing_venue_key(l.resolved_venue_id or l.exchange)) for l in r["legs"]) == selected_sig), None)
    selected = selected or max(rows, key=lambda r: (r["expected_profit"], r["expected_roi"], r["deployed"]))
    profit_tol = max(0.01, abs(selected["expected_profit"]) * 1e-6)
    roi_tol = 0.001
    equivalents = [r for r in rows if abs(selected["expected_profit"] - r["expected_profit"]) <= profit_tol and abs(selected["expected_roi"] - r["expected_roi"]) <= roi_tol]
    def route(r):
        return {
            "legs": [{"selection": str(l.selection), "venue_id": _routing_venue_key(l.resolved_venue_id or l.exchange), "exchange": str(l.exchange), "odds": float(l.odds)} for l in r["legs"]],
            "guaranteed_profit": round(r["expected_profit"], 6),
            "roi_pct": round(r["expected_roi"], 6),
            "deployed": round(r["deployed"], 4),
            "wallet_score": [round(float(x), 8) for x in _wallet_balance_score(r["legs"], r["diagnosis"], venue_wallets)],
        }
    alternatives = [route(r) for r in equivalents if r is not selected][:8]
    selected_route = route(selected)
    counts: dict[str, int] = {}
    for leg in selected_route["legs"]:
        key = str(leg.get("venue_id") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    favourite = min(selected_route["legs"], key=lambda leg: float(leg.get("odds") or 1e9)) if selected_route["legs"] else None
    return {
        "economic_tie": len(equivalents) > 1,
        "tie_tolerance_profit": profit_tol,
        "tie_tolerance_roi_pct": roi_tol,
        "selected": selected_route,
        "selected_legs_per_exchange": counts,
        "favourite_exchange": (favourite or {}).get("venue_id"),
        "favourite_selection": (favourite or {}).get("selection"),
        "alternatives": alternatives,
        "reason": "wallet_balance_tiebreak" if len(equivalents) > 1 and venue_wallets else "venue_neutral_tiebreak" if len(equivalents) > 1 else "best_economic_route",
    }


def book_percentage(legs: list[Leg]) -> float | None:
    """Return the implied book percentage for a complete set of back legs."""
    if len(legs) < 2 or any(l.odds <= 1.0 for l in legs):
        return None
    return round(sum(1.0 / l.odds for l in legs) * 100.0, 6)


def strategy_book_analysis(
    quotes_by_selection: dict[str, Iterable[Leg]],
    minimum_liquidity: float = 0.0,
    require_cross_exchange: bool = True,
) -> dict:
    """Explain the books behind a strategy selection without changing execution.

    ``best_combined`` is the raw highest displayed back price for each outcome,
    regardless of venue. ``selected`` is the combination chosen by
    :func:`best_strategy_legs` under the configured liquidity/cross-exchange rules.
    Per-exchange books make it possible to tell whether a poor selected ROI is a
    market reality or a combination-selection artefact.
    """
    selections = list(quotes_by_selection.keys())
    if len(selections) < 2:
        return {"valid": False, "reason": "Incomplete selection set"}

    selected_legs = best_strategy_legs(
        quotes_by_selection, minimum_liquidity=minimum_liquidity,
        require_cross_exchange=require_cross_exchange,
    )
    selected_diag = diagnose_equal_return(selected_legs, 1000.0) if selected_legs else {"valid": False}

    best_combined_legs = best_back_legs(quotes_by_selection, minimum_liquidity=0.0)
    if len(best_combined_legs) != len(selections):
        best_combined_legs = []

    exchanges = sorted({leg.exchange for quotes in quotes_by_selection.values() for leg in quotes})
    exchange_books: dict[str, float | None] = {}
    for exchange in exchanges:
        by_selection = {
            selection: [leg for leg in quotes_by_selection[selection] if leg.exchange == exchange]
            for selection in selections
        }
        if any(not rows for rows in by_selection.values()):
            exchange_books[exchange] = None
            continue
        legs = best_back_legs(by_selection, minimum_liquidity=0.0)
        exchange_books[exchange] = book_percentage(legs) if len(legs) == len(selections) else None

    expected_profit = float(selected_diag.get("expected_profit", 0.0)) if selected_diag.get("valid") else None
    return {
        "valid": bool(selected_diag.get("valid")),
        "selected_legs": selected_legs,
        "selected_diagnostic": selected_diag,
        "selection_basis": (
            "positive_profit" if expected_profit is not None and expected_profit > 0.0
            else "best_roi_non_positive" if selected_diag.get("valid")
            else "unavailable"
        ),
        "selected_cross_exchange_book_pct": book_percentage(selected_legs),
        "best_combined_book_pct": book_percentage(best_combined_legs),
        "best_combined_legs": best_combined_legs,
        "exchange_books_pct": exchange_books,
    }
