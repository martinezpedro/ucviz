"""UCViz — Unity Catalog Visualizer: entrypoint router.

``st.navigation`` owns the page list so the sidebar shows real page names in a
fixed order — the two explore views on top, Admin in its own section below —
instead of Streamlit's file-name-based auto-discovery. The User Guide sits
in its own Help section so every user can find it.

Page files live in ``views/``, NOT ``pages/``: Streamlit auto-discovers a
``pages/`` folder, and if the first request after a restart hits one of those
files directly it serves the file-name navigation instead of this router.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="UCViz — Unity Catalog Visualizer",
                   page_icon="🔗", layout="wide")

nav = st.navigation({
    "Explore": [
        # default=True keeps it at "/" so existing ?catalog=… links still work.
        st.Page("views/lineage_access.py", title="Lineage & Access",
                icon=":material/account_tree:", default=True),
        st.Page("views/principal_access.py", title="Principal Access",
                icon=":material/group:", url_path="principal_access"),
    ],
    "Settings": [
        st.Page("views/admin.py", title="Admin", icon=":material/settings:",
                url_path="admin"),
    ],
    # Separate from Settings: Admin is restricted, the guide is for everyone.
    "Help": [
        st.Page("views/help.py", title="User Guide", icon=":material/menu_book:",
                url_path="help"),
    ],
})
nav.run()
