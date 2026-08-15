from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

VALID_STREAMS = frozenset({"pre_match", "in_play", "racing"})
ALL_STREAMS = frozenset({"pre_match", "in_play", "racing"})

SHARED_HEATMAP_METRICS = (
    "observations",
    "unique_markets",
    "net_positive",
    "available_depth",
    "top_book_depth",
    "avg_executable_stake",
    "liquidity_capable",
    "liquidity_rejected",
    "liquidity_rejection_rate_pct",
)
LIFECYCLE_HEATMAP_METRICS = (
    "qualified",
    "executed",
    "settled",
    "deployed",
    "settled_deployed",
    "pnl",
    "roi_pct",
)
HEATMAP_METRICS = (
    "observations",
    "unique_markets",
    "net_positive",
    "qualified",
    "executed",
    "settled",
    "deployed",
    "settled_deployed",
    "pnl",
    "roi_pct",
    "available_depth",
    "top_book_depth",
    "avg_executable_stake",
    "liquidity_capable",
    "liquidity_rejected",
    "liquidity_rejection_rate_pct",
)


@dataclass(frozen=True)
class MarketFilters:
    """Pure filter contract shared by Market Analysis and heatmap projections.

    The contract classifies provider observations only. It has no DB/provider
    dependency and therefore cannot select, repair, or merge economic authority.
    """

    scope: str = "all"
    phase: str = "all"
    sport: str = "all"
    search: str = ""
    streams: frozenset[str] = frozenset()
    requested_streams: frozenset[str] = frozenset()

    @classmethod
    def from_data(cls, data: Mapping | None) -> "MarketFilters":
        data = data or {}
        raw_streams = data.get("streams")
        if isinstance(raw_streams, str):
            raw_streams = raw_streams.split(",")
        requested_streams = frozenset(
            str(value).strip().lower()
            for value in (raw_streams or [])
            if str(value).strip().lower() in VALID_STREAMS
        )
        streams = requested_streams
        # The complete set is the legacy/default All state and therefore means
        # unrestricted rather than three redundant OR predicates.
        if streams == ALL_STREAMS:
            streams = frozenset()
        return cls(
            scope=str(data.get("scope") or "all").lower(),
            phase=str(data.get("phase") or "all").lower(),
            sport=str(data.get("sport") or "all").strip(),
            search=str(data.get("search") or "").strip().lower(),
            streams=streams,
            requested_streams=requested_streams,
        )

    @property
    def selected_streams_response(self) -> list[str]:
        return sorted(self.streams) if self.streams else ["pre_match", "in_play", "racing"]

    @property
    def live_domain(self) -> str:
        """LIVE Market Analysis domain after legacy All-stream normalization."""
        if self.streams == frozenset({"racing"}):
            return "racing"
        if self.streams and self.streams.issubset({"pre_match", "in_play"}):
            return "sports"
        return self.scope if self.scope in {"sports", "racing"} else "all"

    @property
    def live_heatmap_domain(self) -> str:
        """LIVE heatmap domain preserving its pre-Stage-07 explicit-stream rule."""
        streams = self.requested_streams
        if streams:
            if streams == frozenset({"racing"}):
                return "racing"
            if streams.issubset({"pre_match", "in_play"}):
                return "sports"
            return "all"
        return self.scope if self.scope in {"sports", "racing"} else "all"

    @property
    def live_decision_hourly_is_precise(self) -> bool:
        sports_streams = self.streams.intersection({"pre_match", "in_play"})
        return (
            self.phase == "all"
            and (
                not self.streams
                or not sports_streams
                or sports_streams == {"pre_match", "in_play"}
            )
        )


def market_stream(row: Mapping, *, phase_hint: bool = True) -> str:
    if str(row.get("section") or "").strip().lower() == "racing":
        return "racing"
    row_phase = str(row.get("phase") or "").strip().lower()
    if phase_hint:
        if row_phase in {"in_play", "inplay"}:
            return "in_play"
        if row_phase in {"pre_match", "prematch", "pre-race", "pre_race"}:
            return "pre_match"
    return "in_play" if int(row.get("in_play") or 0) == 1 else "pre_match"


def market_row_matches(
    row: Mapping,
    filters: MarketFilters,
    *,
    include_phase: bool = True,
    include_search: bool = False,
    search_fields: Iterable[str] = ("sport", "market_name", "section"),
    phase_field: str = "in_play",
    stream_phase_hint: bool = True,
) -> bool:
    if filters.scope == "sports" and str(row.get("section")) != "sports":
        return False
    if filters.scope == "racing" and str(row.get("section")) != "racing":
        return False
    if include_phase:
        if phase_field == "phase":
            row_phase = str(row.get("phase") or "pre_match")
            if filters.phase in {"pre_match", "in_play"} and row_phase != filters.phase:
                return False
        else:
            if filters.phase == "pre_match" and int(row.get("in_play") or 0) != 0:
                return False
            if filters.phase == "in_play" and int(row.get("in_play") or 0) != 1:
                return False
    if filters.streams and market_stream(row, phase_hint=stream_phase_hint) not in filters.streams:
        return False
    if filters.sport not in {"", "all"} and str(row.get("sport") or "") != filters.sport:
        return False
    if include_search and filters.search:
        hay = " ".join(str(row.get(field) or "") for field in search_fields).lower()
        if filters.search not in hay:
            return False
    return True


def heatmap_metric_ownership(mode: str) -> dict[str, str]:
    owner = "live" if str(mode or "sim").lower() == "live" else "sim"
    result = {metric: "shared" for metric in SHARED_HEATMAP_METRICS}
    result.update({metric: owner for metric in LIFECYCLE_HEATMAP_METRICS})
    if owner == "live":
        result["decision_qualified_evidence"] = "live_diagnostic"
    # Preserve public key insertion order from the legacy response.
    ordered = {}
    for metric in HEATMAP_METRICS:
        ordered[metric] = result[metric]
    if owner == "live":
        ordered["decision_qualified_evidence"] = "live_diagnostic"
    return ordered


def live_heatmap_cell(cell: Mapping, *, decision_count: int = 0) -> dict:
    """Return shared provider evidence with LIVE-owned actual lifecycle fields.

    Diagnostic decision qualification is deliberately separate from canonical
    LIVE Qualified/Executed/Settlement/P&L, which fail closed to zero while LIVE
    order writing remains locked.
    """
    item = dict(cell)
    item["decision_qualified_evidence"] = int(decision_count or 0)
    item["qualified"] = 0
    item["executed"] = 0
    item["deployed"] = 0.0
    item["settled"] = 0
    item["settled_deployed"] = 0.0
    item["pnl"] = 0.0
    item["roi_pct"] = 0.0
    return item


def live_market_row(row: Mapping, decision: Mapping | None = None) -> dict:
    """Overlay isolated LIVE decision evidence without borrowing SIM economics."""
    item = dict(row)
    decision = decision or {}
    item["live_decision_qualified"] = int(decision.get("qualified") or 0)
    item["qualified"] = 0
    item["attempts"] = 0
    item["executed"] = 0
    item["settled"] = 0
    item["pnl"] = 0.0
    item["deployed"] = 0.0
    item["returned"] = 0.0
    item["wins"] = 0
    item["losses"] = 0
    item["execution_conversion_pct"] = 0.0
    item["live_simulated_attempts"] = int(decision.get("simulated_attempts") or 0)
    item["live_execution_grade"] = int(decision.get("execution_grade") or 0)
    item["expected_simulated_profit"] = float(decision.get("expected_profit_sum") or 0.0)
    if decision.get("average_executable_stake") is not None:
        item["avg_executable_stake"] = float(decision.get("average_executable_stake") or 0.0)
    return item
