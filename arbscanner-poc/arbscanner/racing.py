from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

RACING_SPORTS = ("Greyhounds",)


def _ascii(value: str | None) -> str:
    if not value:
        return ""
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")


def normalize_track(value: str | None) -> str:
    """Return a conservative venue key for greyhound race matching.

    Exchange event labels often contain the venue plus a date, off time, race
    number or distance. Remove those volatile tokens while preserving the venue
    words. An empty result means the caller should not trust venue-only matching.
    """
    text = _ascii(value).lower().replace("&", " and ")
    text = re.sub(r"\b(?:greyhound|greyhounds|dogs?)\b", " ", text)
    text = re.sub(r"\b(?:race|r)\s*\d{1,2}\b", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", text)
    text = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\b", " ", text)
    text = re.sub(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", " ", text)
    text = re.sub(r"\b\d{3,4}\s*m\b", " ", text)
    text = re.sub(r"\b(?:a\d+|d\d+|s\d+|h\d+|open)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " ".join(text.split()).strip()
    # Exchanges frequently append a country marker to Australian/UK/NZ race
    # venues (for example ``Angle Park AUS``) while the other exchange carries
    # only the venue.  Country remains separate diagnostic metadata, so remove
    # only a trailing region marker from the canonical *track* key.
    text = re.sub(r"\s+(?:aus|australia|gb|uk|ire|ie|nz|new zealand|usa|us)$", "", text).strip()
    return text


def track_similarity(a: str | None, b: str | None) -> float:
    an, bn = normalize_track(a), normalize_track(b)
    if not an or not bn:
        return 0.0
    if an == bn:
        return 1.0
    return SequenceMatcher(None, an, bn).ratio()


def extract_race_number(*values: str | None) -> int | None:
    for value in values:
        text = _ascii(value)
        if not text:
            continue
        for pattern in (r"\bRace\s*(\d{1,2})\b", r"\bR\s*(\d{1,2})\b"):
            match = re.search(pattern, text, flags=re.I)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
    return None


def extract_trap_number(selection: str | None, raw: dict[str, Any] | None = None) -> int | None:
    raw = raw or {}
    for key in (
        "trap", "trap-number", "trap_number", "runner-number", "runner_number",
        "number", "clothNumber", "cloth_number", "sortPriority", "sort_priority",
    ):
        value = raw.get(key)
        if value is None:
            continue
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 12:
            return number
    text = _ascii(selection)
    if not text:
        return None
    patterns = (
        r"^\s*(?:trap\s*)?(\d{1,2})\s*[.:-]\s*",
        r"^\s*\((\d{1,2})\)\s*",
        r"\btrap\s*(\d{1,2})\b",
        r"\bT(\d{1,2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            try:
                number = int(match.group(1))
            except ValueError:
                continue
            if 1 <= number <= 12:
                return number
    return None


def normalize_runner_name(value: str | None) -> str:
    text = _ascii(value).lower().replace("&", " and ")
    text = re.sub(r"^\s*(?:trap\s*)?\d{1,2}\s*[.:-]\s*", "", text)
    text = re.sub(r"^\s*\(\d{1,2}\)\s*", "", text)
    text = re.sub(r"\s*\(t\d{1,2}\)\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split()).strip()


def canonical_runner_key(selection: str | None, trap_number: int | None = None) -> str:
    name = normalize_runner_name(selection)
    if trap_number is not None:
        return f"trap:{int(trap_number)}|{name}"
    return f"name:{name}"


def runner_match_score(
    left_name: str | None,
    right_name: str | None,
    left_trap: int | None = None,
    right_trap: int | None = None,
) -> float:
    """Strict greyhound runner matching.

    Trap disagreement is a hard failure. Matching traps plus matching names are
    strongest; when trap metadata is unavailable, names must be very close.
    """
    if left_trap is not None and right_trap is not None and int(left_trap) != int(right_trap):
        return 0.0
    ln, rn = normalize_runner_name(left_name), normalize_runner_name(right_name)
    if not ln or not rn:
        return 0.0
    name_score = 1.0 if ln == rn else SequenceMatcher(None, ln, rn).ratio()
    if left_trap is not None and right_trap is not None:
        return 0.75 + 0.25 * name_score
    return name_score


def seconds_to_off(start_time: str | None, now: datetime | None = None) -> int | None:
    if not start_time:
        return None
    value = str(start_time).strip()
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return int(round((dt.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds()))
    except Exception:
        return None


def is_withdrawn_status(value: str | None) -> bool:
    return str(value or "").strip().upper() in {"REMOVED", "WITHDRAWN", "NON_RUNNER", "NON-RUNNER", "SCRATCHED"}
