from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.normalization import match_markets, racing_pair_identity
from arbscanner.racing import normalize_track
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _race(
    exchange: str,
    market_id: str,
    *,
    track: str = "Romford",
    start: str = "2026-08-11T14:19:00+00:00",
    display_market: str = "Win",
    runners: int = 5,
    bad_runner: bool = False,
) -> ExchangeMarket:
    quotes = []
    for trap in range(1, runners + 1):
        selection = f"Runner {trap}"
        if bad_runner and trap == runners:
            selection = "Different Dog"
        quotes.append(Quote(
            exchange=exchange,
            event_id=f"{exchange}-romford",
            market_id=market_id,
            event_name=track,
            market_name=display_market,
            selection_id=f"{market_id}-{trap}",
            selection=selection,
            odds=3.0 + trap,
            liquidity=100.0,
            captured_at=datetime.now(timezone.utc).isoformat(),
            start_time=start,
            commission_pct=2.0,
            sport="Greyhounds",
            market_type="win",
            strategy="multi_runner_win",
            in_play=False,
            market_status="OPEN",
            section="racing",
            trap_number=trap,
            canonical_selection_key=f"trap:{trap}|{selection.lower().replace(' ', '-')}",
            runner_status="ACTIVE",
        ))
    return ExchangeMarket(
        exchange=exchange,
        event_id=f"{exchange}-romford",
        market_id=market_id,
        event_name=track,
        market_name=display_market,
        start_time=start,
        quotes=quotes,
        status="OPEN",
        market_type="win",
        strategy="multi_runner_win",
        sport="Greyhounds",
        in_play=False,
        raw={
            "_arbscanner_source_start_raw": start,
            "_arbscanner_start_utc": start,
            "_arbscanner_event_country": "GB",
            "_arbscanner_catalogue_runner_count": runners,
            "_arbscanner_priced_runner_count": runners,
        },
        section="racing",
        race_track=normalize_track(track),
        race_number=None,
    )


def test_romford_exact_pair_uses_adapter_canonical_win_metadata_not_display_name():
    # Betfair/Matchbook adapters already validate the market family. A display
    # label should not make an otherwise identical Racing WIN disappear later.
    bf = _race("Betfair delayed", "bf-romford", display_market="R1 15:19")
    mb = _race("Matchbook", "mb-romford", display_market="Win")
    matches = match_markets([bf, mb], racing_threshold=0.90, racing_runner_threshold=0.92)
    assert len(matches) == 1
    assert matches[0].race_track == "romford"
    assert matches[0].runner_count == 5


def test_shared_identity_and_diagnostics_never_hide_exact_romford_candidate(tmp_path):
    bf = _race("Betfair delayed", "bf-romford", bad_runner=True)
    mb = _race("Matchbook", "mb-romford")
    identity = racing_pair_identity(bf, mb, event_threshold=0.90, runner_threshold=0.92)
    assert identity["track_compatible"] is True
    assert identity["time_compatible"] is True
    assert identity["field_compatible"] is True
    assert identity["event_identity"] is True
    assert identity["runner_aligned"] is False
    assert identity["strict_match"] is False

    scanner = Scanner(DB(tmp_path / "pair-audit.sqlite3"), SecretStore(), producer="worker")
    result = scanner._racing_discovery_diagnostics(
        [bf, mb], [],
        {"racing_match_threshold": 0.90, "racing_runner_match_threshold": 0.92},
        statuses=[],
    )
    assert result["producer"] == {"component": "worker", "version": __version__}
    assert result["summary"]["race_candidates"] == 1
    assert result["summary"]["event_pairs"] == 1
    assert result["summary"]["runner_aligned"] == 0
    assert result["summary"]["candidates"] == 1
    mb_row = next(x for x in result["rows"] if x["exchange"] == "Matchbook")
    assert mb_row["counterpart"] is not None
    assert mb_row["counterpart"]["race_track"] == "romford"
    assert mb_row["counterpart"]["checks"]["track"] == "PASS"
    assert mb_row["counterpart"]["checks"]["time"] == "PASS"
    assert mb_row["counterpart"]["checks"]["field"] == "PASS"
    assert mb_row["counterpart"]["checks"]["runners"] == "FAIL"
    assert mb_row["counterpart"]["checks"]["strict"] == "FAIL"
    assert mb_row["feed_quality"] == "complete"
    assert "runner alignment" in mb_row["reason"].lower()


def test_exact_strict_pair_is_matched_by_same_shared_identity(tmp_path):
    bf = _race("Betfair delayed", "bf-romford")
    mb = _race("Matchbook", "mb-romford")
    matches = match_markets([bf, mb], racing_threshold=0.90, racing_runner_threshold=0.92)
    scanner = Scanner(DB(tmp_path / "strict.sqlite3"), SecretStore(), producer="app")
    result = scanner._racing_discovery_diagnostics(
        [bf, mb], matches,
        {"racing_match_threshold": 0.90, "racing_runner_match_threshold": 0.92},
        statuses=[],
    )
    assert result["summary"]["matched"] == 1
    assert result["summary"]["runner_aligned"] == 1
    assert all(x["match_status"] == "matched" for x in result["rows"])
    assert all(x["counterpart"]["checks"]["strict"] == "PASS" for x in result["rows"])


def test_racing_overview_propagates_pairing_funnel_and_producer(tmp_path):
    api = API(db_path=tmp_path / "overview.sqlite3")
    api.db.set_setting("racing_discovery_latest", {
        "observed_at": "2026-08-11T14:00:00+00:00",
        "producer": {"component": "worker", "version": __version__},
        "summary": {
            "matched": 0,
            "candidates": 1,
            "race_candidates": 3,
            "event_pairs": 1,
            "runner_aligned": 0,
            "unmatched": 4,
            "rejected": 0,
            "by_exchange": {"Betfair delayed": 5, "Matchbook": 2},
            "betfair_feed": {"catalogue": 5, "fully_priced": 5, "incomplete_prices": 0},
        },
        "rows": [],
    })
    result = api.racing_overview({})
    assert result["producer"]["component"] == "worker"
    assert result["summary"]["race_candidates"] == 3
    assert result["summary"]["event_pairs"] == 1
    assert result["summary"]["runner_aligned"] == 0


def test_racing_ui_exposes_pair_audit_feed_quality_and_producer_provenance():
    assert __version__ == "0.9.36"
    assert "Identity audit" in HTML
    assert "Discovery producer" in HTML
    assert "STALE PRODUCER" in HTML
    assert "Feed quality" in HTML
    assert 'id="raceRawCandidates"' in HTML
    assert 'id="raceEventPairs"' in HTML
    assert 'id="raceRunnerAligned"' in HTML
