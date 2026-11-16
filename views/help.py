"""UCViz User Guide — renders docs/USER_GUIDE.md inside the app.

One source of truth for the guide: the Markdown file in the repo. Each ``##``
section becomes a tab; under "Q&A", each ``###`` question becomes an
expandable entry with a search box, so users can find answers without asking.
"""

from __future__ import annotations

import os
import re

import streamlit as st

from modules import ui
from modules.icons import icon

ui.inject_theme()

GUIDE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "USER_GUIDE.md"
)
QA_TITLE = "Q&A"


def _sections(text: str) -> list[tuple[str, str]]:
    """``[(title, body)]`` for each ``## `` section (text before the first is
    repo-only preamble and is skipped)."""
    parts = re.split(r"^## +(.+)$", text, flags=re.MULTILINE)
    return [(parts[i].strip(), parts[i + 1].strip())
            for i in range(1, len(parts) - 1, 2)]


def _questions(body: str) -> list[tuple[str, str]]:
    parts = re.split(r"^### +(.+)$", body, flags=re.MULTILINE)
    return [(parts[i].strip(), parts[i + 1].strip())
            for i in range(1, len(parts) - 1, 2)]


def _render_qa(body: str) -> None:
    questions = _questions(body)
    needle = st.text_input("Search questions", key="help_search",
                           placeholder="e.g. lineage, access, slow, admin…",
                           label_visibility="collapsed")
    words = [w for w in needle.lower().split() if w]
    shown = [(q, a) for q, a in questions
             if all(w in (q + " " + a).lower() for w in words)]
    if not shown:
        st.markdown('<div class="uc-muted">No questions match — try fewer '
                    "words, or check the other tabs.</div>",
                    unsafe_allow_html=True)
    for q, a in shown:
        with st.expander(q, expanded=bool(words) and len(shown) <= 3):
            st.markdown(a)


def main():
    st.markdown(
        f'<div class="uc-title">{icon("info", size=24, color="#4fc3f7")}'
        " UCViz User Guide</div>"
        '<div class="uc-subtitle">What UCViz does, how it works, what each '
        "page shows, its limits, and answers to common questions.</div>",
        unsafe_allow_html=True,
    )
    try:
        with open(GUIDE_PATH, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        st.error("The user guide file (docs/USER_GUIDE.md) is missing from "
                 "this deployment.")
        return

    sections = _sections(text)
    tabs = st.tabs([title for title, _ in sections])
    for tab, (title, body) in zip(tabs, sections):
        with tab:
            if title == QA_TITLE:
                _render_qa(body)
            else:
                st.markdown(body)


# Executed by st.navigation in app.py (the entrypoint router).
main()
