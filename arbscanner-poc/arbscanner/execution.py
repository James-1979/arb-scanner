from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Mapping
from uuid import uuid4

from .models import Leg
from .venues import provider_id_for_name, venue_identity_for_name


class OrderSide(str, Enum):
    BACK = "BACK"
    LAY = "LAY"


class ExecutionState(str, Enum):
    READY = "READY"
    SUBMITTING = "SUBMITTING"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    HEDGING = "HEDGING"
    HEDGED = "HEDGED"
    COMPLETE = "COMPLETE"
    PANIC = "PANIC"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True)
class ExecutionLeg:
    index: int
    exchange: str
    selection: str
    requested_odds: float
    requested_stake: float
    liquidity: float
    commission_pct: float = 0.0
    side: OrderSide = OrderSide.BACK
    event_id: str | None = None
    market_id: str | None = None
    selection_id: str | None = None
    max_slippage_pct: float = 0.50
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None
    currency: str = "GBP"
    canonical_event_id: str | None = None
    canonical_market_id: str | None = None
    canonical_selection_id: str | None = None

    @property
    def resolved_venue_id(self) -> str:
        return self.venue_id or venue_identity_for_name(self.exchange).venue_id

    @property
    def resolved_provider_id(self) -> str:
        return self.provider_id or provider_id_for_name(self.exchange)

    @property
    def capital_required(self) -> float:
        if self.side == OrderSide.LAY:
            return max(0.0, self.requested_stake * (self.requested_odds - 1.0))
        return max(0.0, self.requested_stake)


@dataclass(frozen=True)
class ExecutionPlan:
    id: str
    opportunity_id: int | None
    event_name: str
    market_name: str
    outcomes: tuple[str, ...]
    legs: tuple[ExecutionLeg, ...]
    expected_profit: float
    expected_roi_pct: float
    deployed: float
    target_outcome_pnls: dict[str, float]
    created_at: str
    expires_at: str
    max_unhedged_exposure: float = 25.0
    hedge_reserve_pct: float = 20.0
    in_play: bool = False
    live_execution_allowed: bool = False

    def as_dict(self) -> dict:
        payload = asdict(self)
        by_exchange: dict[str, float] = {}
        by_venue: dict[str, float] = {}
        for leg_obj, leg in zip(self.legs, payload["legs"]):
            leg["side"] = str(leg["side"]).split(".")[-1]
            by_exchange[leg_obj.exchange] = by_exchange.get(leg_obj.exchange, 0.0) + leg_obj.capital_required
            by_venue[leg_obj.resolved_venue_id] = by_venue.get(leg_obj.resolved_venue_id, 0.0) + leg_obj.capital_required
            leg["venue_id"] = leg_obj.resolved_venue_id
            leg["provider_id"] = leg_obj.resolved_provider_id
        payload["capital_required_by_exchange"] = {k: round(v, 4) for k, v in sorted(by_exchange.items())}
        payload["capital_required_by_venue"] = {k: round(v, 4) for k, v in sorted(by_venue.items())}
        payload["recommended_hedge_reserve"] = round(self.deployed * self.hedge_reserve_pct / 100.0, 4)
        return payload


@dataclass(frozen=True)
class Fill:
    fill_id: str
    client_order_id: str
    leg_index: int | None
    exchange: str
    selection: str
    side: OrderSide
    odds: float
    stake: float
    commission_pct: float = 0.0
    is_hedge: bool = False
    matched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None
    currency: str = "GBP"
    external_order_id: str | None = None
    provider_metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["side"] = self.side.value
        return payload


@dataclass(frozen=True)
class HedgeQuote:
    exchange: str
    selection: str
    odds: float
    commission_pct: float = 0.0
    side: OrderSide = OrderSide.BACK
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None


@dataclass(frozen=True)
class HedgeInstruction:
    exchange: str
    selection: str
    side: OrderSide
    odds: float
    stake: float
    commission_pct: float
    venue_id: str | None = None
    provider_id: str | None = None
    underlying_venue_id: str | None = None

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["side"] = self.side.value
        return payload


@dataclass(frozen=True)
class PositionSnapshot:
    outcome_pnls: dict[str, float]
    target_deviation_pnls: dict[str, float]
    worst_case_pnl: float
    best_case_pnl: float
    pnl_spread: float
    exposure_spread: float
    worst_case_loss: float
    balanced: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionResult:
    plan_id: str
    state: ExecutionState
    fills: list[Fill]
    hedge_instructions: list[HedgeInstruction]
    before_hedge: PositionSnapshot
    after_hedge: PositionSnapshot
    events: list[dict]
    theoretical_profit: float
    captured_profit: float
    execution_leakage: float

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "state": self.state.value,
            "fills": [x.as_dict() for x in self.fills],
            "hedge_instructions": [x.as_dict() for x in self.hedge_instructions],
            "before_hedge": self.before_hedge.as_dict(),
            "after_hedge": self.after_hedge.as_dict(),
            "events": list(self.events),
            "theoretical_profit": round(self.theoretical_profit, 4),
            "captured_profit": round(self.captured_profit, 4),
            "execution_leakage": round(self.execution_leakage, 4),
        }


@dataclass
class VenueCapital:
    balance: float
    reserved_normal: float = 0.0
    reserved_hedge: float = 0.0


class CapitalLedger:
    """Venue-aware paper capital reservations.

    Normal orders may not consume the configured hedge reserve. This is a local
    deterministic ledger only; it does not query or mutate an exchange account.
    """

    def __init__(self, balances: Mapping[str, float], hedge_reserve_pct: float = 20.0):
        self.hedge_reserve_pct = min(100.0, max(0.0, float(hedge_reserve_pct)))
        self.venues = {str(k): VenueCapital(max(0.0, float(v))) for k, v in balances.items()}
        self._reservations: dict[str, dict[str, float]] = {}

    def _venue(self, exchange: str) -> VenueCapital:
        if exchange not in self.venues:
            self.venues[exchange] = VenueCapital(0.0)
        return self.venues[exchange]

    def reserve_floor(self, exchange: str) -> float:
        venue = self._venue(exchange)
        return venue.balance * self.hedge_reserve_pct / 100.0

    def free_for_normal(self, exchange: str) -> float:
        venue = self._venue(exchange)
        return max(0.0, venue.balance - venue.reserved_normal - venue.reserved_hedge - self.reserve_floor(exchange))

    def free_for_hedge(self, exchange: str) -> float:
        venue = self._venue(exchange)
        return max(0.0, venue.balance - venue.reserved_normal - venue.reserved_hedge)

    def can_reserve(self, plan: ExecutionPlan) -> tuple[bool, dict[str, float]]:
        needs: dict[str, float] = {}
        for leg in plan.legs:
            needs[leg.exchange] = needs.get(leg.exchange, 0.0) + leg.capital_required
        shortfall = {
            exchange: round(max(0.0, need - self.free_for_normal(exchange)), 4)
            for exchange, need in needs.items()
            if need > self.free_for_normal(exchange) + 1e-9
        }
        return (not shortfall, shortfall)

    def reserve(self, plan: ExecutionPlan) -> bool:
        ok, _ = self.can_reserve(plan)
        if not ok:
            return False
        needs: dict[str, float] = {}
        for leg in plan.legs:
            needs[leg.exchange] = needs.get(leg.exchange, 0.0) + leg.capital_required
        for exchange, amount in needs.items():
            self._venue(exchange).reserved_normal += amount
        self._reservations[plan.id] = needs
        return True

    def release(self, plan_id: str) -> None:
        for exchange, amount in self._reservations.pop(plan_id, {}).items():
            venue = self._venue(exchange)
            venue.reserved_normal = max(0.0, venue.reserved_normal - amount)

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            exchange: {
                "balance": round(v.balance, 4),
                "reserved_normal": round(v.reserved_normal, 4),
                "reserved_hedge": round(v.reserved_hedge, 4),
                "hedge_reserve_floor": round(self.reserve_floor(exchange), 4),
                "free_for_normal": round(self.free_for_normal(exchange), 4),
                "free_for_hedge": round(self.free_for_hedge(exchange), 4),
            }
            for exchange, v in sorted(self.venues.items())
        }


def build_execution_plan(
    legs: list[Leg],
    simulation: Mapping,
    *,
    opportunity_id: int | None = None,
    event_name: str = "",
    market_name: str = "",
    ttl_ms: int = 1500,
    max_slippage_pct: float = 0.50,
    max_unhedged_exposure: float = 25.0,
    hedge_reserve_pct: float = 20.0,
) -> ExecutionPlan:
    if not simulation.get("executable"):
        raise ValueError("Cannot build an execution plan for a non-executable simulation")
    stake_rows = list(simulation.get("stakes") or [])
    if len(stake_rows) != len(legs):
        raise ValueError("Simulation stakes do not match opportunity legs")
    now = datetime.now(timezone.utc)
    plan_legs = []
    for idx, (leg, row) in enumerate(zip(legs, stake_rows)):
        plan_legs.append(ExecutionLeg(
            index=idx,
            exchange=leg.exchange,
            selection=leg.selection,
            requested_odds=float(leg.odds),
            requested_stake=float(row.get("stake") or 0.0),
            liquidity=float(leg.liquidity),
            commission_pct=float(leg.commission_pct),
            side=OrderSide.BACK,
            event_id=leg.event_id,
            market_id=leg.market_id,
            selection_id=leg.selection_id,
            max_slippage_pct=max(0.0, float(max_slippage_pct)),
            venue_id=leg.resolved_venue_id,
            provider_id=leg.resolved_provider_id,
            underlying_venue_id=leg.underlying_venue_id,
            currency=leg.currency,
            canonical_event_id=leg.canonical_event_id,
            canonical_market_id=leg.canonical_market_id,
            canonical_selection_id=leg.canonical_selection_id,
        ))
    return ExecutionPlan(
        id=f"PAPER-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}",
        opportunity_id=opportunity_id,
        event_name=event_name,
        market_name=market_name,
        outcomes=tuple(leg.selection for leg in legs),
        legs=tuple(plan_legs),
        expected_profit=float(simulation.get("expected_profit") or 0.0),
        expected_roi_pct=float(simulation.get("expected_roi_pct") or 0.0),
        deployed=float(simulation.get("deployed") or 0.0),
        target_outcome_pnls={str(k): float(v) for k, v in (simulation.get("outcome_pnls") or {}).items()},
        created_at=now.isoformat(),
        expires_at=(now + timedelta(milliseconds=max(100, int(ttl_ms)))).isoformat(),
        max_unhedged_exposure=max(0.0, float(max_unhedged_exposure)),
        hedge_reserve_pct=min(100.0, max(0.0, float(hedge_reserve_pct))),
        in_play=any(bool(l.in_play) for l in legs),
        live_execution_allowed=False,
    )


def _unique_fills(fills: Iterable[Fill]) -> list[Fill]:
    seen: set[str] = set()
    out: list[Fill] = []
    for fill in fills:
        if fill.fill_id in seen:
            continue
        seen.add(fill.fill_id)
        out.append(fill)
    return out


def position_snapshot(
    outcomes: Iterable[str],
    fills: Iterable[Fill],
    balance_tolerance: float = 0.05,
    target_outcome_pnls: Mapping[str, float] | None = None,
) -> PositionSnapshot:
    outcomes = tuple(outcomes)
    unique = _unique_fills(fills)
    if not outcomes:
        return PositionSnapshot({}, {}, 0.0, 0.0, 0.0, 0.0, 0.0, True)

    outcome_pnls: dict[str, float] = {}
    for outcome in outcomes:
        exchange_pnl: dict[str, float] = {}
        exchange_commission: dict[str, float] = {}
        for fill in unique:
            stake = max(0.0, float(fill.stake))
            odds = max(1.0, float(fill.odds))
            is_winner = str(fill.selection).strip().lower() == str(outcome).strip().lower()
            if fill.side == OrderSide.LAY:
                pnl = -stake * (odds - 1.0) if is_winner else stake
            else:
                pnl = stake * (odds - 1.0) if is_winner else -stake
            exchange_pnl[fill.exchange] = exchange_pnl.get(fill.exchange, 0.0) + pnl
            exchange_commission[fill.exchange] = max(
                exchange_commission.get(fill.exchange, 0.0), max(0.0, float(fill.commission_pct)) / 100.0
            )
        total = 0.0
        for exchange, market_pnl in exchange_pnl.items():
            total += market_pnl - (max(0.0, market_pnl) * exchange_commission.get(exchange, 0.0))
        outcome_pnls[str(outcome)] = round(total, 8)

    target = {str(k): float(v) for k, v in (target_outcome_pnls or {}).items()}
    deviations = {
        str(outcome): outcome_pnls[str(outcome)] - target.get(str(outcome), 0.0)
        for outcome in outcomes
    }
    vals = list(outcome_pnls.values())
    dev_vals = list(deviations.values())
    worst = min(vals)
    best = max(vals)
    spread = best - worst
    exposure_spread = max(dev_vals) - min(dev_vals)
    return PositionSnapshot(
        outcome_pnls={k: round(v, 4) for k, v in outcome_pnls.items()},
        target_deviation_pnls={k: round(v, 4) for k, v in deviations.items()},
        worst_case_pnl=round(worst, 4),
        best_case_pnl=round(best, 4),
        pnl_spread=round(spread, 4),
        exposure_spread=round(exposure_spread, 4),
        worst_case_loss=round(max(0.0, -worst), 4),
        balanced=exposure_spread <= max(0.0, float(balance_tolerance)),
    )


def hedge_quotes_from_plan(plan: ExecutionPlan, odds_multiplier: float = 1.0) -> dict[str, HedgeQuote]:
    out: dict[str, HedgeQuote] = {}
    for leg in plan.legs:
        out[leg.selection] = HedgeQuote(
            exchange=leg.exchange,
            selection=leg.selection,
            odds=max(1.01, float(leg.requested_odds) * max(0.01, float(odds_multiplier))),
            commission_pct=leg.commission_pct,
            side=OrderSide.BACK,
            venue_id=leg.resolved_venue_id,
            provider_id=leg.resolved_provider_id,
            underlying_venue_id=leg.underlying_venue_id,
        )
    return out


def calculate_back_hedges(
    outcomes: Iterable[str],
    fills: Iterable[Fill],
    quotes: Mapping[str, HedgeQuote],
    *,
    balance_tolerance: float = 0.05,
    max_iterations: int = 8,
    target_outcome_pnls: Mapping[str, float] | None = None,
) -> tuple[list[HedgeInstruction], PositionSnapshot]:
    """Equalise outcome P&L with BACK hedges, correcting for commission iteratively."""
    working = list(_unique_fills(fills))
    instructions: list[HedgeInstruction] = []
    outcomes = tuple(outcomes)

    for _ in range(max(1, int(max_iterations))):
        snap = position_snapshot(outcomes, working, balance_tolerance, target_outcome_pnls)
        if snap.balanced:
            return instructions, snap
        target = max(snap.target_deviation_pnls.values())
        pass_instructions: list[HedgeInstruction] = []
        for outcome in outcomes:
            gap = target - float(snap.target_deviation_pnls.get(outcome, 0.0))
            if gap <= balance_tolerance:
                continue
            quote = quotes.get(outcome)
            if not quote or quote.side != OrderSide.BACK or quote.odds <= 1.0:
                continue
            stake = gap / quote.odds
            if stake <= 0.0001:
                continue
            pass_instructions.append(HedgeInstruction(
                exchange=quote.exchange,
                selection=outcome,
                side=OrderSide.BACK,
                odds=float(quote.odds),
                stake=round(stake, 6),
                commission_pct=float(quote.commission_pct),
                venue_id=quote.venue_id or venue_identity_for_name(quote.exchange).venue_id,
                provider_id=quote.provider_id or provider_id_for_name(quote.exchange),
                underlying_venue_id=quote.underlying_venue_id,
            ))
        if not pass_instructions:
            break
        for instruction in pass_instructions:
            instructions.append(instruction)
            working.append(Fill(
                fill_id=f"hedge-{uuid4().hex}",
                client_order_id=f"hedge-{uuid4().hex[:12]}",
                leg_index=None,
                exchange=instruction.exchange,
                selection=instruction.selection,
                side=instruction.side,
                odds=instruction.odds,
                stake=instruction.stake,
                commission_pct=instruction.commission_pct,
                is_hedge=True,
                venue_id=instruction.venue_id or venue_identity_for_name(instruction.exchange).venue_id,
                provider_id=instruction.provider_id or provider_id_for_name(instruction.exchange),
                underlying_venue_id=instruction.underlying_venue_id,
            ))

    return instructions, position_snapshot(outcomes, working, balance_tolerance, target_outcome_pnls)


def order_capital_required(side: OrderSide, odds: float, stake: float) -> float:
    """Cash/liability required to carry one matched order."""
    stake = max(0.0, float(stake or 0.0))
    odds = max(1.0, float(odds or 1.0))
    if side == OrderSide.LAY:
        return stake * max(0.0, odds - 1.0)
    return stake


def capital_required_by_exchange_from_fills(fills: Iterable[Fill]) -> dict[str, float]:
    out: dict[str, float] = {}
    for fill in _unique_fills(fills):
        amount = order_capital_required(fill.side, fill.odds, fill.stake)
        out[exchange_key(fill.exchange)] = out.get(exchange_key(fill.exchange), 0.0) + amount
    return {k: round(v, 8) for k, v in out.items()}


def exchange_outcome_pnls_from_fills(outcomes: Iterable[str], fills: Iterable[Fill]) -> dict[str, dict[str, float]]:
    """Outcome P&L split by venue from the fills that actually occurred.

    This is the settlement representation used by Monitor execution modelling.
    It deliberately works from fills rather than the pre-trade equal-return
    simulation, so partial/rejected legs and emergency hedges flow through to
    the final settled wallet P&L.
    """
    unique = _unique_fills(fills)
    result: dict[str, dict[str, float]] = {}
    for outcome in tuple(str(x) for x in outcomes):
        gross: dict[str, float] = {}
        commission: dict[str, float] = {}
        for fill in unique:
            exchange = exchange_key(fill.exchange)
            stake = max(0.0, float(fill.stake or 0.0))
            odds = max(1.0, float(fill.odds or 1.0))
            wins = str(fill.selection).strip().lower() == outcome.strip().lower()
            if fill.side == OrderSide.LAY:
                pnl = -stake * (odds - 1.0) if wins else stake
            else:
                pnl = stake * (odds - 1.0) if wins else -stake
            gross[exchange] = gross.get(exchange, 0.0) + pnl
            commission[exchange] = max(
                commission.get(exchange, 0.0),
                max(0.0, float(fill.commission_pct or 0.0)) / 100.0,
            )
        result[outcome] = {
            exchange: round(value - max(0.0, value) * commission.get(exchange, 0.0), 8)
            for exchange, value in gross.items()
        }
    return result


class PaperExecutionCoordinator:
    """Paper/monitor_timing execution state machine. It never sends an exchange order."""

    def __init__(self, balance_tolerance: float = 0.05):
        self.balance_tolerance = max(0.0, float(balance_tolerance))

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        fill_fractions: Mapping[int, float] | None = None,
        fill_odds: Mapping[int, float] | None = None,
        hedge_quotes: Mapping[str, HedgeQuote] | None = None,
        hedge_capital_by_exchange: Mapping[str, float] | None = None,
        auto_hedge: bool = True,
    ) -> ExecutionResult:
        if plan.live_execution_allowed:
            raise RuntimeError("PaperExecutionCoordinator refuses live execution plans")

        fill_fractions = dict(fill_fractions or {})
        fill_odds = dict(fill_odds or {})
        events: list[dict] = [{"state": ExecutionState.SUBMITTING.value, "at": datetime.now(timezone.utc).isoformat()}]
        fills: list[Fill] = []

        for leg in plan.legs:
            fraction = min(1.0, max(0.0, float(fill_fractions.get(leg.index, 1.0))))
            stake = leg.requested_stake * fraction
            if stake <= 0.0:
                events.append({"state": "LEG_FAILED", "leg_index": leg.index, "exchange": leg.exchange, "venue_id": leg.resolved_venue_id, "provider_id": leg.resolved_provider_id, "selection": leg.selection})
                continue
            odds = max(1.01, float(fill_odds.get(leg.index, leg.requested_odds)))
            fills.append(Fill(
                fill_id=f"paper-{plan.id}-{leg.index}",
                client_order_id=f"{plan.id}-{leg.index}",
                leg_index=leg.index,
                exchange=leg.exchange,
                selection=leg.selection,
                side=leg.side,
                odds=odds,
                stake=round(stake, 6),
                commission_pct=leg.commission_pct,
                venue_id=leg.resolved_venue_id,
                provider_id=leg.resolved_provider_id,
                underlying_venue_id=leg.underlying_venue_id,
                currency=leg.currency,
            ))
            events.append({
                "state": "LEG_FILLED" if fraction >= 0.999999 else "LEG_PARTIAL",
                "leg_index": leg.index,
                "exchange": leg.exchange,
                "venue_id": leg.resolved_venue_id,
                "provider_id": leg.resolved_provider_id,
                "selection": leg.selection,
                "fraction": round(fraction, 6),
                "stake": round(stake, 4),
                "odds": round(odds, 4),
            })

        before = position_snapshot(plan.outcomes, fills, self.balance_tolerance, plan.target_outcome_pnls)
        if not before.balanced and before.exposure_spread > plan.max_unhedged_exposure:
            events.append({
                "state": "EMERGENCY_HEDGE",
                "exposure_spread": round(before.exposure_spread, 4),
                "limit": round(plan.max_unhedged_exposure, 4),
                "at": datetime.now(timezone.utc).isoformat(),
            })
        all_full = all(float(fill_fractions.get(leg.index, 1.0)) >= 0.999999 for leg in plan.legs)
        if before.balanced and all_full:
            state = ExecutionState.COMPLETE
            after = before
            hedge_instructions: list[HedgeInstruction] = []
        elif not fills:
            state = ExecutionState.FAILED
            after = before
            hedge_instructions = []
        elif not auto_hedge:
            state = ExecutionState.PARTIAL
            after = before
            hedge_instructions = []
        else:
            events.append({"state": ExecutionState.HEDGING.value, "at": datetime.now(timezone.utc).isoformat()})
            effective_hedge_quotes = hedge_quotes_from_plan(plan) if hedge_quotes is None else dict(hedge_quotes)
            hedge_instructions, unconstrained_after = calculate_back_hedges(
                plan.outcomes,
                fills,
                effective_hedge_quotes,
                balance_tolerance=self.balance_tolerance,
                target_outcome_pnls=plan.target_outcome_pnls,
            )
            remaining_hedge_capital = None
            if hedge_capital_by_exchange is not None:
                remaining_hedge_capital = {
                    exchange_key(str(k)): max(0.0, float(v or 0.0))
                    for k, v in hedge_capital_by_exchange.items()
                }
            for idx, instruction in enumerate(hedge_instructions):
                actual_stake = float(instruction.stake)
                if remaining_hedge_capital is not None:
                    exchange = exchange_key(instruction.exchange)
                    available = max(0.0, float(remaining_hedge_capital.get(exchange, 0.0)))
                    requested_capital = order_capital_required(instruction.side, instruction.odds, instruction.stake)
                    if requested_capital > available + 1e-12:
                        if requested_capital <= 0.0 or available <= 0.0:
                            actual_stake = 0.0
                        elif instruction.side == OrderSide.LAY:
                            liability_per_unit = max(1e-12, float(instruction.odds) - 1.0)
                            actual_stake = min(float(instruction.stake), available / liability_per_unit)
                        else:
                            actual_stake = min(float(instruction.stake), available)
                        events.append({
                            "state": "HEDGE_CAPITAL_LIMITED",
                            "exchange": instruction.exchange,
                            "selection": instruction.selection,
                            "requested_stake": round(float(instruction.stake), 6),
                            "filled_stake": round(actual_stake, 6),
                            "available_capital": round(available, 4),
                        })
                    used_capital = order_capital_required(instruction.side, instruction.odds, actual_stake)
                    remaining_hedge_capital[exchange] = max(0.0, available - used_capital)
                if actual_stake <= 0.000001:
                    events.append({
                        "state": "HEDGE_REJECTED_NO_CAPITAL",
                        "exchange": instruction.exchange,
                        "selection": instruction.selection,
                    })
                    continue
                fills.append(Fill(
                    fill_id=f"paper-{plan.id}-hedge-{idx}",
                    client_order_id=f"{plan.id}-hedge-{idx}",
                    leg_index=None,
                    exchange=instruction.exchange,
                    selection=instruction.selection,
                    side=instruction.side,
                    odds=instruction.odds,
                    stake=round(actual_stake, 6),
                    commission_pct=instruction.commission_pct,
                    is_hedge=True,
                    venue_id=instruction.venue_id or venue_identity_for_name(instruction.exchange).venue_id,
                    provider_id=instruction.provider_id or provider_id_for_name(instruction.exchange),
                    underlying_venue_id=instruction.underlying_venue_id,
                ))
            if remaining_hedge_capital is None:
                after = unconstrained_after
            else:
                after = position_snapshot(
                    plan.outcomes,
                    fills,
                    self.balance_tolerance,
                    plan.target_outcome_pnls,
                )
            state = ExecutionState.HEDGED if after.balanced else ExecutionState.PANIC

        events.append({"state": state.value, "at": datetime.now(timezone.utc).isoformat()})
        captured = float(after.worst_case_pnl)
        theoretical = float(plan.expected_profit)
        return ExecutionResult(
            plan_id=plan.id,
            state=state,
            fills=fills,
            hedge_instructions=hedge_instructions,
            before_hedge=before,
            after_hedge=after,
            events=events,
            theoretical_profit=theoretical,
            captured_profit=captured,
            execution_leakage=theoretical - captured,
        )


def stress_test_plan(plan: ExecutionPlan, worse_hedge_odds_pct: float = 0.50) -> list[dict]:
    coordinator = PaperExecutionCoordinator()
    scenarios: list[tuple[str, dict[int, float], float]] = [("all legs fill", {}, 1.0)]
    for leg in plan.legs:
        scenarios.append((f"{leg.exchange} / {leg.selection}: 40% fill", {leg.index: 0.40}, 1.0))
        scenarios.append((f"{leg.exchange} / {leg.selection}: rejected", {leg.index: 0.0}, 1.0))
    scenarios.append((
        f"one partial + hedge price worsens {worse_hedge_odds_pct:.2f}%",
        {plan.legs[0].index: 0.40},
        1.0 - worse_hedge_odds_pct / 100.0,
    ))

    rows = []
    for name, fractions, hedge_mult in scenarios:
        result = coordinator.execute(
            plan,
            fill_fractions=fractions,
            hedge_quotes=hedge_quotes_from_plan(plan, odds_multiplier=hedge_mult),
            auto_hedge=True,
        )
        rows.append({"name": name, **result.as_dict()})
    return rows


def venue_key(name: str) -> str:
    """Stable capital/analytics key for any trading venue/provider display name."""
    return venue_identity_for_name(name).venue_id


def exchange_key(name: str) -> str:
    """Compatibility alias retained for existing Betfair/Matchbook wallet code."""
    return venue_key(name)


def capital_required_by_venue_from_fills(fills: Iterable[Fill]) -> dict[str, float]:
    out: dict[str, float] = {}
    for fill in _unique_fills(fills):
        key = fill.venue_id or venue_key(fill.exchange)
        out[key] = out.get(key, 0.0) + order_capital_required(fill.side, fill.odds, fill.stake)
    return {k: round(v, 8) for k, v in out.items()}


def venue_outcome_pnls_from_fills(outcomes: Iterable[str], fills: Iterable[Fill]) -> dict[str, dict[str, float]]:
    """Outcome P&L split by canonical venue identity.

    Unlike the legacy exchange helper, this honours an explicit ``venue_id`` on
    each fill.  That lets broker/bookmaker fills retain their own venue identity
    without being collapsed through the legacy ``exchange`` display field.
    """
    unique = _unique_fills(fills)
    result: dict[str, dict[str, float]] = {}
    for outcome in tuple(str(x) for x in outcomes):
        gross: dict[str, float] = {}
        commission: dict[str, float] = {}
        for fill in unique:
            venue = fill.venue_id or venue_key(fill.exchange)
            stake = max(0.0, float(fill.stake or 0.0))
            odds = max(1.0, float(fill.odds or 1.0))
            wins = str(fill.selection).strip().lower() == outcome.strip().lower()
            if fill.side == OrderSide.LAY:
                pnl = -stake * (odds - 1.0) if wins else stake
            else:
                pnl = stake * (odds - 1.0) if wins else -stake
            gross[venue] = gross.get(venue, 0.0) + pnl
            commission[venue] = max(
                commission.get(venue, 0.0),
                max(0.0, float(fill.commission_pct or 0.0)) / 100.0,
            )
        result[outcome] = {
            venue: round(value - max(0.0, value) * commission.get(venue, 0.0), 8)
            for venue, value in gross.items()
        }
    return result


def capital_by_exchange(simulation: Mapping) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in simulation.get("stakes") or []:
        key = exchange_key(str(row.get("exchange") or ""))
        out[key] = out.get(key, 0.0) + max(0.0, float(row.get("stake") or 0.0))
    return {k: round(v, 8) for k, v in out.items()}


def scale_simulation(simulation: Mapping, factor: float, *, total_bankroll: float | None = None) -> dict:
    """Scale a linear BACK-only arb simulation to venue wallet capacity."""
    factor = min(1.0, max(0.0, float(factor)))
    out = dict(simulation)
    for key in ("deployed", "expected_profit", "gross_profit", "commission_cost"):
        if key in out and out.get(key) is not None:
            out[key] = round(float(out.get(key) or 0.0) * factor, 8)
    out["stakes"] = [{**row, "stake": round(float(row.get("stake") or 0.0) * factor, 8)} for row in (simulation.get("stakes") or [])]
    for key in ("outcome_pnls", "gross_outcome_pnls", "commission_by_outcome"):
        if key in simulation:
            out[key] = {str(k): round(float(v or 0.0) * factor, 8) for k, v in (simulation.get(key) or {}).items()}
    for key in ("commission_by_outcome_exchange", "gross_by_outcome_exchange", "net_by_outcome_exchange"):
        if key in simulation:
            out[key] = {
                str(outcome): {str(exchange): round(float(value or 0.0) * factor, 8) for exchange, value in (values or {}).items()}
                for outcome, values in (simulation.get(key) or {}).items()
            }
    if "net_pnl_spread" in simulation:
        out["net_pnl_spread"] = round(float(simulation.get("net_pnl_spread") or 0.0) * factor, 8)
    if total_bankroll and total_bankroll > 0:
        out["bankroll_roi_pct"] = round((float(out.get("expected_profit") or 0.0) / total_bankroll) * 100.0, 6)
        out["capital_used_pct"] = round((float(out.get("deployed") or 0.0) / total_bankroll) * 100.0, 6)
    out["wallet_scale_factor"] = round(factor, 8)
    if factor < 0.999999:
        out["limited_by"] = "exchange_balance"
    if factor <= 0:
        out["executable"] = False
        out["reason"] = "Insufficient exchange balance"
    return out


def fit_simulation_to_wallets(simulation: Mapping, free_by_exchange: Mapping[str, float], *, total_bankroll: float | None = None) -> tuple[dict, str | None]:
    needs = capital_by_exchange(simulation)
    factor = 1.0
    limiting: str | None = None
    for exchange, need in needs.items():
        if need <= 0:
            continue
        available = max(0.0, float(free_by_exchange.get(exchange, 0.0)))
        candidate = available / need
        if candidate < factor:
            factor = candidate
            limiting = exchange
    scaled = scale_simulation(simulation, factor, total_bankroll=total_bankroll)
    scaled["capital_required_by_exchange"] = capital_by_exchange(scaled)
    scaled["limiting_exchange"] = limiting if factor < 0.999999 else None
    return scaled, scaled.get("limiting_exchange")


def exchange_outcome_pnls(legs: list[Leg], simulation: Mapping) -> dict[str, dict[str, float]]:
    """Net P&L by venue for each mutually-exclusive outcome."""
    stake_rows = list(simulation.get("stakes") or [])
    if len(stake_rows) != len(legs):
        return {}
    stakes = [max(0.0, float(row.get("stake") or 0.0)) for row in stake_rows]
    exchanges = sorted({exchange_key(l.exchange) for l in legs})
    commissions: dict[str, float] = {}
    for leg in legs:
        key = exchange_key(leg.exchange)
        commissions[key] = max(commissions.get(key, 0.0), max(0.0, float(leg.commission_pct)) / 100.0)
    out: dict[str, dict[str, float]] = {}
    for winner_idx, winner in enumerate(legs):
        venue_pnls = {key: 0.0 for key in exchanges}
        for idx, leg in enumerate(legs):
            key = exchange_key(leg.exchange)
            if idx == winner_idx:
                venue_pnls[key] += stakes[idx] * (float(leg.odds) - 1.0)
            else:
                venue_pnls[key] -= stakes[idx]
        for key, value in list(venue_pnls.items()):
            venue_pnls[key] = round(value - max(0.0, value) * commissions.get(key, 0.0), 8)
        out[str(winner.selection)] = venue_pnls
    return out
