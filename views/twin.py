"""/twin — the SkyAssist 4.0 3D airport digital twin, full width. Public page, no sign-in needed."""
import streamlit as st
import streamlit.components.v1 as components

TWIN_URL = "https://skyassist-4.vercel.app/occ/"
st.markdown("""<style>
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
[data-testid="stHeader"] { background: transparent; height: 0; }
.stApp { background:#060b16; } .block-container { padding: .6rem 1rem 0 1rem; max-width: 100%; }
.tw-bar { display:flex; align-items:center; gap:14px; color:#e2e8f0; font-family: Inter, system-ui, sans-serif; margin-bottom:8px; }
.tw-bar b { font-size:17px; } .tw-bar span { color:#8a9bba; font-size:13px; }
.tw-chip { font-family: ui-monospace, monospace; font-size:11px; font-weight:700; letter-spacing:.08em; padding:4px 10px;
           border-radius:7px; border:1px solid rgba(245,158,11,.5); color:#f59e0b; background:rgba(245,158,11,.08); }
.tw-bar a { margin-left:auto; color:#38bdf8 !important; font-size:13px; text-decoration:none; }
</style>""", unsafe_allow_html=True)
st.markdown(f'<div class="tw-bar"><b>SkyAssist · 3D airport twin</b><span>The OCC agents on a live 3D airport</span>'
            f'<span class="tw-chip">SYNTHETIC DEMO DATA</span><a href="/occ" target="_self">← Back to the OCC</a>'
            f'<a href="{TWIN_URL}" target="_blank" style="margin-left:18px">Open full screen ↗</a></div>',
            unsafe_allow_html=True)
components.iframe(TWIN_URL, height=820, scrolling=False)
st.markdown('<div style="color:#8a9bba;font-size:12.5px;margin-top:6px">The twin runs on its own synthetic dataset; agent '
            'decisions, approvals and audit logs are in the OCC.</div>', unsafe_allow_html=True)
