from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _nav_html():
    return HTML.split('<div class="nav" id="nav"', 1)[1].split('<section id="dashboard"', 1)[0]


def _analytics_html():
    return HTML.split('<section id="analytics" class="page">', 1)[1].split('</section>', 1)[0]


def test_analytics_sections_live_in_left_navigation_not_inside_page_tabs():
    nav = _nav_html()
    for pane, label in (
        ("performance", "Performance"),
        ("market", "Market Analysis"),
        ("replay", "Replay"),
        ("scenarios", "Scenarios"),
    ):
        assert f'data-nav-child="analytics" data-analytics-tab="{pane}"' in nav
        assert f'>{label}<' in nav
    assert 'data-nav-child="analytics" data-analytics-tab="execution"' not in nav
    assert 'data-tab="sports-engines" data-nav-child="sports"' in nav
    assert 'data-tab="racing-engines" data-nav-child="racing"' in nav
    assert 'data-tab="sports-execution" data-nav-child="sports"' not in nav
    assert 'data-tab="racing-execution" data-nav-child="racing"' not in nav
    analytics = _analytics_html()
    assert 'class="analytics-tabs"' not in analytics
    assert 'role="tablist" aria-label="Analytics sections"' not in analytics


def test_clocks_are_larger_and_sports_simulated_badge_uses_blue_info_palette():
    assert 'aspect-ratio:1/1!important' in HTML
    assert '.tag.info{background:#eaf2ff;color:#1849a9' in HTML
    sports = HTML.split('<section id="sports" class="page">', 1)[1].split('</section>', 1)[0]
    assert '<span class="tag info">SIMULATED</span>' in sports
    assert '<span class="tag warn">SIMULATED</span>' not in sports


def _insert_settled_position(api, detected_at, key, pnl):
    db = api.db
    stamp = detected_at.astimezone(timezone.utc).isoformat()
    with db.lock:
        cur = db.conn.execute(
            """INSERT INTO opportunities(
                detected_at,event_key,event_name,event_start,market_name,edge_pct,expected_roi_pct,
                legs_json,is_demo,status,strategy,sport,section,qualification_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (stamp, key, key, stamp, "Match Winner", 2.0, 1.2, "[]", 0,
             "settled", "two-way", "Tennis", "sports", "qualified"),
        )
        oid = int(cur.lastrowid)
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,settled_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,stream,outcome,realized_pnl,realized_by_exchange_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, key, "Match Winner", stamp, stamp, "SETTLED", 100.0, 1.0, "{}", "{}",
             "pre_match", "A", pnl, "{}"),
        )
        db.conn.commit()


def test_dashboard_trends_bucket_records_by_viewer_local_midnight(tmp_path):
    api = API(tmp_path / "local-midnight.sqlite3")
    tz = ZoneInfo("America/New_York")
    local_now = datetime.now(timezone.utc).astimezone(tz)
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    previous_late = local_today - timedelta(minutes=30)
    today_early = local_today + timedelta(minutes=30)

    _insert_settled_position(api, previous_late, "previous-local-day", 2.0)
    _insert_settled_position(api, today_early, "current-local-day", 1.0)

    result = api.dashboard_trends({
        "days": 7,
        "timezone_name": "America/New_York",
        "timezone_offset_minutes": int(local_now.utcoffset().total_seconds() / -60),
    })
    assert result["ok"] is True
    assert result["rows"][-1]["date"] == local_today.date().isoformat()
    assert result["rows"][-2]["date"] == (local_today - timedelta(days=1)).date().isoformat()
    assert result["rows"][-1]["sports"]["pnl"] == 1.0
    assert result["rows"][-2]["sports"]["pnl"] == 2.0


def test_dashboard_requests_timezone_aware_trends_and_refreshes_on_local_day_change():
    assert "timezone_offset_minutes:new Date().getTimezoneOffset()" in HTML
    assert "timezone_name:Intl.DateTimeFormat().resolvedOptions().timeZone||''" in HTML
    assert "function checkDashboardDayRollover()" in HTML
    assert "if(key===dashboardDayKey)return false" in HTML
    for refresh in (
        "loadDashboardTodayPipeline()",
        "loadDashboardTrends()",
        "loadDashboardPerformance()",
        "loadDashboardOverview()",
    ):
        assert refresh in HTML
