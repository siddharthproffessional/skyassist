"""/occ — SkyAssist 3.0 Operations Control Centre (coordinators / OCC controllers only).

Every number on this page is read from the database. Every button calls the same Python service the API uses.
Synthetic demonstration data only.
"""
import json

import pandas as pd
import streamlit as st

from components import ui
from components.session import nav_link, require_login, sign_out
from db import local_time, query, query_one
from services.auth_service import COORDINATOR
from services.ops import (baggage, crew, gate_approvals, maintenance, outbox, rebooking, simulator)
from services.ops.foundation import OpsError, acknowledge_alert, open_alerts

ui.inject(ui.COORDINATOR_CSS + """
.occ-strip{display:flex;gap:10px;flex-wrap:wrap;margin:4px 0 12px}
.occ-chk{font-size:13.5px;padding:3px 0;border-bottom:1px solid #eef2f7}
.occ-chk b{display:inline-block;width:22px}
.occ-flag{font-size:12px;color:#b45309;font-weight:700}
""")
p = require_login(COORDINATOR, "Operations Control Centre", "OCC controllers only (synthetic demo accounts).",
                  ["`sarah` / `aisha`, password `coord123` (or `COORDINATOR_DEMO_PASSWORD`)"])
ss = st.session_state

with st.sidebar:
    st.markdown("### 🛫 SkyAssist OCC")
    st.caption(f"Signed in as **{p.display_name}** · OCC controller (demo account)")
    nav_link("views/occ.py", "Operations Control Centre", "🛫")
    nav_link("views/twin.py", "3D airport twin", "🌐")
    nav_link("views/coordinator.py", "Assistance requests", "🛟")
    nav_link("views/operations.py", "Operations overview", "📊")
    st.divider()
    st.caption(f"Workflow delivery: **{outbox.mode().replace('_', ' ').lower()}**")
    if st.button("Dispatch outbox now", use_container_width=True):
        r = outbox.dispatch()
        st.toast(f"{r['processed']} event(s) processed ({r['mode'].replace('_', ' ').lower()})")
    st.divider()
    if st.button("Sign out", use_container_width=True):
        sign_out(COORDINATOR)
        st.rerun()
    st.caption("Synthetic demonstration data. Demo sign-in, not production identity.")


def chk(ok):
    return "✅" if ok is True else ("❌" if ok is False else "❔")


def show_checks(checks, key_ok="passed"):
    rows = "".join(f'<div class="occ-chk"><b>{chk(c.get(key_ok, c.get("result")))}</b>{ui.esc(c["check"])} — '
                   f'<span class="sa-muted">{ui.esc(c["detail"])}</span></div>' for c in checks)
    ui.md(ui.card(rows))


def run_scenario(code, **kw):
    try:
        ss["occ_last"] = {"code": code, "result": simulator.run(p, code, **kw)}
        outbox.dispatch()
    except OpsError as e:
        st.error(str(e))


def kpis():
    q = lambda sql, a=(): (query_one(sql, a) or {}).get("n", 0)  # noqa: E731
    return [
        ("Open alerts", q("SELECT COUNT(*) n FROM operational_alerts WHERE status='OPEN'"), "needs attention"),
        ("Departures blocked", q("SELECT COUNT(DISTINCT flight_no) n FROM departure_holds WHERE status='ACTIVE'"),
         "active crew / maintenance holds"),
        ("Pending approvals", q("SELECT COUNT(*) n FROM approval_requests WHERE status='PENDING'") +
         q("SELECT COUNT(*) n FROM crew_assignment_proposals WHERE status='PENDING_APPROVAL'"), "gate + crew"),
        ("Passengers rebooked", q("SELECT COUNT(*) n FROM passenger_rebooking_history"), "committed only"),
        ("Rolled-back txns", q("SELECT COUNT(*) n FROM rebooking_transactions WHERE status='ROLLED_BACK'"),
         "isolated failures"),
        ("Audit events", q("SELECT COUNT(*) n FROM audit_events"), "append-only"),
    ]


st.markdown("## Operations Control Centre")
st.caption("SkyAssist 3.0 · deterministic Python decides; n8n orchestrates; humans approve. Synthetic data.")
ui.tiles(kpis())

tabs = st.tabs(["Live journey", "Burst & resilience", "Safety evidence", "Monitoring", "Overview & demos", "Rebooking", "Crew", "Maintenance", "Baggage", "Gates & approvals",
                "Audit & workflows"])

# ------------------------------------------------------------------ Round 3 workspaces
from views import occ_round3 as r3  # noqa: E402


with tabs[0]:
    r3.render_journey(p)
with tabs[1]:
    r3.render_burst(p)
with tabs[2]:
    r3.render_safety(p)
with tabs[3]:
    r3.render_monitor(p)

# ------------------------------------------------------------------ overview / scenario launcher
with tabs[4]:
    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Challenge scenarios")
        for code, desc in simulator.SCENARIOS.items():
            done = simulator.already_run(code)
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{code}** — {desc}" + ("  \n:green[✓ run on this dataset]" if done else ""))
            if c2.button("Run", key=f"run_{code}", disabled=done, use_container_width=True):
                run_scenario(code)
                st.rerun()
        st.divider()
        with st.expander("⚠️ Synthetic demo reset"):
            st.warning("Restores ONLY the synthetic operations demo dataset (SA-5xx/SA-7xx/EK-812 flights, OPS "
                       "passengers, crew, seats, bags). v2 passengers and accounts, the append-only audit trail and "
                       "stored maintenance sign-offs are never deleted.")
            if st.checkbox("I understand this is a synthetic demo reset", key="reset_ok"):
                if st.button("Reset synthetic demo data", type="primary"):
                    simulator.reset(p)
                    ss.pop("occ_last", None)
                    st.success("Synthetic demo dataset restored.")
                    st.rerun()
    with right:
        st.markdown("#### Open alerts")
        al = open_alerts()
        if not al:
            ui.md(ui.empty("No open alerts."))
        for a in al[:12]:
            tone = {"CRITICAL": "bad", "HIGH": "warn"}.get(a["severity"], "neutral")
            ui.md(ui.card(f'{ui.badge(a["severity"], tone)} {ui.badge(a["module"], "info")}'
                          f'<div style="font-weight:700;margin-top:6px">{ui.esc(a["title"])}</div>'
                          f'<div class="sa-muted">{ui.esc(a["detail"])}</div>'))
            if st.button("Acknowledge", key=f"ack_{a['alert_id']}"):
                acknowledge_alert(p, a["alert_id"])
                st.rerun()
    last = ss.get("occ_last")
    if last:
        st.markdown(f"#### Last run: {last['code']}")
        st.json(json.loads(json.dumps(last["result"], default=str)), expanded=False)

# ------------------------------------------------------------------ rebooking (U-4.1)
with tabs[5]:
    js = rebooking.jobs()
    if not js:
        ui.md(ui.empty("No rebooking jobs yet. Run U-4.1 from the Overview tab."))
    for j in js:
        rep = rebooking.report(j["job_id"])
        st.markdown(f"**{j['job_id']}** · {j['disrupted_flight']} → {j['target_flight']} · "
                    f"{ui.badge(j['status'].replace('_', ' '), 'warn' if rep['escalated'] else 'good')}",
                    unsafe_allow_html=True)
        st.caption(f"{rep['total']} passengers · {rep['vulnerable']} vulnerable (own priority transaction) · "
                   f"{rep['committed_passengers']} committed · {rep['rolled_back_transactions']} rolled-back attempt(s)")
        df = pd.DataFrame(rep["batches"])
        st.dataframe(df, hide_index=True, use_container_width=True)
        for b in rep["batches"]:
            if b["status"] == "ESCALATED":
                st.error(f"{b['batch_id']} escalated — {b['last_error']}")
                if st.button(f"Retry {b['batch_id']} with fresh availability", key=f"retry_{b['batch_id']}"):
                    rebooking.retry_escalated(p, b["batch_id"])
                    st.rerun()
        with st.expander("Job timeline (audit)"):
            ui.md(ui.timeline([{**e, "created_at": e["occurred_at"], "detail": e.get("reason") or e.get("result")} for e in
                               rebooking.timeline(j["job_id"])]))

# ------------------------------------------------------------------ crew (U-4.2)
with tabs[6]:
    flight = st.selectbox("Flight", ["SA-700", "SA-702"], key="crew_f")
    ds = maintenance.departure_status(flight)
    st.markdown(ui.badge(ds["label"], "bad" if ds["blocked"] else "good"), unsafe_allow_html=True)
    for c in crew.crew_board(flight):
        v = c["validation"]
        head = f"{c['crew_id']} {c['name']} · {c['role']} · {c['status']}"
        if v:
            with st.expander(f"{head} — {v['verdict']}", expanded=v["verdict"] != "LEGAL"):
                show_checks(v["checks"], "result")
        else:
            st.caption(head)
    st.markdown("#### Crew messages (stored as untrusted data)")
    for m in query("SELECT * FROM crew_messages WHERE flight_no=? ORDER BY msg_id DESC", (flight,)):
        flags = ", ".join(json.loads(m["flags"] or "[]")) or "none"
        ui.md(ui.card(f'<div class="sa-muted">{ui.esc(m["sender"])} · {ui.esc(local_time(m["received_at"]))}</div>'
                      f'<div>“{ui.esc(m["body"])}”</div><div class="occ-flag">flags: {ui.esc(flags)} — no effect on '
                      f'duty counters or legality</div>'))
    st.markdown("#### Reserve proposals")
    for pr in crew.proposals(flight):
        st.markdown(f"**{pr['proposal_id']}** · replace {pr['replace_crew_id']} with **{pr['candidate_crew_id']}** · "
                    f"{pr['status'].replace('_', ' ')} · projected remaining {pr['remaining_min']} min")
        if pr["status"] == "PENDING_APPROVAL":
            a1, a2 = st.columns(2)
            if a1.button("Approve (re-validated at execution)", key=f"cap_{pr['proposal_id']}"):
                try:
                    crew.decide_proposal(p, pr["proposal_id"], True, "Approved in OCC")
                except OpsError as e:
                    st.error(str(e))
                st.rerun()
            if a2.button("Reject", key=f"crj_{pr['proposal_id']}"):
                crew.decide_proposal(p, pr["proposal_id"], False, "Rejected in OCC")
                st.rerun()

# ------------------------------------------------------------------ maintenance (U-4.3)
with tabs[7]:
    for d in query("SELECT * FROM aircraft_defects ORDER BY opened_at_utc"):
        st.markdown(f"**{d['defect_id']}** · {d['reg']} · {d['defect_type']} · {d['severity']} · "
                    f"{ui.badge(d['status'], 'good' if d['status'] == 'CLEARED' else 'bad')}", unsafe_allow_html=True)
    st.markdown(ui.badge(maintenance.departure_status("SA-710")["label"],
                         "bad" if maintenance.departure_status("SA-710")["blocked"] else "good") + " SA-710",
                unsafe_allow_html=True)
    st.markdown("#### Sign-offs (immutable records)")
    for s in maintenance.signoffs():
        with st.expander(f"{s['signoff_id']} · {s['engineer_id']} · '{s['result']}' · {s['validation_status']}",
                         expanded=s["validation_status"] == "INVALID"):
            st.caption(f"Signed {s['signed_at_utc']} · evidence {s['evidence_ref']} · record hash "
                       f"{s['record_hash'][:16]}…")
            show_checks([{"check": e["check_name"], "passed": bool(e["passed"]), "detail": e["detail"]}
                         for e in maintenance.validation_events(s["signoff_id"])])
    with st.expander("Submit a sign-off for validation"):
        with st.form("so"):
            f = {k: st.text_input(k, v) for k, v in {"engineer_id": "E-501", "defect_id": "DEF-HYD-1",
                                                       "reg": "A6-SKB", "task_scope": "HYDRAULIC", "result": "OK",
                                                       "signed_at_utc": "2026-11-20T09:15:00+00:00",
                                                       "evidence_ref": "WO-7781"}.items()}
            if st.form_submit_button("Validate"):
                try:
                    r = maintenance.submit_signoff(p, **f)
                    (st.success if r["status"] == "VALID" else st.error)(f"{r['status']}: " + "; ".join(r["reasons"]))
                except OpsError as e:
                    st.error(str(e))

# ------------------------------------------------------------------ baggage (U-4.4)
with tabs[8]:
    c1, c2 = st.columns(2)
    br = c1.text_input("Booking", "PNRBAG")
    cf = c2.text_input("Candidate flight", "SA-592")
    try:
        plan = baggage.calculate_baggage_feasibility(br, cf)
        pax = plan["passenger"]
        ui.tiles([("Passenger", "CAN MAKE IT" if plan["passenger_feasible"] else "CANNOT", f"{pax.get('transfer_min')} min "
                   f"transfer vs {pax.get('minutes_to_boarding_close')} min to close"),
                  ("Bag connection", f"{plan['connection_min']} min", f"minimum {plan['min_bag_transfer_min']} min"),
                  ("Decision", plan["decision"].replace("_", " "), plan.get("bag_flight") or "—")])
        if plan.get("delivery_note"):
            st.info(f"Bags would travel on {plan['delivery_note']['carrying_flight']} — expected "
                    f"{plan['delivery_note']['expected_arrival']}.")
    except OpsError as e:
        st.error(str(e))
    view = baggage.passenger_baggage_view(br)
    if view:
        st.markdown("#### Committed baggage state")
        st.dataframe(pd.DataFrame(view["assignments"] or [{"status": "no assignment yet"}]), hide_index=True,
                     use_container_width=True)
        if view["delivery_note"]:
            st.success(f"Delivery note {view['delivery_note']['note_id']} issued with the rebooking transaction.")

# ------------------------------------------------------------------ gates & approvals (U-4.5)
with tabs[9]:
    for a in gate_approvals.approvals():
        snap = json.loads(a["snapshot"])
        tone = {"PENDING": "pending", "APPROVED": "good", "EXECUTED": "good", "STALE": "bad", "REJECTED": "bad",
                "EXPIRED": "bad"}.get(a["status"], "neutral")
        with st.expander(f"{a['approval_id']} · {a['flight_no']} → {snap['target_gate']} · {a['status']}",
                         expanded=a["status"] in ("PENDING", "APPROVED", "STALE")):
            st.markdown(ui.badge(a["status"], tone), unsafe_allow_html=True)
            st.caption(f"Reviewed versions {snap['versions']} · snapshot {a['snapshot_hash'][:16]}…"
                       + (f" · re-staged from {a['parent_id']}" if a["parent_id"] else ""))
            if a["stale_reason"]:
                st.error(a["stale_reason"])
            b1, b2, b3 = st.columns(3)
            if a["status"] == "PENDING":
                if b1.button("Approve this snapshot", key=f"gap_{a['approval_id']}"):
                    gate_approvals.approve(p, a["approval_id"], a["snapshot_hash"], "Approved in OCC")
                    st.rerun()
                if b2.button("Reject", key=f"grj_{a['approval_id']}"):
                    gate_approvals.reject(p, a["approval_id"], "Rejected in OCC")
                    st.rerun()
            if a["status"] == "APPROVED" and b3.button("Execute", key=f"gex_{a['approval_id']}"):
                r = gate_approvals.execute(p, a["approval_id"])
                (st.success if r["status"] == "EXECUTED" else st.error)(r.get("reason") or "Executed")
                outbox.dispatch()
                st.rerun()
    st.markdown("#### Gate board (concourse B)")
    st.dataframe(pd.DataFrame(gate_approvals.gate_board("B")), hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ audit & workflows
with tabs[10]:
    mod = st.selectbox("Module", ["ALL", "REBOOKING", "BAGGAGE", "CREW", "MAINTENANCE", "GATES", "API", "SIMULATOR"])
    rows = (query("SELECT * FROM audit_events ORDER BY event_id DESC LIMIT 200") if mod == "ALL" else
            query("SELECT * FROM audit_events WHERE module=? ORDER BY event_id DESC LIMIT 200", (mod,)))
    st.dataframe(pd.DataFrame(rows)[["event_id", "occurred_at", "actor", "module", "event_type", "entity_id", "result",
                                     "reason"]] if rows else pd.DataFrame(), hide_index=True, use_container_width=True)
    st.caption("Audit events are append-only: UPDATE and DELETE are blocked by database triggers.")
    st.markdown(f"#### Workflow outbox — {outbox.mode().replace('_', ' ').lower()}")
    st.caption("LOCAL SIMULATED = n8n is not configured in this deployment; events were not sent anywhere.")
    st.write(outbox.stats())
    st.dataframe(pd.DataFrame(outbox.recent()), hide_index=True, use_container_width=True)
