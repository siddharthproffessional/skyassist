"""OCC page smoke tests (Streamlit AppTest) — rendering, access control, and scenario launch from the UI."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _occ(principal=None, role="COORDINATOR"):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(ROOT, "views", "occ.py"), default_timeout=60)
    if principal:
        at.session_state["principals"] = {role: principal}
    return at.run()


def test_occ_requires_coordinator_sign_in(ops):
    at = _occ()
    assert not at.exception
    assert "Operations Control Centre" in " ".join(m.value for m in at.markdown)
    assert not at.tabs


def test_occ_renders_all_workspaces(ops):
    at = _occ(ops)
    assert not at.exception
    labels = [t.label for t in at.tabs]
    for t in ("Overview & demos", "Rebooking", "Crew", "Maintenance", "Baggage", "Gates & approvals",
              "Audit & workflows"):
        assert t in labels


def test_occ_scenario_buttons_run_real_services(ops):
    from db import query_one
    at = _occ(ops)
    for code in ("U-4.1", "U-4.2", "U-4.3", "U-4.4", "U-4.5"):
        at.button(key=f"run_{code}").click().run()
        assert not at.exception, code
    assert query_one("SELECT COUNT(*) n FROM passenger_rebooking_history")["n"] >= 31
    assert query_one("SELECT status FROM approval_requests WHERE flight_no='EK-812' ORDER BY requested_at "
                     "LIMIT 1")["status"] == "STALE"
    assert at.button(key="run_U-4.1").disabled


def test_passenger_sees_separate_baggage_after_u44(ops):
    from services.auth_service import authenticate
    from services.ops import simulator
    from streamlit.testing.v1 import AppTest
    simulator.run(ops, "U-4.4")
    p = authenticate("PNRBAG", "demo123", "PASSENGER")
    at = AppTest.from_file(os.path.join(ROOT, "views", "passenger.py"), default_timeout=60)
    at.session_state["principals"] = {"PASSENGER": p}
    at.run()
    assert not at.exception
    assert "Your bags will NOT travel with you" in " ".join(m.value for m in at.markdown)


def test_round3_tabs_render_and_send_alert(ops):
    at = _occ(ops)
    labels = [t.label for t in at.tabs]
    for t in ("Live journey", "Burst & resilience", "Safety evidence", "Monitoring"):
        assert t in labels
    at.button(key="r3_send").click().run()
    assert not at.exception
    from db import query_one
    assert query_one("SELECT status FROM journey_runs")["status"] == "COMPLETED"


def test_live_journey_survives_cleared_run(ops):
    at = _occ(ops)
    at.session_state["r3_last"] = {"run_id": "RUN-GONE", "duplicate": False}
    at.session_state["r3_burst"] = {"batch_id": "BURST-GONE", "results": [], "wall_ms": 0, "workers": 4, "ai_calls": 0}
    at.run()
    assert not at.exception


def test_twin_page_renders_from_live_data(ops):
    from services.ops import journey
    from streamlit.testing.v1 import AppTest
    journey.process_alert(ops, journey.SAMPLES["Cancellation (safe, automatic)"])
    at = AppTest.from_file(os.path.join(ROOT, "views", "twin.py"), default_timeout=60).run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "PAX REBOOKED<b class=\"g\">40" in html and "SA-560" in html
