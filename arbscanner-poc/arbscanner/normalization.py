from __future__ import annotations
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from .models import ExchangeMarket, MarketMatch
from .sports import THREE_WAY_MATCH_ODDS_SPORTS, normalize_sport
from .racing import normalize_track, track_similarity, runner_match_score, canonical_runner_key

_DRAW = {"draw", "the draw", "tie", "x"}
_YES = {"yes", "y"}
_NO = {"no", "n"}
_OVER = {"over", "over 2 5", "over 2.5", "2 5 over", "2.5 over"}
_UNDER = {"under", "under 2 5", "under 2.5", "2 5 under", "2.5 under"}
_STOPWORDS = {"fc", "afc", "cf", "club", "football", "soccer"}


def norm_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9.]+", " ", value)
    words = [w for w in value.split() if w not in _STOPWORDS]
    return " ".join(words).strip()


def norm_selection(value: str | None) -> str:
    n = norm_text(value)
    compact = n.replace(".", "")
    if n in _DRAW:
        return "draw"
    if n in _YES:
        return "yes"
    if n in _NO:
        return "no"
    if n in _OVER or compact in {"over 25", "25 over"} or (n.startswith("over") and ("2.5" in n or "25" in compact)):
        return "over 2.5"
    if n in _UNDER or compact in {"under 25", "25 under"} or (n.startswith("under") and ("2.5" in n or "25" in compact)):
        return "under 2.5"
    return n


def classify_market(value: str | None, runner_count: int | None = None, sport: str | None = None) -> tuple[str, str]:
    """Return (canonical market type, strategy family).

    Sports remain deliberately constrained to the verified 2/3-runner families.
    Greyhound Racing adds a strict pre-race multi-runner WIN family.
    """
    n = norm_text(value)
    compact = n.replace(".", "")
    sp = normalize_sport(sport)

    if sp == "Greyhounds":
        # v0.8.3 intentionally supports WIN only. Reject place/forecast/tricast
        # and other derivative race markets even when they contain many runners.
        if any(token in n for token in ("place", "forecast", "tricast", "each way", "each-way", "without", "top ")):
            return n, "unknown"
        if runner_count is not None and runner_count >= 2 and (n in {"win", "winner", "to win", "race winner"} or " win" in f" {n}" or n.startswith("winner")):
            return "win", "multi_runner_win"
        return n, "unknown"

    # Reject obvious sub-period winner markets before the generic two-runner
    # winner rule. These are especially common in darts/snooker (leg/frame),
    # tennis/volleyball (set), North-American sports (quarter/period/inning)
    # and can otherwise look identical to the full-match market by runner names.
    subperiod_tokens = (
        "frame winner", "1st frame", "first frame",
        "leg winner", "1st leg", "first leg",
        "set winner", "1st set", "first set",
        "game winner", "quarter winner", "1st quarter", "first quarter",
        "half winner", "1st half", "first half",
        "period winner", "1st period", "first period",
        "inning winner", "innings winner", "1st inning", "first inning",
    )
    if any(token in n for token in subperiod_tokens):
        return n, "unknown"

    if sp in {"Football", "Unknown"}:
        if any(x in n for x in ("match odds", "match winner", "full time result", "moneyline", "money line")) or n == "1x2":
            return "match odds", "1x2" if runner_count == 3 else "two-way"
        if ("over under" in n or "total goals" in n or "goals" in n) and ("2.5" in n or "25" in compact):
            return "over/under 2.5 goals", "two-way"
        if "both teams to score" in n or "btts" in n:
            return "both teams to score", "two-way"

    # Selected team sports also have a standard three-runner full-time Match Odds
    # market. Keep it distinct from overtime-inclusive two-way winner markets.
    if sp in THREE_WAY_MATCH_ODDS_SPORTS and runner_count == 3 and (
        any(x in n for x in ("match odds", "match winner", "full time result")) or n == "1x2"
    ):
        return "match odds", "1x2"

    # Across non-football sports, a two-runner winner/moneyline is the safest
    # common denominator between exchanges.
    if runner_count == 2 and any(x in n for x in ("match odds", "match winner", "moneyline", "money line", "winner", "to win")):
        return "match winner", "two-way"

    # Conservative football fallback retained for Matchbook naming variations.
    if sp in {"Football", "Unknown"} and runner_count == 2 and n:
        if n in {"over under 2.5 goals", "both teams to score"}:
            return n, "two-way"
    return n, "unknown"


def market_settlement_scope(value: str | None, sport: str | None = None) -> str:
    """Coarse settlement scope used to prevent unsafe cross-venue matching.

    For sports where overtime treatment is material, an explicitly regulation-
    only market must never be paired with a full-game/overtime market. Generic
    full-match names remain compatible with each other.
    """
    n = norm_text(value)
    sp = normalize_sport(sport)
    if sp in {"Ice Hockey", "American Football", "Basketball", "Handball"}:
        if any(x in n for x in ("regulation", "60 minute", "60 min", "regular time", "3 way", "three way")):
            return "regulation"
        if any(x in n for x in ("including overtime", "incl overtime", "overtime included", "moneyline", "money line")):
            return "full_game"
    return "generic"


def norm_market(value: str | None, runner_count: int | None = None) -> str:
    return classify_market(value, runner_count)[0]


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    try:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def event_similarity(a: ExchangeMarket, b: ExchangeMarket, max_minutes: int = 20) -> float:
    if normalize_sport(a.sport) != normalize_sport(b.sport):
        return 0.0
    if normalize_sport(a.sport) == "Greyhounds":
        # Racing is much denser than ordinary sports schedules. Venue + off time
        # therefore matters more than loose event-name similarity.
        venue_score = track_similarity(a.race_track or a.event_name, b.race_track or b.event_name)
        if venue_score and venue_score < 0.88:
            return 0.0
        at, bt = parse_time(a.start_time), parse_time(b.start_time)
        if at and bt:
            mins = abs((at - bt).total_seconds()) / 60.0
            if mins > 5.0:
                return 0.0
            time_score = max(0.0, 1.0 - mins / 5.0)
            return 0.68 * (venue_score or 0.75) + 0.32 * time_score
        return (venue_score or 0.0) * 0.9
    an, bn = norm_text(a.event_name), norm_text(b.event_name)
    if not an or not bn:
        return 0.0
    name_score = SequenceMatcher(None, an, bn).ratio()
    at, bt = parse_time(a.start_time), parse_time(b.start_time)
    if at and bt:
        mins = abs((at - bt).total_seconds()) / 60.0
        if mins > max_minutes:
            return 0.0
        time_score = max(0.0, 1.0 - mins / max_minutes)
        return 0.8 * name_score + 0.2 * time_score
    return name_score * 0.9



def _effective_market_classification(market: ExchangeMarket) -> tuple[str, str]:
    """Prefer adapter-supplied canonical Racing metadata when it is explicit.

    Betfair/Matchbook APIs can use display market names that are not themselves a
    stable classifier.  The adapters already validate the exchange market type
    before constructing an ExchangeMarket, so do not throw that information away
    and then try to infer Racing WIN again from presentation text.
    """
    sp = normalize_sport(market.sport)
    if sp == "Greyhounds" and len(market.quotes) >= 2:
        strategy = str(market.strategy or "")
        market_type = str(market.market_type or "").lower()
        if strategy == "multi_runner_win" or market_type == "win":
            return "win", "multi_runner_win"
    return classify_market(market.market_name, len(market.quotes), market.sport)


def _racing_market_meta(market: ExchangeMarket) -> dict:
    raw = market.raw if isinstance(market.raw, dict) else {}
    catalogue = raw.get("catalogue") if isinstance(raw.get("catalogue"), dict) else {}
    event = catalogue.get("event") if isinstance(catalogue.get("event"), dict) else {}
    country = (
        raw.get("_arbscanner_event_country") or raw.get("country") or raw.get("countryCode")
        or raw.get("country_code") or event.get("countryCode") or event.get("country")
    )
    source_start = raw.get("_arbscanner_source_start_raw")
    if source_start is None and catalogue:
        source_start = catalogue.get("marketStartTime") or event.get("openDate")
    return {
        "country": str(country).upper() if country else None,
        "source_start_raw": source_start if source_start is not None else market.start_time,
        "canonical_start": market.start_time,
        "canonical_track": normalize_track(market.race_track or market.event_name),
    }


def racing_runner_alignment(a: ExchangeMarket, b: ExchangeMarket, threshold: float = 0.92) -> dict:
    """Return one-to-one runner alignment evidence for a Racing pair."""
    expected = len(a.quotes)
    if expected < 2 or len(b.quotes) != expected:
        return {
            "field_compatible": False, "matched": 0, "expected": expected,
            "average_score": 0.0, "minimum_score": 0.0, "aligned": False,
        }
    used: set[str] = set()
    scores: list[float] = []
    passed = 0
    for left in a.quotes:
        best = None
        for right in b.quotes:
            if str(right.selection_id) in used:
                continue
            score = float(runner_match_score(left.selection, right.selection, left.trap_number, right.trap_number))
            if best is None or score > best[0]:
                best = (score, right)
        if best is None:
            scores.append(0.0)
            continue
        scores.append(best[0])
        if best[0] >= threshold:
            passed += 1
            used.add(str(best[1].selection_id))
    return {
        "field_compatible": True,
        "matched": passed,
        "expected": expected,
        "average_score": (sum(scores) / len(scores)) if scores else 0.0,
        "minimum_score": min(scores) if scores else 0.0,
        "aligned": passed == expected,
    }


def racing_pair_identity(
    a: ExchangeMarket,
    b: ExchangeMarket,
    *,
    runner_threshold: float = 0.92,
    event_threshold: float = 0.90,
    track_threshold: float = 0.88,
    max_minutes: float = 5.0,
) -> dict:
    """Single source of truth for Greyhound race identity and strict pairing.

    Diagnostics and the strict matcher both consume this function.  An event can
    be a valid race candidate even when runner alignment subsequently fails; a
    strict match additionally requires equal fields, full runner alignment and the
    configured event-confidence threshold.
    """
    a_meta, b_meta = _racing_market_meta(a), _racing_market_meta(b)
    a_track, b_track = a_meta["canonical_track"], b_meta["canonical_track"]
    track_score = float(track_similarity(a_track, b_track))
    at, bt = parse_time(a.start_time), parse_time(b.start_time)
    delta = abs((at - bt).total_seconds()) / 60.0 if at and bt else None
    a_country = str(a_meta.get("country") or "").upper()
    b_country = str(b_meta.get("country") or "").upper()
    country_compatible = not (a_country and b_country and a_country != b_country)
    race_number_compatible = not (
        a.race_number is not None and b.race_number is not None
        and int(a.race_number) != int(b.race_number)
    )
    time_compatible = delta is None or delta <= float(max_minutes)
    track_compatible = track_score >= float(track_threshold)
    sport_compatible = normalize_sport(a.sport) == "Greyhounds" and normalize_sport(b.sport) == "Greyhounds"
    event_identity = bool(sport_compatible and track_compatible and time_compatible and country_compatible and race_number_compatible)
    runner = racing_runner_alignment(a, b, runner_threshold)
    score = float(event_similarity(a, b)) if sport_compatible else 0.0
    strict_match = bool(
        event_identity and runner["field_compatible"] and runner["aligned"]
        and score >= float(event_threshold)
    )
    return {
        "sport_compatible": sport_compatible,
        "track_score": track_score,
        "track_compatible": track_compatible,
        "source_track": a.race_track or a.event_name,
        "candidate_track": b.race_track or b.event_name,
        "source_track_key": a_track,
        "candidate_track_key": b_track,
        "source_start_raw": a_meta.get("source_start_raw"),
        "candidate_start_raw": b_meta.get("source_start_raw"),
        "source_start_utc": a.start_time,
        "candidate_start_utc": b.start_time,
        "time_delta_minutes": delta,
        "time_compatible": time_compatible,
        "source_country": a_meta.get("country"),
        "candidate_country": b_meta.get("country"),
        "country_compatible": country_compatible,
        "source_race_number": a.race_number,
        "candidate_race_number": b.race_number,
        "race_number_compatible": race_number_compatible,
        "source_runner_count": len(a.quotes),
        "candidate_runner_count": len(b.quotes),
        "field_compatible": bool(runner["field_compatible"]),
        "runner_match_count": int(runner["matched"]),
        "runner_expected": int(runner["expected"]),
        "runner_score": float(runner["average_score"]),
        "runner_min_score": float(runner["minimum_score"]),
        "runner_aligned": bool(runner["aligned"]),
        "event_score": score,
        "event_identity": event_identity,
        "strict_match": strict_match,
    }

def racing_fields_align(a: ExchangeMarket, b: ExchangeMarket, threshold: float = 0.92) -> bool:
    """Return True only when every Greyhound runner aligns one-to-one."""
    return bool(racing_runner_alignment(a, b, threshold).get("aligned"))


def _pair_match_score(
    a: ExchangeMarket,
    b: ExchangeMarket,
    *,
    threshold: float,
    racing_threshold: float,
    racing_runner_threshold: float,
) -> float | None:
    """Return a compatibility score for two provider markets or ``None``.

    The rules intentionally preserve the established two-provider matching
    semantics while making them reusable by the N-venue clustering layer.
    """
    if str(a.provider_id or a.exchange).lower() == str(b.provider_id or b.exchange).lower():
        return None
    a_type, a_strategy = _effective_market_classification(a)
    b_type, b_strategy = _effective_market_classification(b)
    if a_type != b_type or a_strategy != b_strategy or len(a.quotes) != len(b.quotes):
        return None
    a_scope = market_settlement_scope(a.market_name, a.sport)
    b_scope = market_settlement_scope(b.market_name, b.sport)
    if a_scope != b_scope and "generic" not in {a_scope, b_scope}:
        return None
    if "regulation" in {a_scope, b_scope} and a_scope != b_scope:
        return None
    local_threshold = racing_threshold if a_strategy == "multi_runner_win" else threshold
    if a_strategy == "multi_runner_win":
        identity = racing_pair_identity(
            a, b, runner_threshold=racing_runner_threshold,
            event_threshold=local_threshold,
        )
        if not identity["strict_match"]:
            return None
        return float(identity["event_score"])
    score = event_similarity(a, b)
    return float(score) if score >= local_threshold else None


def _market_stable_key(m: ExchangeMarket) -> tuple:
    return (
        str(m.start_time or ""),
        norm_text(m.event_name),
        str(m.market_id or ""),
        str(m.provider_id or m.exchange or "").lower(),
    )


def _canonical_representative(markets: list[ExchangeMarket]) -> ExchangeMarket:
    """Choose display/canonical source without depending on registration order."""
    return min(
        markets,
        key=lambda m: (
            -len(m.quotes),
            str(m.start_time or "~"),
            norm_text(m.event_name),
            str(m.market_id or ""),
            str(m.provider_id or m.exchange or "").lower(),
        ),
    )


def _best_compatible_cluster(
    remaining: list[ExchangeMarket],
    pair_scores: dict[tuple[int, int], float],
) -> list[ExchangeMarket]:
    """Find the strongest pairwise-compatible one-market-per-provider cluster.

    Provider counts are currently small (two, soon three/four), so a bounded
    backtracking search is clearer and safer than a primary-provider greedy
    matcher. The result is deterministic and independent from registration/input
    order. Only markets connected by pairwise compatibility can coexist.
    """
    indexed = list(enumerate(remaining))
    by_provider: dict[str, list[tuple[int, ExchangeMarket]]] = {}
    for idx, market in indexed:
        pid = str(market.provider_id or market.exchange or "").lower()
        by_provider.setdefault(pid, []).append((idx, market))
    providers = sorted(by_provider)
    best: tuple[tuple, list[tuple[int, ExchangeMarket]]] | None = None

    def score_cluster(chosen: list[tuple[int, ExchangeMarket]]) -> tuple:
        scores = []
        for i in range(len(chosen)):
            for j in range(i + 1, len(chosen)):
                a, b = sorted((chosen[i][0], chosen[j][0]))
                scores.append(pair_scores.get((a, b), -1.0))
        mean = sum(scores) / len(scores) if scores else -1.0
        minimum = min(scores) if scores else -1.0
        stable = tuple(_market_stable_key(x[1]) for x in sorted(chosen, key=lambda x: _market_stable_key(x[1])))
        # More venues first, then strongest weakest-link and average similarity.
        return (len(chosen), minimum, mean, tuple(reversed(stable)))

    def compatible(candidate_idx: int, chosen: list[tuple[int, ExchangeMarket]]) -> bool:
        for other_idx, _ in chosen:
            key = tuple(sorted((candidate_idx, other_idx)))
            if key not in pair_scores:
                return False
        return True

    def walk(provider_pos: int, chosen: list[tuple[int, ExchangeMarket]]) -> None:
        nonlocal best
        # Even a partial cluster is a candidate once it spans two venues.
        if len(chosen) >= 2:
            score = score_cluster(chosen)
            if best is None or score > best[0]:
                best = (score, list(chosen))
        if provider_pos >= len(providers):
            return
        # Bound: if all remaining providers cannot beat current venue count, stop.
        if best is not None and len(chosen) + (len(providers) - provider_pos) < best[0][0]:
            return
        pid = providers[provider_pos]
        # Skipping a provider is valid; some canonical markets may exist on only a
        # subset of enabled venues.
        walk(provider_pos + 1, chosen)
        for item in sorted(by_provider[pid], key=lambda x: _market_stable_key(x[1])):
            if compatible(item[0], chosen):
                chosen.append(item)
                walk(provider_pos + 1, chosen)
                chosen.pop()

    walk(0, [])
    return [x[1] for x in best[1]] if best else []


def match_markets(
    markets: list[ExchangeMarket],
    threshold: float = 0.72,
    racing_threshold: float = 0.90,
    racing_runner_threshold: float = 0.92,
) -> list[MarketMatch]:
    """Cluster equivalent economic markets across two or more venues.

    0.9.0 replaces the historical primary-exchange + one-counterpart matcher.
    The canonical cluster retains every mutually compatible venue, making
    Betfair/Matchbook/Smarkets/BETDAQ coexist without silently dropping the third
    or fourth provider. Existing two-provider semantics and settlement checks are
    preserved by :func:`_pair_match_score`.
    """
    eligible: list[ExchangeMarket] = []
    for m in markets:
        canonical, strategy = _effective_market_classification(m)
        if strategy not in {"1x2", "two-way", "multi_runner_win"}:
            continue
        if strategy == "multi_runner_win":
            if len(m.quotes) < 2:
                continue
            m.section = "racing"
            m.race_track = m.race_track or normalize_track(m.event_name)
        elif len(m.quotes) not in {2, 3}:
            continue
        m.market_type = canonical
        m.strategy = strategy
        eligible.append(m)
    if len({str(m.provider_id or m.exchange).lower() for m in eligible}) < 2:
        return []

    # Partition by market structure first to keep pairwise work bounded.
    buckets: dict[tuple, list[ExchangeMarket]] = {}
    for m in eligible:
        m_type, strategy = _effective_market_classification(m)
        buckets.setdefault((m_type, strategy, len(m.quotes), normalize_sport(m.sport)), []).append(m)

    matches: list[MarketMatch] = []
    for (market_type, strategy, _runner_len, sport), bucket in buckets.items():
        bucket = sorted(bucket, key=_market_stable_key)
        # Build pair compatibility by stable local indices.
        pair_scores: dict[tuple[int, int], float] = {}
        for i, a in enumerate(bucket):
            for j in range(i + 1, len(bucket)):
                b = bucket[j]
                score = _pair_match_score(
                    a, b, threshold=threshold, racing_threshold=racing_threshold,
                    racing_runner_threshold=racing_runner_threshold,
                )
                if score is not None:
                    pair_scores[(i, j)] = score
        remaining = list(bucket)
        while len({str(m.provider_id or m.exchange).lower() for m in remaining}) >= 2:
            # Re-index scores for the current remainder, retaining only compatible edges.
            rem_index = {id(m): i for i, m in enumerate(remaining)}
            rem_scores: dict[tuple[int, int], float] = {}
            orig_index = {id(m): i for i, m in enumerate(bucket)}
            for i, a in enumerate(remaining):
                for j in range(i + 1, len(remaining)):
                    oa, ob = sorted((orig_index[id(a)], orig_index[id(remaining[j])]))
                    if (oa, ob) in pair_scores:
                        rem_scores[(i, j)] = pair_scores[(oa, ob)]
            cluster = _best_compatible_cluster(remaining, rem_scores)
            if len(cluster) < 2:
                break
            rep = _canonical_representative(cluster)
            is_racing = strategy == "multi_runner_win"
            race_track = normalize_track(rep.race_track or rep.event_name) if is_racing else None
            start_time = next((m.start_time for m in sorted(cluster, key=_market_stable_key) if m.start_time), None)
            event_key = f"{race_track}|{start_time or ''}" if is_racing else norm_text(rep.event_name)
            # Canonical IDs are provider-neutral and deterministic from economic identity.
            import hashlib
            canonical_event_id = "evt:" + hashlib.sha256(f"{sport}|{event_key}|{start_time or ''}".encode()).hexdigest()[:20]
            canonical_market_id = "mkt:" + hashlib.sha256(f"{canonical_event_id}|{market_type}|{strategy}".encode()).hexdigest()[:20]
            for m in cluster:
                m.canonical_event_id = canonical_event_id
                m.canonical_market_id = canonical_market_id
            cluster_scores = []
            for i in range(len(cluster)):
                for j in range(i + 1, len(cluster)):
                    # Recalculate is cheap and avoids dependence on transient indices.
                    score = _pair_match_score(cluster[i], cluster[j], threshold=threshold,
                                              racing_threshold=racing_threshold,
                                              racing_runner_threshold=racing_runner_threshold)
                    if score is not None:
                        cluster_scores.append(score)
            score = sum(cluster_scores) / len(cluster_scores) if cluster_scores else 0.0
            matches.append(MarketMatch(
                event_key=event_key,
                market_key=market_type,
                display_event=rep.event_name,
                display_market=("Win" if market_type == "win" else "Match Odds" if market_type == "match odds" else "Match Winner" if market_type == "match winner" else market_type.title()),
                start_time=start_time,
                markets=sorted(cluster, key=lambda m: str(m.provider_id or m.exchange).lower()),
                match_score=score,
                market_type=market_type,
                strategy=strategy,
                sport=sport,
                in_play=True if any(m.in_play is True for m in cluster) else False if all(m.in_play is False for m in cluster) else None,
                status=next((m.status for m in cluster if m.status), None),
                section="racing" if is_racing else "sports",
                race_track=race_track,
                race_number=next((m.race_number for m in cluster if m.race_number is not None), None),
                runner_count=len(rep.quotes),
                canonical_event_id=canonical_event_id,
                canonical_market_id=canonical_market_id,
            ))
            chosen_ids = {id(m) for m in cluster}
            remaining = [m for m in remaining if id(m) not in chosen_ids]
    return sorted(matches, key=lambda m: (str(m.start_time or ""), m.sport, m.event_key, m.market_key))


def align_quotes(match: MarketMatch, threshold: float = 0.68, racing_threshold: float = 0.92) -> dict[str, list]:
    """Align mutually exclusive selections across matched exchange markets.

    Racing uses trap identity when available and a deliberately stricter fallback
    name threshold. A disagreement or incomplete field rejects the whole market.
    """
    if not match.markets:
        return {}
    base_quotes = match.markets[0].quotes
    expected = len(base_quotes)
    if match.strategy == "multi_runner_win":
        if expected < 2:
            return {}
        groups: dict[str, list] = {q.selection: [q] for q in base_quotes}
        for market in match.markets[1:]:
            if len(market.quotes) != expected:
                return {}
            used: set[str] = set()
            for base in base_quotes:
                best = None
                for quote in market.quotes:
                    if quote.selection_id in used:
                        continue
                    score = runner_match_score(base.selection, quote.selection, base.trap_number, quote.trap_number)
                    if best is None or score > best[0]:
                        best = (score, quote)
                if not best or best[0] < racing_threshold:
                    return {}
                matched = best[1]
                # When both sides expose trap metadata, a matching name is still
                # required; this protects against stale/reordered race cards.
                used.add(matched.selection_id)
                groups[base.selection].append(matched)
        return groups

    if expected not in {2, 3}:
        return {}
    groups = {q.selection: [q] for q in base_quotes}
    base_norm = {q.selection: norm_selection(q.selection) for q in base_quotes}
    for market in match.markets[1:]:
        if len(market.quotes) != expected:
            return {}
        used: set[str] = set()
        for label, base_n in base_norm.items():
            best = None
            for quote in market.quotes:
                if quote.selection_id in used:
                    continue
                qn = norm_selection(quote.selection)
                if base_n == qn:
                    score = 1.0
                elif base_n in {"draw", "yes", "no", "over 2.5", "under 2.5"} or qn in {"draw", "yes", "no", "over 2.5", "under 2.5"}:
                    score = 0.0
                else:
                    score = SequenceMatcher(None, base_n, qn).ratio()
                if best is None or score > best[0]:
                    best = (score, quote)
            if not best or best[0] < threshold:
                return {}
            used.add(best[1].selection_id)
            groups[label].append(best[1])
    return groups

