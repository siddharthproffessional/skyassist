"""Round 3 OCC workspaces: Live journey, Burst & resilience, Safety evidence, Monitoring.
Imported by views/occ.py. Every number is read from the database."""
import io
import json
import statistics

import pandas as pd
import streamlit as st

from components import ui
from db import query
from services.ai import gateway
from services.ops import burst, journey
from services.ops.foundation import OpsError

STATUS_TONE = {"COMPLETED": "good", "PAUSED_FOR_APPROVAL": "warn", "BLOCKED": "bad", "REJECTED": "bad",
               "REFUSED": "neutral", "DUPLICATE": "neutral"}
CASE_LABEL = {"SAFE_AUTO": "Safe action — done automatically", "RISKY_PAUSED": "Risky action — paused for approval",
              "ATTACK_BLOCKED": "Attack — blocked"}


def _step_rows(steps):
    rows = []
    for s in steps:
        d = s["detail"]
        try:
            d = json.loads(d) if d else ""
        except Exception:
            pass
        if isinstance(d, dict) and "trace" in d:
            d = " · ".join(f"{t['provider']}: {t['outcome']}" + (f" ({t['ms']} ms)" if t.get("ms") else "")
                           for t in d["trace"])
        rows.append({"step": s["step"], "status": s["status"], "ms": s["ms"],
                     "detail": d if isinstance(d, str) else json.dumps(d, default=str)[:300]})
    return rows


def show_run(r):
    st.markdown(f"{ui.badge(r['status'].replace('_', ' '), STATUS_TONE.get(r['status'], 'neutral'))} "
                + (ui.badge(CASE_LABEL.get(r['safety_case'], ''), 'info') if r.get('safety_case') else "")
                + (ui.badge("duplicate — same run returned", "neutral") if r.get("duplicate") else ""),
                unsafe_allow_html=True)
    st.caption(f"{r['run_id']} · {r['alert_type'] or 'not classified'} · AI: {r['ai_provider'] or 'not called'} · "
               f"{r['latency_ms']} ms")
    ui.md(ui.card(f'<div class="sa-label">Output for the controller ({ui.esc(r["summary_source"] or "")})</div>'
                  f'<div style="font-size:16px;margin-top:4px">{ui.esc(r["summary"] or "")}</div>'))
    st.dataframe(pd.DataFrame(_step_rows(r["steps"])), hide_index=True, use_container_width=True)


def render_journey(p):
    st.markdown("#### One alert, end to end")
    st.caption("Trigger → input checks → injection screen → AI intake (Gemini → Groq → rules) → validation in code → "
               "specialist agent → safe action automatically / risky action paused → output. Every step is logged.")
    c1, c2 = st.columns([2, 1])
    sample = c2.selectbox("Sample alert", list(journey.SAMPLES), key="r3_sample")
    up = c2.file_uploader("…or upload an alert (.txt / .json)", type=["txt", "json"], key="r3_up")
    default = journey.SAMPLES[sample]
    if up is not None:
        raw = up.getvalue()[:8000].decode("utf-8", errors="replace")
        try:
            obj = json.loads(raw)
            default = obj.get("text") or obj.get("alert") or raw if isinstance(obj, dict) else raw
        except Exception:
            default = raw
    text = c1.text_area("Alert text", value=default, height=120, key=f"r3_text_{sample}_{up.name if up else ''}")
    if c1.button("Send alert to SkyAssist", type="primary", key="r3_send"):
        with st.spinner("Processing… (if an AI provider is slow, SkyAssist retries once, then switches provider)"):
            try:
                st.session_state["r3_last"] = journey.process_alert(p, text, source="UPLOAD" if up else "OCC")
            except OpsError as e:
                st.error(str(e))
    r = st.session_state.get("r3_last")
    full = journey.get_run(r["run_id"]) if r else None
    if r and full is None:          # run cleared by a demo reset or an app restart
        st.session_state.pop("r3_last", None)
    if full:
        show_run(full | {"duplicate": r.get("duplicate")})
    st.markdown("#### Paused for approval (human in the loop)")
    st.caption("Safe default: if nobody approves within the time limit, the action expires and nothing changes.")
    pend = journey.pending_approvals()
    if not pend:
        ui.md(ui.empty("Nothing waiting."))
    for a in pend:
        ui.md(ui.card(f'{ui.badge("PENDING", "warn")} <b>{ui.esc(a["action"].replace("_", " ").lower())}</b> · '
                      f'{ui.esc(a["booking_ref"])} → {ui.esc(a["flight_no"])}<div class="sa-muted">'
                      f'{ui.esc(a["risk_reason"])} · expires {ui.esc(a["expires_at"][11:16])} UTC</div>'))
        b1, b2 = st.columns(2)
        if b1.button("Approve (re-checked at execution)", key=f"jap_ok_{a['approval_id']}"):
            out = journey.decide(p, a["approval_id"], True, "Approved in OCC")
            (st.success if out["status"] == "APPROVED_EXECUTED" else st.warning)(out["status"])
            st.rerun()
        if b2.button("Reject", key=f"jap_no_{a['approval_id']}"):
            journey.decide(p, a["approval_id"], False, "Rejected in OCC")
            st.rerun()
    st.caption("Gate moves and crew replacements from alerts wait in the Gates & approvals and Crew tabs.")


def render_burst(p):
    st.markdown("#### Burst test: 12 mixed alerts at once")
    st.caption("4 workers in parallel, a per-alert deadline, AI retries and provider fallback, idempotent duplicates. "
               "Use the switches to make a provider fail or slow down; the switches are labelled SIMULATED in the "
               "logs.")
    chain = gateway.configured_chain()
    st.write("AI provider chain: **" + " → ".join(chain + ["rules"]) + "**"
             + ("" if chain else " (no API keys configured, so the rules extractor answers)"))
    sim = gateway.simulation_state()
    c = st.columns(4)
    g_out = c[0].toggle("Simulate Gemini outage", value="gemini" in sim["outage"], key="sim_g")
    q_slow = c[1].toggle("Simulate slow Groq", value="groq" in sim["slow"], key="sim_q")
    gateway.set_simulation("gemini", outage=g_out)
    gateway.set_simulation("groq", slow=q_slow)
    if c[2].button("Reset circuit breakers"):
        gateway.reset_breakers()
    fresh = c[3].checkbox("Treat as new alerts", value=True, help="Tags this burst so it is not a duplicate of an "
                                                                    "earlier burst")
    if st.button("Run burst of 12 alerts", type="primary"):
        with st.spinner("Running 12 alerts on 4 workers…"):
            st.session_state["r3_burst"] = burst.run_burst(p, fresh=fresh)
    b = st.session_state.get("r3_burst")
    if b and not query("SELECT 1 x FROM journey_runs WHERE batch_id=? LIMIT 1", (b["batch_id"],)):
        b = None                    # burst cleared by a demo reset
    if b:
        df = pd.DataFrame(b["results"])
        ok = df["status"].str.contains("COMPLETED|PAUSED|BLOCKED|REJECTED|DUPLICATE|REFUSED").sum()
        ui.tiles([("Alerts", len(df), f"{b['workers']} workers"), ("Wall time", f"{b['wall_ms']} ms", "all 12"),
                  ("Handled without crash", f"{ok}/{len(df)}", "every alert has a status"),
                  ("AI calls", b["ai_calls"], "incl. retries / fallbacks")])
        st.dataframe(df, hide_index=True, use_container_width=True)
        calls = query("SELECT provider, attempt, outcome, error, latency_ms FROM llm_calls WHERE run_id IN "
                      "(SELECT run_id FROM journey_runs WHERE batch_id=?) ORDER BY id", (b["batch_id"],))
        if calls:
            st.markdown("**AI gateway attempts in this burst**")
            st.dataframe(pd.DataFrame(calls), hide_index=True, use_container_width=True)
    br = gateway.breaker_state()
    if br:
        st.caption("Circuit breakers: " + "; ".join(f"{k}: {'OPEN for ' + str(v['reopens_in_s']) + ' s' if v['open'] else 'closed'}"
                                                 f" ({v['consecutive_failures']} consecutive failures)"
                                                 for k, v in br.items()))


def render_safety(p):
    st.markdown("#### The three safety cases, with their log entries")
    st.caption("Checks run in code, not in the LLM. Audit events are append-only (database triggers block edits).")
    ev = journey.safety_evidence()
    export = {}
    for case, title in CASE_LABEL.items():
        r = ev.get(case)
        st.markdown(f"**{title}**")
        if not r:
            ui.md(ui.empty("Not run yet — send a matching sample alert in Live journey."))
            continue
        st.caption(f"Alert: “{r['alert_text'][:160]}”")
        st.write(r["summary"])
        steps = pd.DataFrame(_step_rows(r["steps"]))
        st.dataframe(steps[["step", "status", "detail"]], hide_index=True, use_container_width=True)
        st.caption("Audit log entries (append-only):")
        st.dataframe(pd.DataFrame(r["audit"]), hide_index=True, use_container_width=True)
        export[case] = {"run_id": r["run_id"], "alert": r["alert_text"], "status": r["status"],
                        "summary": r["summary"], "steps": _step_rows(r["steps"]), "audit_events": r["audit"]}
    if export:
        st.download_button("Download log evidence (JSON)", json.dumps(export, indent=2, default=str),
                           file_name="skyassist_round3_safety_log_evidence.json", mime="application/json")
        flat = [{"case": c, **a} for c, v in export.items() for a in v["audit_events"]]
        buf = io.StringIO()
        pd.DataFrame(flat).to_csv(buf, index=False)
        st.download_button("Download log evidence (CSV)", buf.getvalue(),
                           file_name="skyassist_round3_safety_log_evidence.csv", mime="text/csv")


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, round(q * (len(xs) - 1))))
    return round(xs[k])


def render_monitor(p):
    from services.ai import pricing
    st.markdown("#### Monitoring")
    runs = query("SELECT status, latency_ms, ai_provider FROM journey_runs")
    lat = [r["latency_ms"] for r in runs]
    calls = query("SELECT * FROM llm_calls")
    okc = [c for c in calls if c["outcome"] == "OK"]
    fb = sum(1 for c in calls if c["outcome"] == "FALLBACK")
    tin, tout = sum(c["tokens_in"] or 0 for c in okc), sum(c["tokens_out"] or 0 for c in okc)
    cost = sum(pricing.cost_usd(c["provider"], c["tokens_in"] or 0, c["tokens_out"] or 0) for c in okc)
    ui.tiles([("Alerts processed", len(runs), "all sources"),
              ("Latency p50 / p95", f"{_pct(lat, .5) or '—'} / {_pct(lat, .95) or '—'} ms", "end to end, per alert"),
              ("AI calls OK", f"{len(okc)}/{len(calls)}", f"{fb} fell back to rules"),
              ("Tokens in / out", f"{tin:,} / {tout:,}", f"≈ ${cost:.4f} total")])
    if runs:
        st.dataframe(pd.DataFrame(runs).groupby("status").size().rename("alerts").reset_index(), hide_index=True)
    if calls:
        df = pd.DataFrame(calls)
        g = df.groupby(["provider", "outcome"]).agg(calls=("id", "count"), median_ms=("latency_ms", "median"),
                                                     tokens_in=("tokens_in", "sum"), tokens_out=("tokens_out", "sum"))
        st.dataframe(g.reset_index(), hide_index=True, use_container_width=True)
    st.caption("Health endpoint for uptime monitoring: GET /health on the workflow API. Prices: "
               + pricing.SOURCE_NOTE)
