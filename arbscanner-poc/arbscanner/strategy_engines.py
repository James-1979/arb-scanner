from __future__ import annotations

"""0.9.14 pluggable strategy-engine framework.

The module is intentionally provider-blind. Engines receive immutable canonical
market evidence and return DecisionIntent proposals. Provider adapters, credentials,
wallet mutation and order-write APIs are not imported here and remain outside the
engine boundary.
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from itertools import product
from hashlib import sha256
import json
import time
from typing import Any, Mapping, Iterable

from .engine import best_strategy_legs, diagnose_equal_return
from .models import Leg

ENGINE_LIFECYCLES = ("DISABLED", "EXPERIMENTAL", "SIM", "LIVE_APPROVED")
ENGINE_GRADES = ("RESEARCH", "STANDARD", "ADVANCED", "EXTREME")
INTENT_TYPES = ("ARBITRAGE", "OPEN_POSITION", "CLOSE_POSITION", "REDUCE_POSITION", "HEDGE", "TRADE", "MARKET_MAKE")
ENGINE_TYPES = ("SPORTS_BASELINE_ARB", "SPORTS_SUPERBET_ARB", "GREYHOUNDS_BASELINE_ARB", "SPORTS_DEPTH_ARB_REFERENCE", "NOOP_TEST_ENGINE")
ENGINE_TYPE_ALIASES = {
    "LEGACY_SIMPLE_ARB": "SPORTS_BASELINE_ARB",
    "BASELINE_ARB": "SPORTS_BASELINE_ARB",
    "SUPERBET_ARB": "SPORTS_SUPERBET_ARB",
    "GREYHOUND_BASELINE": "GREYHOUNDS_BASELINE_ARB",
    "GREYHOUNDS_BASELINE": "GREYHOUNDS_BASELINE_ARB",
    "DEPTH_ARB_REFERENCE": "SPORTS_DEPTH_ARB_REFERENCE",
}

COMMON_ENGINE_CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "minimum_liquidity": {"type": "number", "minimum": 0.0},
    "minimum_edge": {"type": "number", "minimum": 0.0},
    "minimum_profit": {"type": "number", "minimum": 0.0},
    "require_cross_exchange": {"type": "boolean"},
    "reference_bankroll": {"type": "number", "exclusive_minimum": 0.0},
    "maximum_slippage": {"type": "number", "minimum": 0.0, "maximum": 100.0},
    "decision_ttl_ms": {"type": "integer", "minimum": 1},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Mapping[str, Any] | dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EngineQuote:
    provider: str
    economic_venue: str
    exchange_label: str
    event_id: str | None
    market_id: str | None
    selection_id: str | None
    selection: str
    side: str
    odds: float
    liquidity: float
    commission_pct: float
    captured_at: str | None
    quote_age_ms: int | None
    feed_entitlement: str
    market_data_transport: str
    depth_levels: tuple[tuple[str, int, float, float], ...] = field(default_factory=tuple)

    @classmethod
    def from_leg(cls, leg: Leg) -> "EngineQuote":
        levels = []
        for level in getattr(leg, "depth_levels", ()) or ():
            try:
                levels.append((
                    str(getattr(level, "side", "BACK") or "BACK").upper(),
                    int(getattr(level, "level", 0) or 0),
                    float(getattr(level, "odds", 0.0) or 0.0),
                    float(getattr(level, "available_size", 0.0) or 0.0),
                ))
            except Exception:
                continue
        return cls(
            provider=str(getattr(leg, "resolved_provider_id", None) or getattr(leg, "provider_id", None) or leg.exchange),
            economic_venue=str(getattr(leg, "resolved_venue_id", None) or getattr(leg, "venue_id", None) or leg.exchange),
            exchange_label=str(leg.exchange), event_id=leg.event_id, market_id=leg.market_id,
            selection_id=leg.selection_id, selection=str(leg.selection), side=str(leg.side or "BACK").upper(),
            odds=float(leg.odds), liquidity=float(leg.liquidity or 0.0),
            commission_pct=float(leg.commission_pct or 0.0), captured_at=leg.captured_at,
            quote_age_ms=leg.quote_age_ms, feed_entitlement=str(leg.feed_entitlement or "unknown"),
            market_data_transport=str(leg.market_data_transport or "unknown"), depth_levels=tuple(levels),
        )

    def to_leg(self, *, sport: str, market_type: str, strategy: str, section: str, in_play: bool | None, market_status: str | None) -> Leg:
        from .models import DepthLevel
        return Leg(
            exchange=self.exchange_label, selection=self.selection, odds=self.odds, liquidity=self.liquidity,
            commission_pct=self.commission_pct, event_id=self.event_id, market_id=self.market_id,
            selection_id=self.selection_id, captured_at=self.captured_at, market_type=market_type,
            strategy=strategy, sport=sport, in_play=in_play, market_status=market_status, section=section,
            venue_id=self.economic_venue, provider_id=self.provider, side=self.side,
            executable_capacity=self.liquidity, feed_entitlement=self.feed_entitlement,
            market_data_transport=self.market_data_transport, quote_age_ms=self.quote_age_ms,
            depth_levels=tuple(DepthLevel(side=s, level=l, odds=o, available_size=a) for s, l, o, a in self.depth_levels),
        )


@dataclass(frozen=True)
class MarketEvidence:
    market_snapshot_id: str
    feed_generation: str
    section: str
    sport: str
    competition: str | None
    event_id: str
    event_name: str
    event_start_time: str | None
    market_id: str
    market_name: str
    market_type: str
    strategy: str
    market_status: str | None
    in_play: bool | None
    observed_at: str
    selections: tuple[tuple[str, tuple[EngineQuote, ...]], ...]

    @classmethod
    def from_candidates(cls, market: Any, candidates: Mapping[str, Iterable[Leg]], *, feed_generation: str = "unknown", observed_at: str | None = None) -> "MarketEvidence":
        frozen = tuple(
            (str(selection), tuple(EngineQuote.from_leg(leg) for leg in legs))
            for selection, legs in candidates.items()
        )
        event_id = str(getattr(market, "canonical_event_id", None) or getattr(market, "event_key", "") or "")
        market_id = str(getattr(market, "canonical_market_id", None) or getattr(market, "display_market", "") or "")
        observed = observed_at or utc_now()
        fingerprint = {
            "event": event_id, "market": market_id, "observed": observed,
            "feed_generation": str(feed_generation),
            "quotes": [[name, [asdict(q) for q in quotes]] for name, quotes in frozen],
        }
        return cls(
            market_snapshot_id=stable_hash(fingerprint)[:32], feed_generation=str(feed_generation or "unknown"),
            section=str(getattr(market, "section", "sports") or "sports"), sport=str(getattr(market, "sport", "Unknown") or "Unknown"),
            competition=str(getattr(market, "competition", "") or "") or None, event_id=event_id,
            event_name=str(getattr(market, "display_event", "") or ""), event_start_time=getattr(market, "start_time", None),
            market_id=market_id, market_name=str(getattr(market, "display_market", "") or ""),
            market_type=str(getattr(market, "canonical_market_type", None) or getattr(market, "display_market", "") or "Unknown"),
            strategy=str(getattr(market, "strategy", "two-way") or "two-way"), market_status=getattr(market, "status", None),
            in_play=getattr(market, "in_play", None), observed_at=observed, selections=frozen,
        )

    def leg_candidates(self) -> dict[str, list[Leg]]:
        return {
            selection: [quote.to_leg(sport=self.sport, market_type=self.market_type, strategy=self.strategy,
                                     section=self.section, in_play=self.in_play, market_status=self.market_status) for quote in quotes]
            for selection, quotes in self.selections
        }


@dataclass(frozen=True)
class EngineEvaluationContext:
    market_snapshot_id: str
    feed_generation: str
    engine_instance_id: str
    engine_type: str
    engine_version: str
    engine_grade: str
    config_version: int
    config_hash: str
    evaluation_timestamp: str
    mode: str
    requested_lifecycle: str
    effective_lifecycle: str


@dataclass(frozen=True)
class DecisionLeg:
    provider: str
    economic_venue: str
    market_id: str | None
    selection_id: str | None
    selection: str
    side: str
    requested_odds: float
    minimum_acceptable_odds: float
    requested_stake: float


@dataclass(frozen=True)
class DecisionIntent:
    decision_id: str
    economic_intent_key: str
    intent_type: str
    engine_instance_id: str
    engine_type: str
    engine_version: str
    engine_grade: str
    capabilities: tuple[str, ...]
    config_version: int
    config_hash: str
    market_snapshot_id: str
    feed_generation: str
    created_at: str
    expires_at: str
    section: str
    sport: str
    event: str
    market: str
    legs: tuple[DecisionLeg, ...]
    expected_edge: float | None
    expected_profit: float | None
    requested_capital: float
    requested_stake: float
    minimum_profit: float | None
    maximum_slippage: float
    expected_commission: float
    expected_fees: float
    mode: str
    reason_codes: tuple[str, ...]
    strategy_metrics: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EngineEvaluation:
    context: EngineEvaluationContext
    selected_legs: tuple[Leg, ...]
    diagnostic_legs: tuple[Leg, ...]
    decision: DecisionIntent | None
    duration_ms: float
    error: str | None = None


class StrategyEngine:
    engine_type = "BASE"
    display_name = "Strategy Engine"
    engine_version = "1.0.0"
    engine_grade = "RESEARCH"
    capabilities: tuple[str, ...] = ()
    config_schema: Mapping[str, Mapping[str, Any]] = {}
    package_origin: str | None = None
    package_sha256: str | None = None
    reference_only: bool = False

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = dict(config or {})
        self._evidence: MarketEvidence | None = None

    def initialize(self, context: EngineEvaluationContext) -> None:
        return None

    def on_market_evidence(self, snapshot: MarketEvidence) -> None:
        self._evidence = snapshot

    def on_market_event(self, event: Mapping[str, Any]) -> None:
        return None

    def evaluate(self, context: EngineEvaluationContext) -> EngineEvaluation:
        raise NotImplementedError

    def snapshot_state(self) -> dict[str, Any]:
        return {}

    def restore_state(self, state: Mapping[str, Any]) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def _intent(self, context: EngineEvaluationContext, evidence: MarketEvidence, legs: list[Leg], diagnosis: Mapping[str, Any]) -> DecisionIntent | None:
        if not legs or not diagnosis.get("valid"):
            return None
        expected_profit = float(diagnosis.get("expected_profit") or 0.0)
        expected_roi = float(diagnosis.get("expected_roi_pct") or 0.0)
        minimum_profit = float(self.config.get("minimum_profit", 0.0) or 0.0)
        minimum_edge = float(self.config.get("minimum_edge", 0.0) or 0.0)
        if expected_profit < minimum_profit or expected_roi < minimum_edge:
            return None
        deployed = float(diagnosis.get("deployed") or 0.0)
        stakes = list(diagnosis.get("stakes") or [])
        dlegs = []
        for idx, leg in enumerate(legs):
            stake_item = stakes[idx] if idx < len(stakes) else 0.0
            if isinstance(stake_item, dict):
                stake_item = stake_item.get("stake", 0.0)
            stake = float(stake_item or 0.0)
            dlegs.append(DecisionLeg(
                provider=str(getattr(leg, "resolved_provider_id", None) or leg.provider_id or leg.exchange),
                economic_venue=str(getattr(leg, "resolved_venue_id", None) or leg.venue_id or leg.exchange),
                market_id=leg.market_id, selection_id=leg.selection_id, selection=str(leg.selection),
                side=str(leg.side or "BACK").upper(), requested_odds=float(leg.odds),
                minimum_acceptable_odds=float(leg.odds) * (1.0 - float(self.config.get("maximum_slippage", 0.0) or 0.0) / 100.0),
                requested_stake=stake,
            ))
        economic_key_payload = sorted((x.economic_venue, x.market_id or "", x.selection_id or x.selection, x.side) for x in dlegs)
        economic_key = stable_hash({"event": evidence.event_id, "market": evidence.market_id, "legs": economic_key_payload})[:32]
        created = context.evaluation_timestamp
        ttl_ms = max(1, int(self.config.get("decision_ttl_ms", 1500) or 1500))
        try:
            expires = (datetime.fromisoformat(created.replace("Z", "+00:00")) + timedelta(milliseconds=ttl_ms)).isoformat()
        except Exception:
            expires = created
        identity = {
            "engine": context.engine_instance_id, "snapshot": evidence.market_snapshot_id,
            "config": context.config_hash, "created": created, "economic": economic_key,
        }
        return DecisionIntent(
            decision_id=stable_hash(identity)[:32], economic_intent_key=economic_key, intent_type="ARBITRAGE",
            engine_instance_id=context.engine_instance_id, engine_type=context.engine_type,
            engine_version=context.engine_version, engine_grade=context.engine_grade, capabilities=tuple(self.capabilities),
            config_version=context.config_version, config_hash=context.config_hash,
            market_snapshot_id=evidence.market_snapshot_id, feed_generation=evidence.feed_generation,
            created_at=created, expires_at=expires, section=evidence.section, sport=evidence.sport,
            event=evidence.event_name, market=evidence.market_name, legs=tuple(dlegs), expected_edge=expected_roi,
            expected_profit=expected_profit, requested_capital=deployed, requested_stake=sum(x.requested_stake for x in dlegs),
            minimum_profit=minimum_profit, maximum_slippage=float(self.config.get("maximum_slippage", 0.0) or 0.0),
            expected_commission=float(diagnosis.get("commission_cost") or 0.0), expected_fees=0.0,
            mode=context.mode, reason_codes=("ENGINE_PROPOSAL", context.effective_lifecycle),
            strategy_metrics={
                "guaranteed_profit": expected_profit,
                "arb_edge_pct": expected_roi,
                "capital_committed": deployed,
                "commission_cost": float(diagnosis.get("commission_cost") or 0.0),
            },
        )


class BaselineArbEngine(StrategyEngine):
    engine_type = "SPORTS_BASELINE_ARB"
    display_name = "Sports Baseline ARB"
    engine_version = "1.0.0"
    engine_grade = "STANDARD"
    capabilities = ("ARBITRAGE", "MULTI_LEG", "MULTI_VENUE", "USES_PREMATCH", "USES_IN_PLAY")
    config_schema = COMMON_ENGINE_CONFIG_SCHEMA

    def evaluate(self, context: EngineEvaluationContext) -> EngineEvaluation:
        started = time.perf_counter()
        evidence = self._evidence
        if evidence is None:
            return EngineEvaluation(context, (), (), None, 0.0, "NO_EVIDENCE")
        candidates = evidence.leg_candidates()
        require_cross = bool(self.config.get("require_cross_exchange", True))
        min_liquidity = max(0.0, float(self.config.get("minimum_liquidity", 0.0) or 0.0))
        wallets = self.config.get("_venue_wallets") if isinstance(self.config.get("_venue_wallets"), dict) else None
        diagnostic = best_strategy_legs(candidates, 0.0, require_cross_exchange=require_cross, venue_wallets=wallets) if candidates else []
        selected = best_strategy_legs(candidates, min_liquidity, require_cross_exchange=require_cross, venue_wallets=wallets) if candidates else []
        reference = max(1.0, float(self.config.get("reference_bankroll", 1000.0) or 1000.0))
        diag = diagnose_equal_return(selected, reference) if selected else {"valid": False}
        intent = self._intent(context, evidence, selected, diag)
        return EngineEvaluation(context, tuple(selected), tuple(diagnostic), intent, (time.perf_counter() - started) * 1000.0)


SUPERBET_CONFIG_SCHEMA = {
    **COMMON_ENGINE_CONFIG_SCHEMA,
    "max_tranches": {"type": "integer_or_unlimited", "minimum": 2},
    "tranche_size_mode": {"type": "string", "enum": ("base", "fixed")},
    "tranche_size": {"type": "number", "minimum": 0.0},
    "max_total_stake": {"type": "number", "minimum": 0.0},
    "min_net_edge": {"type": "number", "minimum": 0.0},
    "min_depth_multiplier": {"type": "number", "minimum": 1.0},
    "recheck_delay_ms": {"type": "integer", "minimum": 0},
}


class SuperBetArbEngine(BaselineArbEngine):
    engine_type = "SPORTS_SUPERBET_ARB"
    display_name = "Sports SuperBet ARB"
    engine_version = "1.0.0"
    engine_grade = "ADVANCED"
    capabilities = ("ARBITRAGE", "MULTI_LEG", "MULTI_VENUE", "USES_DEPTH", "SCALED_ENTRY", "USES_PREMATCH", "USES_IN_PLAY")
    config_schema = SUPERBET_CONFIG_SCHEMA

    def _intent(self, context, evidence, legs, diagnosis):
        intent = super()._intent(context, evidence, legs, diagnosis)
        if intent is None:
            return None
        metrics = dict(intent.strategy_metrics)
        metrics.update({
            "max_tranches": ("unlimited" if str(self.config.get("max_tranches", 3)).strip().lower() == "unlimited" else max(2, int(self.config.get("max_tranches", 3) or 3))),
            "max_total_stake": float(self.config.get("max_total_stake", 100.0) or 0.0),
            "min_depth_multiplier": float(self.config.get("min_depth_multiplier", 1.25) or 1.0),
            "strategy_family": "SUPERBET",
        })
        return DecisionIntent(**{**intent.as_dict(), "capabilities": tuple(intent.capabilities), "legs": tuple(intent.legs), "reason_codes": tuple(intent.reason_codes), "strategy_metrics": metrics})


class GreyhoundsBaselineArbEngine(BaselineArbEngine):
    engine_type = "GREYHOUNDS_BASELINE_ARB"
    display_name = "Greyhounds Baseline ARB"
    engine_version = "1.0.0"
    engine_grade = "STANDARD"
    capabilities = ("ARBITRAGE", "MULTI_LEG", "MULTI_VENUE", "RACING_SPECIALIST", "USES_PREMATCH")


# Compatibility import names only. Registry identities are canonical 0.9.15 names.
LegacySimpleArbEngine = BaselineArbEngine
GreyhoundBaselineEngine = GreyhoundsBaselineArbEngine


class DepthArbReferenceEngine(StrategyEngine):
    engine_type = "SPORTS_DEPTH_ARB_REFERENCE"
    display_name = "Sports Depth ARB Reference"
    engine_version = "1.0.0"
    engine_grade = "RESEARCH"
    capabilities = ("ARBITRAGE", "MULTI_LEG", "MULTI_VENUE", "USES_DEPTH", "USES_PREMATCH", "USES_IN_PLAY")
    config_schema = COMMON_ENGINE_CONFIG_SCHEMA
    reference_only = True

    @staticmethod
    def _depth_capacity(leg: Leg) -> float:
        levels = [x for x in (getattr(leg, "depth_levels", ()) or ()) if str(getattr(x, "side", "BACK")).upper() == str(leg.side or "BACK").upper() and int(getattr(x, "level", 0) or 0) <= 3]
        if levels:
            return sum(max(0.0, float(getattr(x, "available_size", 0.0) or 0.0)) for x in levels)
        return max(0.0, float(leg.liquidity or 0.0))

    def _select(self, candidates: Mapping[str, Iterable[Leg]], minimum_liquidity: float, require_cross: bool) -> list[Leg]:
        selections = list(candidates)
        if len(selections) < 2:
            return []
        choices = []
        for selection in selections:
            valid = [x for x in candidates[selection] if float(x.odds) > 1.0 and self._depth_capacity(x) >= minimum_liquidity]
            if not valid:
                return []
            choices.append(valid)
        best = None
        reference = max(1.0, float(self.config.get("reference_bankroll", 1000.0) or 1000.0))
        for combo in product(*choices):
            legs = list(combo)
            if require_cross and len({x.resolved_venue_id for x in legs}) < 2:
                continue
            diag = diagnose_equal_return(legs, reference)
            if not diag.get("valid"):
                continue
            score = (float(diag.get("expected_roi_pct") or -1e9), min(self._depth_capacity(x) for x in legs), float(diag.get("expected_profit") or -1e9))
            if best is None or score > best[0]:
                best = (score, legs, diag)
        return best[1] if best else []

    def evaluate(self, context: EngineEvaluationContext) -> EngineEvaluation:
        started = time.perf_counter()
        evidence = self._evidence
        if evidence is None:
            return EngineEvaluation(context, (), (), None, 0.0, "NO_EVIDENCE")
        candidates = evidence.leg_candidates()
        require_cross = bool(self.config.get("require_cross_exchange", True))
        min_liquidity = max(0.0, float(self.config.get("minimum_liquidity", 0.0) or 0.0))
        diagnostic = self._select(candidates, 0.0, require_cross)
        selected = self._select(candidates, min_liquidity, require_cross)
        reference = max(1.0, float(self.config.get("reference_bankroll", 1000.0) or 1000.0))
        diag = diagnose_equal_return(selected, reference) if selected else {"valid": False}
        intent = self._intent(context, evidence, selected, diag)
        return EngineEvaluation(context, tuple(selected), tuple(diagnostic), intent, (time.perf_counter() - started) * 1000.0)


class NoopTestEngine(StrategyEngine):
    engine_type = "NOOP_TEST_ENGINE"
    display_name = "Framework NOOP Test Engine"
    engine_version = "1.0.0"
    engine_grade = "RESEARCH"
    capabilities = ("TEST_ONLY",)
    reference_only = True

    def evaluate(self, context: EngineEvaluationContext) -> EngineEvaluation:
        started = time.perf_counter()
        return EngineEvaluation(context, (), (), None, (time.perf_counter() - started) * 1000.0)


class EngineRegistry:
    def __init__(self):
        self._types: dict[str, type[StrategyEngine]] = {}
        self.register_type(BaselineArbEngine)
        self.register_type(SuperBetArbEngine)
        self.register_type(GreyhoundsBaselineArbEngine)
        self.register_type(DepthArbReferenceEngine)
        self.register_type(NoopTestEngine)
        self.reload_packages()

    def reload_packages(self) -> None:
        """Register reviewed-local .arbengine implementations from persistent storage."""
        try:
            from .engine_packages import load_dynamic_engine_classes
            for engine_cls in load_dynamic_engine_classes():
                identity = str(engine_cls.engine_type).upper()
                if identity in self._types:
                    # Built-in/core types always win over external packages.
                    continue
                self.register_type(engine_cls)
        except Exception:
            # Package failures stay isolated from the core engine catalogue.
            return

    @staticmethod
    def canonical_type(engine_type: str) -> str:
        identity = str(engine_type or "").upper()
        return ENGINE_TYPE_ALIASES.get(identity, identity)

    def register_type(self, engine_cls: type[StrategyEngine]) -> None:
        identity = str(engine_cls.engine_type).upper()
        if identity in self._types:
            raise ValueError(f"Duplicate engine type: {identity}")
        self._types[identity] = engine_cls

    def types(self) -> list[dict[str, Any]]:
        return [{
            "engine_type": key, "display_name": cls.display_name, "engine_version": cls.engine_version, "engine_grade": cls.engine_grade,
            "capabilities": list(cls.capabilities), "config_schema": dict(cls.config_schema),
            "reference_only": bool(getattr(cls, "reference_only", False)),
            "package_origin": getattr(cls, "package_origin", None),
            "package_sha256": getattr(cls, "package_sha256", None),
            "engine_class": cls.__name__,
        } for key, cls in sorted(self._types.items())]

    def validate_config(self, engine_type: str, config: Mapping[str, Any]) -> dict[str, Any]:
        cls = self._types.get(self.canonical_type(engine_type))
        if cls is None:
            raise KeyError(f"Unknown engine type: {engine_type}")
        clean = dict(config or {})
        schema = dict(cls.config_schema)
        unknown = sorted(set(clean) - set(schema))
        if unknown:
            raise ValueError(f"Unknown {engine_type} configuration keys: {', '.join(unknown)}")
        for key, value in clean.items():
            rule = schema.get(key) or {}
            kind = rule.get("type")
            if kind == "boolean":
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be boolean")
                continue
            if kind == "string":
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
                allowed = tuple(rule.get("enum") or ())
                if allowed and value not in allowed:
                    raise ValueError(f"{key} must be one of: {', '.join(map(str, allowed))}")
                continue
            if kind == "integer_or_unlimited":
                if isinstance(value, str) and value.strip().lower() == "unlimited":
                    continue
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{key} must be an integer or 'unlimited'")
                numeric = float(value)
            elif kind == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{key} must be an integer")
                numeric = float(value)
            elif kind == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be numeric")
                numeric = float(value)
            else:
                continue
            if "minimum" in rule and numeric < float(rule["minimum"]):
                raise ValueError(f"{key} must be >= {rule['minimum']}")
            if "exclusive_minimum" in rule and numeric <= float(rule["exclusive_minimum"]):
                raise ValueError(f"{key} must be > {rule['exclusive_minimum']}")
            if "maximum" in rule and numeric > float(rule["maximum"]):
                raise ValueError(f"{key} must be <= {rule['maximum']}")
        return clean

    def create(self, engine_type: str, config: Mapping[str, Any]) -> StrategyEngine:
        cls = self._types.get(self.canonical_type(engine_type))
        if cls is None:
            raise KeyError(f"Unknown engine type: {engine_type}")
        return cls(config)

    def version(self, engine_type: str) -> str:
        cls = self._types.get(self.canonical_type(engine_type))
        if cls is None:
            raise KeyError(engine_type)
        return str(cls.engine_version)


class EngineRouter:
    @staticmethod
    def compatible(instance: Mapping[str, Any], evidence: MarketEvidence, *, include_disabled: bool = False) -> bool:
        if not include_disabled and str(instance.get("effective_lifecycle") or "DISABLED") == "DISABLED":
            # 0.9.38 recovery: an older 0.9.36/0.9.37 process may have persisted
            # a market-local venue-eligibility miss as the engine's global
            # lifecycle state.  Allow that specific stale state back through
            # routing so the next evidence record can recover automatically.
            if str(instance.get("effective_reason") or "") != "INSUFFICIENT_COMPATIBLE_VENUE_FEEDS":
                return False
        section = str(instance.get("section") or "all").lower()
        if section not in {"all", str(evidence.section).lower()}:
            return False
        sport = str(instance.get("sport") or "all").strip().lower()
        if sport not in {"", "all", str(evidence.sport).lower()}:
            return False
        competition = str(instance.get("competition") or "all").strip().lower()
        if competition not in {"", "all", str(evidence.competition or "").lower()}:
            return False
        market_type = str(instance.get("market_type") or "all").strip().lower()
        if market_type not in {"", "all", str(evidence.market_type).lower(), str(evidence.market_name).lower()}:
            return False
        return True

    def route(self, instances: Iterable[Mapping[str, Any]], evidence: MarketEvidence, *, include_disabled: bool = False) -> list[Mapping[str, Any]]:
        return [x for x in instances if self.compatible(x, evidence, include_disabled=include_disabled)]


def effective_lifecycle(requested: str, *, live_execution_unlocked: bool = False, provider_available: bool = True) -> tuple[str, str]:
    """Resolve legacy lifecycle metadata without creating a third operational mode.

    ArbScanner has exactly two operational economic modes: SIM and LIVE. LIVE_APPROVED is
    still an authorisation/lifecycle marker, but when the central LIVE lock is
    closed it cannot silently become another economic mode; it remains
    LIVE_APPROVED and central validation blocks order writes.
    """
    requested = str(requested or "DISABLED").upper()
    if requested not in ENGINE_LIFECYCLES:
        return "DISABLED", "INVALID_REQUESTED_LIFECYCLE"
    if requested == "DISABLED":
        return "DISABLED", "REQUESTED_DISABLED"
    if not provider_available:
        return "DISABLED", "REQUIRED_PROVIDER_UNAVAILABLE"
    if requested == "LIVE_APPROVED" and not live_execution_unlocked:
        return "LIVE_APPROVED", "LIVE_EXECUTION_LOCKED"
    return requested, "REQUESTED_EFFECTIVE"


class EngineRuntime:
    """Coordinates immutable evidence -> isolated engines -> persisted intents."""

    def __init__(self, db, *, mode_provider=lambda: "sim"):
        self.db = db
        self.registry = EngineRegistry()
        self.router = EngineRouter()
        self.mode_provider = mode_provider

    def evaluate(self, evidence: MarketEvidence, *, config_overrides: Mapping[str, Any] | None = None,
                 instance_ids: Iterable[str] | None = None, evaluation_timestamp: str | None = None,
                 mode_override: str | None = None, persist: bool = True, research_mode: bool = False) -> list[EngineEvaluation]:
        instances = self.db.engine_instances()
        selected_ids = {str(x) for x in (instance_ids or [])}
        routed = self.router.route(instances, evidence, include_disabled=research_mode)
        if selected_ids:
            routed = [x for x in routed if str(x.get("engine_instance_id")) in selected_ids]
        platform_cfg = self.db.get_setting("config", {}) or {}
        try:
            max_runtimes = max(1, min(1000, int(platform_cfg.get("engine_max_concurrent_runtimes", 100))))
        except (TypeError, ValueError):
            max_runtimes = 100
        routed = routed[:max_runtimes]
        results: list[EngineEvaluation] = []
        for row in routed:
            config_row = self.db.engine_active_config(str(row["engine_instance_id"]))
            if not config_row:
                continue
            config = dict(config_row.get("config") or {})
            if config_overrides:
                # 0.9.37: platform/Sports Config values are an outer envelope.
                # Engines may request stricter minima/lower maxima, never loosen
                # portfolio-wide guardrails. Non-guardrail values remain overrides.
                for key, value in dict(config_overrides).items():
                    if key in {"minimum_liquidity", "minimum_edge", "minimum_profit"}:
                        config[key] = max(float(config.get(key, 0.0) or 0.0), float(value or 0.0))
                    elif key in {"maximum_slippage"}:
                        current = config.get(key)
                        config[key] = float(value or 0.0) if current is None else min(float(current or 0.0), float(value or 0.0))
                    else:
                        config[key] = value
            requested = str(row.get("requested_lifecycle") or "DISABLED").upper()
            run_mode = str(mode_override or self.mode_provider() or "sim").lower()
            if run_mode not in {"sim", "live"}:
                run_mode = "sim"
            evidence_venues = {str(q.economic_venue or q.provider).strip().lower() for _, quotes in evidence.selections for q in quotes if str(q.economic_venue or q.provider).strip()}
            minimum_venues = 2 if bool(config.get("require_cross_exchange", False)) else 1
            # Resolve engine/global enablement independently from this specific
            # market's venue suitability.  A bad market is a local rejection,
            # never a persisted engine disablement.
            if research_mode:
                effective, reason = "SIM", "RESEARCH_RUN_OVERRIDE"
            elif run_mode == "sim":
                if not bool(row.get("sim_enabled", requested in {"SIM", "EXPERIMENTAL"})):
                    effective, reason = "DISABLED", "SIM_DISABLED"
                else:
                    effective, reason = "SIM", "SIM_ENABLED"
            else:
                if not bool(row.get("live_enabled", requested == "LIVE_APPROVED")):
                    effective, reason = "DISABLED", "LIVE_DISABLED"
                else:
                    effective, reason = effective_lifecycle("LIVE_APPROVED", live_execution_unlocked=False, provider_available=True)
            # Persist only genuine engine/global state.  This also heals the stale
            # INSUFFICIENT_COMPATIBLE_VENUE_FEEDS disablement written by 0.9.36.
            if not research_mode and (effective != str(row.get("effective_lifecycle") or "") or reason != str(row.get("effective_reason") or "")):
                self.db.engine_set_effective(str(row["engine_instance_id"]), effective, reason)
            context = EngineEvaluationContext(
                market_snapshot_id=evidence.market_snapshot_id, feed_generation=evidence.feed_generation,
                engine_instance_id=str(row["engine_instance_id"]), engine_type=self.registry.canonical_type(str(row["engine_type"])),
                engine_version=str(row["engine_version"]), engine_grade=str(row.get("engine_grade") or "RESEARCH"), config_version=int(config_row["config_version"]),
                config_hash=str(config_row["config_hash"]), evaluation_timestamp=str(evaluation_timestamp or utc_now()), mode=run_mode,
                requested_lifecycle=requested, effective_lifecycle=effective,
            )
            if effective == "DISABLED":
                continue
            if len(evidence_venues) < minimum_venues:
                # Local evidence outcome: count the evaluation, but do not mutate
                # engine health/enablement or invent an opportunity/decision.
                result = EngineEvaluation(context, (), (), None, 0.0, None)
                if persist:
                    self.db.engine_record_evaluation(result, evidence)
                results.append(result)
                continue
            engine = self.registry.create(str(row["engine_type"]), config)
            try:
                engine.initialize(context)
                engine.on_market_evidence(evidence)
                result = engine.evaluate(context)
                if persist:
                    self.db.engine_record_evaluation(result, evidence)
                results.append(result)
            except Exception as exc:
                if persist:
                    self.db.engine_record_error(
                        str(row["engine_instance_id"]), evidence.market_snapshot_id, type(exc).__name__, str(exc),
                        mode=context.mode, section=evidence.section,
                        stream=("in_play" if bool(evidence.in_play) else ("racing" if str(evidence.section).lower()=="racing" else "pre_match")),
                    )
                results.append(EngineEvaluation(context, (), (), None, 0.0, f"{type(exc).__name__}: {exc}"))
            finally:
                try:
                    engine.shutdown()
                except Exception:
                    pass
        return results

    def evaluate_primary(self, evidence: MarketEvidence, *, minimum_liquidity: float, require_cross_exchange: bool, reference_bankroll: float = 1000.0, minimum_edge: float | None = None, minimum_profit: float | None = None, maximum_slippage: float | None = None, venue_wallets: dict | None = None) -> EngineEvaluation | None:
        """Evaluate all routed engines but return the canonical baseline for scanner qualification.

        Sports qualification is SPORTS_BASELINE_ARB. Racing qualification is GREYHOUNDS_BASELINE_ARB.
        Other active engines still consume and persist the identical snapshot for comparison.
        """
        overrides = {
            "minimum_liquidity": minimum_liquidity, "require_cross_exchange": require_cross_exchange,
            "reference_bankroll": reference_bankroll,
        }
        if minimum_edge is not None:
            overrides["minimum_edge"] = minimum_edge
        if minimum_profit is not None:
            overrides["minimum_profit"] = minimum_profit
        if maximum_slippage is not None:
            overrides["maximum_slippage"] = maximum_slippage
        if venue_wallets is not None:
            overrides["_venue_wallets"] = dict(venue_wallets)
        results = self.evaluate(evidence, config_overrides=overrides)
        wanted = "GREYHOUNDS_BASELINE_ARB" if str(evidence.section).lower() == "racing" else "SPORTS_BASELINE_ARB"
        for result in results:
            if self.registry.canonical_type(result.context.engine_type) == wanted and result.context.effective_lifecycle in {"SIM", "LIVE_APPROVED"}:
                return result
        return None

    def evaluate_legacy(self, evidence: MarketEvidence, *, minimum_liquidity: float, require_cross_exchange: bool, reference_bankroll: float = 1000.0) -> EngineEvaluation | None:
        return self.evaluate_primary(evidence, minimum_liquidity=minimum_liquidity, require_cross_exchange=require_cross_exchange, reference_bankroll=reference_bankroll)

    def primary_config(self, *, section: str) -> dict[str, Any]:
        wanted = "GREYHOUNDS_BASELINE_ARB" if str(section).lower() == "racing" else "SPORTS_BASELINE_ARB"
        for row in self.db.engine_instances(section=section):
            if self.registry.canonical_type(str(row.get("engine_type"))) == wanted:
                cfg = self.db.engine_active_config(str(row["engine_instance_id"]))
                return dict((cfg or {}).get("config") or {})
        return {}

    def capability_execution_config(self, capability: str, *, section: str | None = None) -> dict[str, Any]:
        """Resolve one active capability-driven execution policy without engine-name branching.

        Strategy-specific engines own their parameters.  The scanner/execution
        layer asks only for a declared capability (for example SCALED_ENTRY).
        """
        wanted = str(capability or "").strip().upper()
        for row in self.db.engine_instances(section=section):
            engine_type = self.registry.canonical_type(str(row.get("engine_type")))
            meta = next((x for x in self.registry.types() if x["engine_type"] == engine_type), None) or {}
            if wanted not in {str(x).upper() for x in (meta.get("capabilities") or [])}:
                continue
            if not bool(row.get("sim_enabled", str(row.get("effective_lifecycle") or "DISABLED") != "DISABLED")):
                continue
            cfg = self.db.engine_active_config(str(row["engine_instance_id"])) or {}
            out = dict(cfg.get("config") or {})
            out.update({
                "enabled": True,
                "engine_instance_id": str(row["engine_instance_id"]),
                "engine_type": engine_type,
                "engine_grade": str(row.get("engine_grade") or "RESEARCH"),
                "capability": wanted,
            })
            return out
        return {"enabled": False, "capability": wanted}

    def scaled_entry_execution_config(self, *, section: str = "sports") -> dict[str, Any]:
        return self.capability_execution_config("SCALED_ENTRY", section=section)

    # Compatibility shim for pre-0.9.15 callers; core code uses capabilities.
    def superbet_execution_config(self) -> dict[str, Any]:
        return self.scaled_entry_execution_config(section="sports")



def validate_intent(intent: DecisionIntent, *, current_feed_generation: str, now: str | None = None, allow_live: bool = False,
                    seen_economic_intents: set[str] | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        now_dt = datetime.fromisoformat((now or utc_now()).replace("Z", "+00:00"))
        expiry = datetime.fromisoformat(intent.expires_at.replace("Z", "+00:00"))
        if expiry <= now_dt:
            reasons.append("DECISION_EXPIRED")
    except Exception:
        reasons.append("INVALID_EXPIRY")
    if str(intent.feed_generation) != str(current_feed_generation):
        reasons.append("FEED_GENERATION_MISMATCH")
    if str(intent.mode).lower() == "live" and not allow_live:
        reasons.append("LIVE_EXECUTION_LOCKED")
    if seen_economic_intents is not None:
        if intent.economic_intent_key in seen_economic_intents:
            reasons.append("DUPLICATE_ECONOMIC_INTENT")
        elif not reasons:
            seen_economic_intents.add(intent.economic_intent_key)
    if str(intent.intent_type or "").upper() not in INTENT_TYPES:
        reasons.append("INVALID_INTENT_TYPE")
    if not intent.legs:
        reasons.append("NO_LEGS")
    if str(intent.intent_type).upper() == "ARBITRAGE" and intent.minimum_profit is None:
        reasons.append("ARBITRAGE_MINIMUM_PROFIT_REQUIRED")
    return {"ok": not reasons, "reasons": reasons or ["CENTRAL_VALIDATION_PASSED"]}


def default_engine_instances() -> list[dict[str, Any]]:
    baseline_cfg = {"minimum_liquidity": 0.0, "minimum_edge": 0.0, "minimum_profit": 0.0, "require_cross_exchange": True,
                    "reference_bankroll": 1000.0, "maximum_slippage": 0.5, "decision_ttl_ms": 1500}
    superbet_cfg = {**baseline_cfg, "max_tranches": 3, "tranche_size_mode": "base", "tranche_size": 0.0,
                    "max_total_stake": 100.0, "min_net_edge": 1.0, "min_depth_multiplier": 1.25, "recheck_delay_ms": 100}
    return [
        {
            "engine_instance_id": "SPORTS_BASELINE_ARB_PRIMARY", "engine_type": "SPORTS_BASELINE_ARB", "engine_grade": "STANDARD", "section": "sports",
            "sport": "all", "competition": "all", "market_type": "all", "requested_lifecycle": "SIM",
            "nickname": "Baseline", "description": "General Sports arbitrage baseline using canonical matched exchange evidence and commission-aware equal-return staking.", "config": dict(baseline_cfg),
        },
        {
            "engine_instance_id": "GREYHOUNDS_BASELINE_ARB_PRIMARY", "engine_type": "GREYHOUNDS_BASELINE_ARB", "engine_grade": "STANDARD", "section": "racing",
            "sport": "Greyhounds", "competition": "all", "market_type": "all", "requested_lifecycle": "SIM",
            "nickname": "Greyhounds Base", "description": "Greyhounds specialist arbitrage baseline routed through canonical Racing evidence.", "config": dict(baseline_cfg),
        },
        {
            "engine_instance_id": "SPORTS_SUPERBET_ARB_PRIMARY", "engine_type": "SPORTS_SUPERBET_ARB", "engine_grade": "ADVANCED", "section": "sports",
            "sport": "all", "competition": "all", "market_type": "all", "requested_lifecycle": "DISABLED",
            "nickname": "SuperBet", "description": "Advanced Sports arbitrage strategy with capability-driven scaled entry and depth-aware execution controls.", "config": superbet_cfg,
        },
        {
            "engine_instance_id": "SPORTS_DEPTH_ARB_REFERENCE", "engine_type": "SPORTS_DEPTH_ARB_REFERENCE", "engine_grade": "RESEARCH", "section": "sports",
            "sport": "all", "competition": "all", "market_type": "all", "requested_lifecycle": "DISABLED",
            "nickname": "Depth Research", "description": "Research/reference Sports arbitrage engine using top-of-book depth to characterise alternative strategy behaviour.",
            "config": {**baseline_cfg, "minimum_liquidity": 2.0},
        },
        {
            "engine_instance_id": "NOOP_FRAMEWORK_TEST", "engine_type": "NOOP_TEST_ENGINE", "engine_grade": "RESEARCH", "section": "all",
            "sport": "all", "competition": "all", "market_type": "all", "requested_lifecycle": "DISABLED",
            "nickname": "Framework Test", "description": "Framework validation engine. Produces no economic intent and is hidden from normal engine management.", "config": {},
        },
    ]
