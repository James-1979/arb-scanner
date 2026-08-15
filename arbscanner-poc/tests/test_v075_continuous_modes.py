from pathlib import Path

from arbscanner.api import API


class FakeService:
    def __init__(self):
        self.loaded = False

    def status(self):
        return {"loaded": self.loaded, "running": self.loaded}

    def install(self):
        self.loaded = True
        return {"ok": True}

    def uninstall(self):
        self.loaded = False
        return {"ok": True}


def test_continuous_scanner_and_monitor_alias_lifecycle(tmp_path: Path):
    api = API(tmp_path / "continuous.sqlite3")
    api.service = FakeService()
    state = api.ensure_scanner_running()["state"]
    assert state["settings"]["config"]["scanner_enabled"] is True
    assert state["background"]["loaded"] is True
    assert state["settings"]["mode"] == "sim"

    monitor_timing = api.activate_monitor_timing()["state"]
    assert monitor_timing["settings"]["mode"] == "sim"
    assert monitor_timing["automation"]["running"] is True
    assert monitor_timing["automation"]["status"] == "SIM ACTIVE"

    watch = api.stop_monitor_timing()["state"]
    assert watch["settings"]["mode"] == "sim"
    assert watch["settings"]["config"]["scanner_enabled"] is True
    assert watch["background"]["loaded"] is True
    assert watch["automation"]["status"] == "SIM ACTIVE"


def test_worker_scans_without_jobs():
    worker = (Path(__file__).parents[1] / "worker.py").read_text()
    assert "scanner.discover_once(job_id=None)" in worker
    assert "scanner.price_scan_once(job_id=None, force=False)" in worker
    assert "active_job()" not in worker
    assert "spawn_due_schedule" not in worker


def test_primary_ui_has_no_job_workflow_and_drawer_has_real_interactions():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert ">Jobs</span>" not in html
    assert ">Jobs</h1>" not in html
    assert 'data-opportunity-id="${Number(x.id)}"' in html
    assert "document.addEventListener('click'" in html
    assert "openOpportunityDrawer(opp.dataset.opportunityId)" in html
    assert "$('drawerCloseBtn')?.addEventListener('click',closeOpportunityDrawer)" in html
    assert "$('oppDrawerBackdrop')?.addEventListener('click',closeOpportunityDrawer)" in html
    assert "Escape" in html
