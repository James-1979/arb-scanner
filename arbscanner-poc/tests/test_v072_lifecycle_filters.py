from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.replay import replay_analysis


class FakeService:
    def __init__(self):
        self.loaded = False
        self.installed = False

    def worker_path(self):
        return Path('/tmp/fake-worker')

    def status(self):
        return {
            'installed': self.installed,
            'loaded': self.loaded,
            'worker_path': '/tmp/fake-worker',
        }

    def install(self):
        self.installed = True
        self.loaded = True
        return {'ok': True, 'message': 'loaded', **self.status()}

    def uninstall(self):
        self.installed = False
        self.loaded = False
        return {'ok': True, 'message': 'removed', **self.status()}


def add_opp(db: DB, key: str, event_start: str, detected: str, sport='Football') -> int:
    legs = [
        {'exchange': 'Matchbook', 'selection': 'Home', 'odds': 2.2, 'liquidity': 500.0, 'commission_pct': 0.0, 'sport': sport},
        {'exchange': 'Betfair delayed', 'selection': 'Away', 'odds': 2.2, 'liquidity': 500.0, 'commission_pct': 0.0, 'sport': sport},
    ]
    oid = db.add_opportunity(
        key, key.title(), event_start, 'Match Winner', 9.0, 10.0,
        legs, [], 0.99, f'sig-{key}', strategy='two-way', sport=sport,
    )
    db.conn.execute('UPDATE opportunities SET detected_at=? WHERE id=?', (detected, oid))
    db.conn.commit()
    return oid


def test_mode_aware_start_stop_controls_background_automation(tmp_path: Path):
    api = API(tmp_path / 'lifecycle.sqlite3')
    api.service = FakeService()

    changed = api.set_operating_mode({'mode': 'monitor_timing'})
    assert changed['ok'] is True

    started = api.start_automation({'mode': 'monitor_timing'})
    assert started['ok'] is True
    assert started['state']['automation']['running'] is True
    assert started['state']['automation']['mode'] == 'sim'
    assert started['state']['settings']['config']['scanner_enabled'] is True
    assert started['state']['automation']['started_at'] is None

    stopped = api.stop_automation()
    assert stopped['ok'] is True
    assert stopped['state']['automation']['running'] is True
    assert stopped['state']['automation']['mode'] == 'sim'
    assert stopped['state']['automation']['status'] == 'SIM ACTIVE'
    assert stopped['state']['settings']['config']['scanner_enabled'] is True
    # Legacy MONITOR_TIMING stop is a safe alias back to MONITOR; worker stays loaded.
    assert stopped['state']['background']['loaded'] is True

    locked = api.start_automation({'mode': 'live'})
    assert locked['ok'] is False
    assert 'locked' in locked['message'].lower()


def test_activity_filters_apply_period_mode_sport_exchange_market_and_search(tmp_path: Path):
    api = API(tmp_path / 'filters.sqlite3')
    current = add_opp(api.db, 'alpha v beta', '2026-08-09T12:00:00+00:00', '2026-08-09T10:00:00+00:00')
    old = add_opp(api.db, 'old v match', '2026-07-01T12:00:00+00:00', '2026-07-01T10:00:00+00:00')
    api.db.settle(current, 'Home')
    api.db.settle(old, 'Away')
    # Settled-result analytics are intentionally settlement-time based. Pin the
    # fixture settlement observations into their historical query windows rather
    # than inheriting the wall-clock time at which this test happens to run.
    api.db.conn.execute(
        'UPDATE settlements SET settled_at=? WHERE opportunity_id=?',
        ('2026-08-09T13:00:00+00:00', current),
    )
    api.db.conn.execute(
        'UPDATE settlements SET settled_at=? WHERE opportunity_id=?',
        ('2026-07-01T13:00:00+00:00', old),
    )
    api.db.conn.commit()
    api.db.add_execution_run(current, 'monitor_timing', 'captured_stress', 'STRESS_TESTED', started_at='2026-08-09T10:01:00+00:00')
    api.db.add_execution_run(old, 'live', 'live_order', 'COMPLETE', is_real=True, started_at='2026-07-01T10:01:00+00:00')

    result = api.activity_analytics({
        'from_utc': '2026-08-09T00:00:00+00:00',
        'to_utc': '2026-08-10T00:00:00+00:00',
        'mode': 'monitor_timing',
        'sport': 'Football',
        'exchange': 'betfair',
        'market': 'Winner',
        'search': 'alpha',
    })
    assert result['ok'] is True
    assert len(result['results']) == 1
    assert result['results'][0]['event_name'] == 'Alpha V Beta'
    assert result['results'][0]['exchanges'] == ['Betfair delayed', 'Matchbook']
    assert len(result['executions']) == 1
    assert result['execution_counts']['monitor']['count'] == 1
    assert result['summary']['unique_settled_markets'] == 1
    assert result['summary']['settled_opportunities'] == 1

    no_live = api.activity_analytics({
        'from_utc': '2026-08-09T00:00:00+00:00',
        'to_utc': '2026-08-10T00:00:00+00:00',
        'mode': 'live',
    })
    assert no_live['results'] == []
    assert no_live['executions'] == []


def test_replay_accepts_explicit_utc_period_and_exchange_filter(tmp_path: Path):
    db = DB(tmp_path / 'replay-filter.sqlite3')
    current = add_opp(db, 'alpha v beta', '2026-08-09T12:00:00+00:00', '2026-08-09T10:00:00+00:00')
    old = add_opp(db, 'old v match', '2026-07-01T12:00:00+00:00', '2026-07-01T10:00:00+00:00')
    db.settle(current, 'Home')
    db.settle(old, 'Away')

    from datetime import datetime, timezone
    result = replay_analysis(
        db, 500.0, min_profit=0.0, min_deployed_roi_pct=0.0,
        date_from=datetime(2026, 8, 9, tzinfo=timezone.utc),
        date_to=datetime(2026, 8, 10, tzinfo=timezone.utc),
        exchange='matchbook', search='alpha',
    )
    assert result['counts']['settled_available'] == 1
    assert len(result['events']) == 1
    assert result['events'][0]['event_name'] == 'Alpha V Beta'
    assert result['filters']['exchange'] == 'matchbook'
    assert result['filters']['date_from'].startswith('2026-08-09')


def test_frontend_has_final_lifecycle_and_shared_analytics_filters():
    html = (Path(__file__).parents[1] / 'frontend' / 'index.html').read_text()
    assert 'id="automationActionBtn"' in html
    assert 'start_automation' in html
    assert 'stop_automation' in html
    assert 'id="activityPeriod"' in html
    assert 'Last 24 hours' in html
    assert 'Last 7 days' in html
    assert 'Last 30 days' in html
    assert 'This month' in html
    assert 'Custom dates' in html
    assert 'id="activityMode"' in html
    assert 'id="activitySport"' in html
    assert 'id="activityExchange"' in html
    assert 'id="activityMarket"' in html
    assert 'id="activitySearch"' in html
    assert 'id="analyticsStrategy"' in html
