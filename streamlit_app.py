"""
Halloween Sundowner RSVP — entry point.

  Guests:  https://<your-app>.streamlit.app/
  Hosts:   https://<your-app>.streamlit.app/?view=host   (password protected)

Run locally:  streamlit run streamlit_app.py
"""

import streamlit as st

from ui_common import decor, inject_css

IS_HOST = st.query_params.get("view") == "host"

st.set_page_config(
    page_title="Host lounge" if IS_HOST else "You're invited · RSVP",
    page_icon="🎃",
    layout="wide" if IS_HOST else "centered",
    initial_sidebar_state="collapsed",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

inject_css()
decor()

if IS_HOST:
    from host_ui import render_host
    st.html('<div class="host-mode"></div>')
    render_host()
else:
    from guest_ui import render_guest
    render_guest()
