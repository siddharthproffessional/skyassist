"""SkyAssist 3.0 — entry point and router.

    streamlit run app.py

Routes (one Streamlit server, one shared SQLite backend):
    /              landing page
    /passenger     Passenger portal (mobile-first)
    /coordinator   Airport coordinator portal (desktop-first)
    /operations    Operations overview + synthetic-data simulator (coordinators only)
    /occ           SkyAssist 3.0 Operations Control Centre (coordinators / OCC controllers only)
"""
import streamlit as st

import build_db
from approval import get_engine

st.set_page_config(page_title="SkyAssist", page_icon="✈️", layout="wide", initial_sidebar_state="auto")


@st.cache_resource
def _bootstrap():
    build_db.ensure_database()   # builds the synthetic DB if missing, otherwise runs idempotent migrations only
    get_engine()                 # one approval event loop shared by every session in this process
    from services.ops.seed import ensure_seeded
    ensure_seeded()              # SkyAssist 3.0: synthetic OCC demo dataset (only if not already present)
    return True


_bootstrap()

pages = [
    st.Page("views/home.py", title="SkyAssist", icon="✈️", default=True),
    st.Page("views/passenger.py", title="Passenger portal", icon="🧳", url_path="passenger"),
    st.Page("views/coordinator.py", title="Coordinator portal", icon="🧑‍✈️", url_path="coordinator"),
    st.Page("views/operations.py", title="Operations overview", icon="📊", url_path="operations"),
    st.Page("views/occ.py", title="Operations Control Centre", icon="🛫", url_path="occ"),
    st.Page("views/twin.py", title="3D airport twin", icon="🌐", url_path="twin"),
]
st.navigation(pages, position="hidden").run()
