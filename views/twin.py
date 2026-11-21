"""/twin — SkyAssist 3.0 3D airport twin, drawn live from THIS app's database (no external site).

Same data the OCC agents write: flight status, gate changes, departure holds, paused approvals, rebooked passengers.
Read-only view; synthetic demonstration data.
"""
import json
import os
from collections import Counter

import streamlit as st
import streamlit.components.v1 as components

from db import query, query_one

st.markdown("""<style>
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
[data-testid="stHeader"] { background: transparent; height: 0; }
.stApp { background: radial-gradient(1200px 600px at 70% -10%, #0f2244 0%, #060b16 55%); }
.block-container { padding: .6rem 1.1rem 3.6rem 1.1rem; max-width: 100%; }
.stApp p, .stApp span, .stApp div { color: #e2e8f0; }
.tw-bar { display:flex; align-items:center; gap:14px; font-family: Inter, system-ui, sans-serif; margin-bottom:10px;
          background:#0c1424; border:1px solid #1d2b45; border-radius:14px; padding:10px 16px; }
.tw-logo { width:30px; height:30px; border-radius:9px; background:linear-gradient(135deg,#38bdf8,#a78bfa); display:grid;
           place-items:center; color:#060b16; font-weight:900; }
.tw-bar b { font-size:17px; color:#fff; } .tw-bar .s { color:#8a9bba; font-size:13px; }
.tw-chip { font-family: ui-monospace, Menlo, monospace; font-size:11px; font-weight:700; letter-spacing:.08em; padding:4px 10px;
           border-radius:7px; border:1px solid rgba(245,158,11,.5); color:#f59e0b !important; background:rgba(245,158,11,.08); }
.tw-bar a { color:#38bdf8 !important; font-size:13px; text-decoration:none; }
.tw-panel { background:#0c1424; border:1px solid #1d2b45; border-radius:14px; padding:12px 14px; margin-bottom:10px; }
.tw-h { color:#38bdf8 !important; font-size:11px; letter-spacing:.14em; text-transform:uppercase; font-weight:700; margin:0 0 8px; }
.tw-q { color:#38bdf8 !important; font-size:10.5px; letter-spacing:.12em; text-transform:uppercase; font-weight:700; margin-top:9px; }
.tw-a { font-size:13px; margin-top:2px; } .mono { font-family: ui-monospace, Menlo, monospace; color:#fff !important; }
.tw-f { display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-bottom:1px solid #142039; font-size:12.5px; }
.tw-f .d { color:#8a9bba !important; font-size:11.5px; }
.chip { font-family: ui-monospace, Menlo, monospace; font-size:10px; font-weight:700; padding:2px 7px; border-radius:6px; border:1px solid; }
.chip.r { color:#f87171 !important; border-color:rgba(239,68,68,.5); } .chip.a { color:#f59e0b !important; border-color:rgba(245,158,11,.5); }
.chip.g { color:#22c55e !important; border-color:rgba(34,197,94,.45); } .chip.b { color:#38bdf8 !important; border-color:rgba(56,189,248,.45); }
.tw-sb { position:fixed; left:0; right:0; bottom:0; z-index:999; background:rgba(6,11,22,.96); border-top:1px solid #1d2b45;
         display:flex; gap:22px; padding:8px 22px; font-family: ui-monospace, Menlo, monospace; font-size:11.5px; white-space:nowrap; overflow-x:auto; }
.tw-sb span { color:#8a9bba !important; letter-spacing:.1em; } .tw-sb b { margin-left:6px; color:#fff !important; }
.tw-sb b.r { color:#f87171 !important; } .tw-sb b.a { color:#f59e0b !important; } .tw-sb b.g { color:#22c55e !important; }
.stButton > button { background:#12203a; color:#e2e8f0; border:1px solid #27406a; border-radius:10px; }
</style>""", unsafe_allow_html=True)


def esc(x):
    import html
    return html.escape(str(x)) if x is not None else "—"


def load():
    gates = {r["gate_id"]: {"x": r["x"], "y": r["y"], "c": r["concourse"]} for r in query(
        "SELECT g.gate_id, g.concourse, n.x, n.y FROM gate_locations g JOIN terminal_nodes n ON n.node_id=g.node_id")}
    hubs = [dict(r) for r in query("SELECT node_id, x, y, concourse FROM terminal_nodes WHERE node_type='HUB'")]
    holds = {r["flight_no"]: r["hold_type"] for r in query("SELECT flight_no, hold_type FROM departure_holds "
                                                          "WHERE status='ACTIVE'")}
    pending = {r["flight_no"] for r in query("SELECT flight_no FROM approval_requests WHERE status='PENDING'")}
    pending |= {r["flight_no"] for r in query("SELECT flight_no FROM journey_approvals WHERE status='PENDING'")}
    pending |= {r["flight_no"] for r in query("SELECT flight_no FROM crew_assignment_proposals "
                                              "WHERE status='PENDING_APPROVAL'")}
    pax = Counter(r["outbound"] for r in query("SELECT outbound FROM passengers"))
    flights = []
    for f in query("SELECT flight_no, direction, status, gate, previous_gate, destination, origin FROM flights"):
        state = ("CANCELLED" if f["status"] == "CANCELLED" else "HELD" if f["flight_no"] in holds else
                 "PENDING" if f["flight_no"] in pending else "CHANGED" if f["status"] == "GATE_CHANGED" else
                 "DELAYED" if f["status"] == "DELAYED" else "ARRIVED" if f["direction"] == "ARRIVAL" else "OK")
        if f["gate"] in gates:
            flights.append({"no": f["flight_no"], "gate": f["gate"], "state": state, "status": f["status"],
                            "dir": f["direction"], "to": f["destination"] or f["origin"], "pax": pax.get(f["flight_no"], 0),
                            "hold": holds.get(f["flight_no"]), "prev": f["previous_gate"]})
    gate_of = {f["no"]: f["gate"] for f in flights}
    arcs = [{"from": gate_of[r["from_flight"]], "to": gate_of[r["to_flight"]], "n": r["n"]}
            for r in query("SELECT from_flight, to_flight, COUNT(*) n FROM passenger_rebooking_history "
                           "GROUP BY from_flight, to_flight")
            if r["from_flight"] in gate_of and r["to_flight"] in gate_of]
    return {"gates": gates, "hubs": hubs, "flights": flights, "arcs": arcs}


def n(sql, a=()):
    return (query_one(sql, a) or {}).get("n", 0)


data = load()
st.markdown('<div class="tw-bar"><div class="tw-logo">✈</div><b>SkyAssist · 3D airport twin</b>'
            '<span class="s">Live from the same database the OCC agents write to</span>'
            '<span class="tw-chip">SYNTHETIC DEMO DATA</span><span style="flex:1"></span>'
            '<a href="/occ" target="_self">Open the OCC →</a></div>', unsafe_allow_html=True)

left, right = st.columns([3.2, 1.25])
with right:
    if st.button("↻ Refresh from database", use_container_width=True):
        st.rerun()
    run = query_one("SELECT * FROM journey_runs ORDER BY created_at DESC LIMIT 1")
    if run:
        o = json.loads(run["outcome"]) if run["outcome"] else {}
        appr = query("SELECT approval_id FROM journey_approvals WHERE run_id=? AND status='PENDING'", (run["run_id"],))
        who = (f'{o.get("committed", 0)} rebooked to <span class="mono">{esc(o.get("target"))}</span>'
               if run["alert_type"] == "FLIGHT_CANCELLED" else
               ", ".join(f'<span class="mono">{esc(k)}</span> {esc(v)}' for k, v in (o.get("verdicts") or {}).items())
               or "No passengers changed")
        rows = [("1 What happened?", esc((run["alert_text"] or "")[:120])),
                ("2 Who is affected?", who),
                ("3 What is the AI doing?", "Stopped in code before any AI call" if run["status"] in ("BLOCKED", "REJECTED")
                 else f'Read the alert with <span class="mono">{esc(run["ai_provider"])}</span>; code checked every field'),
                ("4 Is approval required?", "Yes — " + ", ".join(f'<span class="mono">{esc(a["approval_id"])}</span>'
                                                                for a in appr) if appr else
                 ("Yes — waiting for a controller" if run["status"] == "PAUSED_FOR_APPROVAL" else "No")),
                ("5 What happened next?", f'<span class="mono">{esc(run["status"])}</span> · {esc(run["run_id"])}')]
        st.markdown('<div class="tw-panel"><div class="tw-h">Operational picture · last alert</div>'
                    + "".join(f'<div class="tw-q">{q}</div><div class="tw-a">{a}</div>' for q, a in rows) + "</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="tw-panel"><div class="tw-h">Operational picture</div><div class="tw-a">No alert yet. '
                    'Send one from the OCC Live journey tab and refresh.</div></div>', unsafe_allow_html=True)
    tone = {"CANCELLED": ("r", "CANCELLED"), "HELD": ("r", "HELD"), "PENDING": ("a", "APPROVAL"),
            "CHANGED": ("a", "GATE CHANGED"), "DELAYED": ("a", "DELAYED")}
    att = [f for f in data["flights"] if f["state"] in tone]
    att.sort(key=lambda f: ["CANCELLED", "HELD", "PENDING", "CHANGED", "DELAYED"].index(f["state"]))
    items = "".join(f'<div class="tw-f"><div><span class="mono">{esc(f["no"])}</span> → {esc(f["to"])}<div class="d">Gate '
                    f'{esc(f["gate"])} · {f["pax"]} pax{" · " + esc(f["hold"]) + " hold" if f["hold"] else ""}</div></div>'
                    f'<span class="chip {tone[f["state"]][0]}">{tone[f["state"]][1]}</span></div>' for f in att[:9])
    st.markdown(f'<div class="tw-panel"><div class="tw-h">Flights needing attention · {len(att)}</div>{items}</div>',
                unsafe_allow_html=True)

with left:
    components.html("""
<div id="wrap" style="position:relative;width:100%;height:660px;border-radius:14px;overflow:hidden;
     border:1px solid #1d2b45;background:#070d1a">
  <div id="legend" style="position:absolute;left:12px;bottom:10px;z-index:5;font:12px Inter,system-ui,sans-serif;color:#cbd5e1;
       background:rgba(7,13,26,.82);border:1px solid #1d2b45;border-radius:10px;padding:7px 10px;display:flex;gap:12px;flex-wrap:wrap">
    <span><b style="color:#22c55e">●</b> On time</span><span><b style="color:#94a3b8">●</b> Arrived</span>
    <span><b style="color:#f59e0b">●</b> Gate change / delay / approval pending</span>
    <span><b style="color:#ef4444">●</b> Cancelled / departure held</span><span><b style="color:#38bdf8">⌒</b> Rebooked passengers</span>
  </div>
  <div id="err" style="display:none;position:absolute;inset:0;color:#cbd5e1;font:14px Inter,sans-serif;padding:30px"></div>
</div>
<script type="importmap">{"imports":{"three":"__THREE__/build/three.module.js",
 "three/addons/":"__THREE__/examples/jsm/"}}</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
const D = __DATA__;
const wrap = document.getElementById("wrap");
try {
const W = wrap.clientWidth, H = wrap.clientHeight;
const renderer = new THREE.WebGLRenderer({antialias:true}); renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.setSize(W,H); renderer.shadowMap.enabled = true; wrap.appendChild(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x070d1a); scene.fog = new THREE.Fog(0x070d1a, 900, 1900);
const cam = new THREE.PerspectiveCamera(42, W/H, 1, 4000); cam.position.set(480, 560, 900);
const ctl = new OrbitControls(cam, renderer.domElement); ctl.target.set(480, 0, 330); ctl.enableDamping = true;
ctl.autoRotate = true; ctl.autoRotateSpeed = 0.35; ctl.maxPolarAngle = 1.35;
renderer.domElement.addEventListener("pointerdown", () => ctl.autoRotate = false);
scene.add(new THREE.HemisphereLight(0x9fc4ff, 0x0b1222, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(300, 700, 200); sun.castShadow = true; scene.add(sun);
// apron and grid
const apron = new THREE.Mesh(new THREE.PlaneGeometry(1500, 1100), new THREE.MeshStandardMaterial({color:0x0e1729, roughness:.95}));
apron.rotation.x = -Math.PI/2; apron.position.set(480, -0.5, 330); apron.receiveShadow = true; scene.add(apron);
const grid = new THREE.GridHelper(1500, 50, 0x1b2a46, 0x13203a); grid.position.set(480, 0, 330); scene.add(grid);
const mat = (c, e=0) => new THREE.MeshStandardMaterial({color:c, emissive:c, emissiveIntensity:e, roughness:.6, metalness:.15});
const COL = {OK:0x22c55e, ARRIVED:0x94a3b8, CHANGED:0xf59e0b, DELAYED:0xf59e0b, PENDING:0xf59e0b, CANCELLED:0xef4444, HELD:0xef4444};
// concourses (piers) from gate coordinates
const byC = {}; for (const [g,v] of Object.entries(D.gates)) (byC[v.c] ||= []).push(v);
for (const [c, gs] of Object.entries(byC)) {
  const x = gs.reduce((a,v)=>a+v.x,0)/gs.length, y0 = Math.min(...gs.map(v=>v.y)), y1 = Math.max(...gs.map(v=>v.y));
  const pier = new THREE.Mesh(new THREE.BoxGeometry(46, 22, y1-y0+60), mat(0x24365a, .08));
  pier.position.set(x, 11, (y0+y1)/2); pier.castShadow = true; scene.add(pier);
  const roof = new THREE.Mesh(new THREE.BoxGeometry(50, 3, y1-y0+64), mat(0x3b5b8f, .15)); roof.position.set(x, 23.5, (y0+y1)/2); scene.add(roof);
  scene.add(label("CONCOURSE " + c, x, 60, y0-55, "#9fb6da", 26));
}
// main terminal + skywalk
const hx = D.hubs.map(h=>h.x), hy = Math.max(...D.hubs.map(h=>h.y));
const term = new THREE.Mesh(new THREE.BoxGeometry(Math.max(...hx)-Math.min(...hx)+140, 30, 70), mat(0x1f3358, .1));
term.position.set((Math.max(...hx)+Math.min(...hx))/2, 15, hy+40); scene.add(term);
const sky = new THREE.Mesh(new THREE.BoxGeometry(Math.max(...hx)-Math.min(...hx), 6, 10), mat(0x38bdf8, .5));
sky.position.set((Math.max(...hx)+Math.min(...hx))/2, 40, hy+10); scene.add(sky);
// gates: small pads; aircraft at occupied gates
const gpos = g => { const v = D.gates[g]; const side = (parseInt(g.slice(1))%2) ? -1 : 1; return {x: v.x + side*70, z: v.y, side}; };
for (const g of Object.keys(D.gates)) { const p = gpos(g);
  const pad = new THREE.Mesh(new THREE.BoxGeometry(18, 1, 10), mat(0x1d2b45)); pad.position.set(p.x - p.side*40, 1, p.z); scene.add(pad); }
const planes = [];
for (const f of D.flights) { const p = gpos(f.gate), c = COL[f.state] ?? 0x22c55e;
  const ac = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(4.2, 3.2, 60, 14), mat(0xe8eef8, .05)); body.rotation.x = Math.PI/2; ac.add(body);
  const wing = new THREE.Mesh(new THREE.BoxGeometry(56, 1.6, 11), mat(0xcbd5e1)); wing.position.z = 2; ac.add(wing);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(1.6, 12, 8), mat(c, .6)); tail.position.set(0, 7, 26); ac.add(tail);
  const stab = new THREE.Mesh(new THREE.BoxGeometry(20, 1.2, 6), mat(0xcbd5e1)); stab.position.set(0, 2, 27); ac.add(stab);
  ac.rotation.y = p.side > 0 ? Math.PI/2 : -Math.PI/2; ac.position.set(p.x, 6, p.z); ac.traverse(o=>o.castShadow=true); scene.add(ac);
  const ring = new THREE.Mesh(new THREE.RingGeometry(34, 38, 40), new THREE.MeshBasicMaterial({color:c, transparent:true, opacity:.55, side:THREE.DoubleSide}));
  ring.rotation.x = -Math.PI/2; ring.position.set(p.x, 1.2, p.z); scene.add(ring);
  planes.push({ring, alert: ["CANCELLED","HELD","PENDING","CHANGED","DELAYED"].includes(f.state)});
  const hot = !(f.state==="OK"||f.state==="ARRIVED");
  const txt = hot ? f.no + " → " + f.to + "  " + (f.state==="HELD" ? (f.hold||"") + " HOLD" : f.state==="PENDING" ? "APPROVAL" : f.state==="CHANGED" ? "GATE CHANGED" : f.state) : f.no;
  scene.add(label(txt, p.x, hot ? 58 : 34, p.z, "#" + c.toString(16).padStart(6,"0"), hot ? 13 : 8, hot));
}
// rebooking arcs
const arcs = [];
for (const a of D.arcs) { const A = gpos(a.from), B = gpos(a.to);
  const mid = new THREE.Vector3((A.x+B.x)/2, 90 + Math.hypot(A.x-B.x, A.z-B.z)*0.25, (A.z+B.z)/2);
  const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(A.x, 8, A.z), mid, new THREE.Vector3(B.x, 8, B.z));
  const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 60, 1.4 + Math.min(a.n, 40)/20, 8),
                              new THREE.MeshBasicMaterial({color:0x38bdf8, transparent:true, opacity:.75}));
  scene.add(tube); const dot = new THREE.Mesh(new THREE.SphereGeometry(4, 12, 12), new THREE.MeshBasicMaterial({color:0xffffff}));
  scene.add(dot); arcs.push({curve, dot, t: Math.random()});
  scene.add(label(a.n + " rebooked", mid.x, mid.y + 12, mid.z, "#38bdf8", 15, true));
}
function label(text, x, y, z, color, size, box) {
  const cv = document.createElement("canvas"), f = size*4, font = `700 ${f}px Inter, system-ui, sans-serif`;
  let c2 = cv.getContext("2d"); c2.font = font; const w = Math.ceil(c2.measureText(text).width + f);
  cv.width = w; cv.height = Math.ceil(f*1.7); c2 = cv.getContext("2d"); c2.font = font;   // resizing resets the context
  if (box) { c2.fillStyle = "rgba(7,13,26,.85)"; c2.strokeStyle = color; c2.lineWidth = 6; roundRect(c2, 3, 3, w-6, f*1.7-6, 18); c2.fill(); c2.stroke(); }
  c2.fillStyle = box ? "#ffffff" : color; c2.textBaseline = "middle"; c2.fillText(text, f/2, f*0.87);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map: new THREE.CanvasTexture(cv), depthWrite:false}));
  sp.scale.set(w/4, f*1.7/4, 1); sp.position.set(x, y, z); return sp;
}
function roundRect(c, x, y, w, h, r) { c.beginPath(); c.moveTo(x+r,y); c.arcTo(x+w,y,x+w,y+h,r); c.arcTo(x+w,y+h,x,y+h,r); c.arcTo(x,y+h,x,y,r); c.arcTo(x,y,x+w,y,r); c.closePath(); }
let t0 = performance.now();
renderer.setAnimationLoop(() => { const t = (performance.now()-t0)/1000;
  for (const p of planes) if (p.alert) p.ring.material.opacity = .35 + .35*Math.abs(Math.sin(t*2.2));
  for (const a of arcs) { a.t = (a.t + 0.004) % 1; a.dot.position.copy(a.curve.getPoint(a.t)); }
  ctl.update(); renderer.render(scene, cam); });
addEventListener("resize", () => { const w = wrap.clientWidth; renderer.setSize(w, H); cam.aspect = w/H; cam.updateProjectionMatrix(); });
} catch (e) { const el = document.getElementById("err"); el.style.display = "block"; el.textContent = "3D view could not start: " + e; }
</script>""".replace("__DATA__", json.dumps(data)).replace(
    "__THREE__", os.getenv("SKYASSIST_THREE_BASE", "https://cdn.jsdelivr.net/npm/three@0.160.0")), height=672)

bar = [("FLIGHTS", len(data["flights"]), ""),
       ("CANCELLED", sum(f["state"] == "CANCELLED" for f in data["flights"]), "r"),
       ("DEPARTURES HELD", sum(f["state"] == "HELD" for f in data["flights"]), "r"),
       ("AWAITING APPROVAL", n("SELECT COUNT(*) n FROM approval_requests WHERE status='PENDING'")
        + n("SELECT COUNT(*) n FROM journey_approvals WHERE status='PENDING'")
        + n("SELECT COUNT(*) n FROM crew_assignment_proposals WHERE status='PENDING_APPROVAL'"), "a"),
       ("PAX REBOOKED", n("SELECT COUNT(*) n FROM passenger_rebooking_history"), "g"),
       ("ATTACKS BLOCKED", n("SELECT COUNT(*) n FROM journey_runs WHERE status='BLOCKED'"), "g"),
       ("AUDIT EVENTS", n("SELECT COUNT(*) n FROM audit_events"), "")]
st.markdown('<div class="tw-sb">' + "".join(f'<span>{l}<b class="{c}">{v}</b></span>' for l, v, c in bar) + "</div>",
            unsafe_allow_html=True)
