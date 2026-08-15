from pathlib import Path


def _html():
    return Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()


def test_dashboard_clock_and_theme_toggle_are_present():
    html = _html()
    assert 'class="analog-clocks"' in html
    assert 'id="dashClockLocalHour"' in html
    assert 'id="dashClockUtcHour"' in html
    assert 'id="dashClockNewYorkHour"' in html
    assert 'id="dashClockSydneyHour"' in html
    assert 'function setAnalogClock(' in html
    assert 'id="themeToggle"' in html
    assert 'function toggleTheme()' in html
    assert 'autobuys.theme' in html
    assert ':root[data-theme="dark"]' in html
    assert 'function startDashboardClock()' in html
    assert 'dashboardClockTimer=setInterval(renderDashboardClock,1000)' in html
    assert 'function renderDashboardClock()' in html


def test_today_activity_is_cumulative_and_calm():
    html = _html()
    assert 'Activity Monitor' in html
    assert 'Cumulative scanner activity since local midnight' in html
    assert 'function loadDashboardTodayPipeline()' in html
    assert 'localMidnight(new Date()).toISOString()' in html
    assert "loadDashboardTodayPipeline()},10000)" in html
    assert '@keyframes stagePulseCalm' in html
    assert '7.5s ease-in-out infinite' in html
    assert 'stageFlash' not in html
    assert 'flowMove' not in html
