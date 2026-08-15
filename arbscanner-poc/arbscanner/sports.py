from __future__ import annotations

SUPPORTED_SPORTS = (
    "Football",
    "Tennis",
    "Darts",
    "Snooker",
    "Basketball",
    "American Football",
    "Baseball",
    "Ice Hockey",
    "Cricket",
    "Rugby Union",
    "Rugby League",
    "Volleyball",
    "Handball",
    "Australian Rules",
    "Field Hockey",
)

SUPPORTED_RACING = ("Greyhounds",)
SUPPORTED_MARKETS = SUPPORTED_SPORTS + SUPPORTED_RACING

# Sports where a standard full-time Match Odds market can legitimately contain
# Home / Draw / Away. These are still kept separate from overtime-inclusive
# two-way winner markets.
THREE_WAY_MATCH_ODDS_SPORTS = frozenset({
    "Football",
    "Rugby Union",
    "Rugby League",
    "Handball",
    "Australian Rules",
    "Field Hockey",
})

_ALIASES = {
    "soccer": "Football",
    "football": "Football",
    "association football": "Football",
    "tennis": "Tennis",
    "darts": "Darts",
    "snooker": "Snooker",
    "basketball": "Basketball",
    "american football": "American Football",
    "nfl": "American Football",
    "ncaa football": "American Football",
    "baseball": "Baseball",
    "ice hockey": "Ice Hockey",
    "cricket": "Cricket",
    "rugby union": "Rugby Union",
    "rugby league": "Rugby League",
    "volleyball": "Volleyball",
    "volley ball": "Volleyball",
    "handball": "Handball",
    "australian rules": "Australian Rules",
    "australian rules football": "Australian Rules",
    "aussie rules": "Australian Rules",
    "afl": "Australian Rules",
    # Betfair commonly distinguishes Ice Hockey from Hockey. Treat bare
    # "Hockey" as field hockey so the two sports cannot be cross-matched.
    "hockey": "Field Hockey",
    "field hockey": "Field Hockey",
    "greyhound": "Greyhounds",
    "greyhounds": "Greyhounds",
    "greyhound racing": "Greyhounds",
    "greyhound races": "Greyhounds",
}

BETFAIR_EVENT_TYPE_ALIASES = {
    "Soccer": "Football",
    "Tennis": "Tennis",
    "Darts": "Darts",
    "Snooker": "Snooker",
    "Basketball": "Basketball",
    "American Football": "American Football",
    "Baseball": "Baseball",
    "Ice Hockey": "Ice Hockey",
    "Cricket": "Cricket",
    "Rugby Union": "Rugby Union",
    "Rugby League": "Rugby League",
    "Volleyball": "Volleyball",
    "Handball": "Handball",
    "Australian Rules": "Australian Rules",
    "Hockey": "Field Hockey",
    "Field Hockey": "Field Hockey",
    "Greyhounds": "Greyhounds",
    "Greyhound Racing": "Greyhounds",
}

# Deliberately narrow market-code allowlist. We favour standard winner / match
# odds markets and football's already-tested totals/BTTS rather than ingesting
# handicaps, periods, sets, innings or props with exchange-specific settlement.
BETFAIR_SAFE_MARKET_CODES = {
    "Football": {"MATCH_ODDS", "OVER_UNDER_25", "BOTH_TEAMS_TO_SCORE"},
    "Tennis": {"MATCH_ODDS", "WINNER"},
    "Darts": {"MATCH_ODDS", "WINNER"},
    "Snooker": {"MATCH_ODDS", "WINNER"},
    "Basketball": {"MATCH_ODDS", "MONEYLINE", "MONEY_LINE", "WINNER"},
    "American Football": {"MATCH_ODDS", "MONEYLINE", "MONEY_LINE", "WINNER"},
    "Baseball": {"MATCH_ODDS", "MONEYLINE", "MONEY_LINE", "WINNER"},
    "Ice Hockey": {"MATCH_ODDS", "MONEYLINE", "MONEY_LINE", "WINNER"},
    "Cricket": {"MATCH_ODDS", "WINNER"},
    "Rugby Union": {"MATCH_ODDS", "WINNER"},
    "Rugby League": {"MATCH_ODDS", "WINNER"},
    "Volleyball": {"MATCH_ODDS", "WINNER"},
    "Handball": {"MATCH_ODDS", "WINNER"},
    "Australian Rules": {"MATCH_ODDS", "WINNER"},
    "Field Hockey": {"MATCH_ODDS", "WINNER"},
    "Greyhounds": {"WIN", "WINNER"},
}


def normalize_sport(value: str | None) -> str:
    if not value:
        return "Unknown"
    raw = str(value).strip()
    key = raw.lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    return _ALIASES.get(key, raw.title())


def enabled_sports_from_config(cfg: dict) -> list[str]:
    """Return every enabled exchange event domain (Sports plus Racing)."""
    out = []
    for sport in SUPPORTED_SPORTS:
        key = "sport_" + sport.lower().replace(" ", "_") + "_enabled"
        if cfg.get(key, True):
            out.append(sport)
    if cfg.get("racing_greyhounds_enabled", True):
        out.append("Greyhounds")
    return out


def is_supported(sport: str | None) -> bool:
    return normalize_sport(sport) in SUPPORTED_MARKETS


def is_allowed_market_shape(sport: str | None, canonical: str, strategy: str) -> bool:
    sp = normalize_sport(sport)
    if sp == "Greyhounds":
        return canonical == "win" and strategy == "multi_runner_win"
    if sp == "Football":
        return canonical in {"match odds", "over/under 2.5 goals", "both teams to score"}
    if canonical == "match winner" and strategy == "two-way":
        return True
    return sp in THREE_WAY_MATCH_ODDS_SPORTS and canonical == "match odds" and strategy == "1x2"
