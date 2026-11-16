"""Shared UI theme and render helpers (dark theme, Lucide icons, tag pills)."""

from __future__ import annotations

import html

import streamlit as st

from modules.icons import icon
from modules.tags_lookup import ROLE_ABAC, ROLE_DOMAIN, ROLE_SENSITIVITY

# Structural palettes. Only these background/text/accent tones change between
# dark and light; the saturated layer/status/tag colors below work on both.
# Extra GRAPH_*/NODE_*/EDGE_* keys feed the Plotly figure (visualization.py).
PALETTES = {
    "dark": {
        "BG": "#1a1a2e", "PANEL": "#16213e", "PANEL_HOVER": "#1c2b52",
        "BORDER": "#2f3a63", "TEXT": "#e6e9f2", "MUTED": "#9aa6c9",
        "HEADER": "#b8c2e6", "ACCENT": "#4fc3f7",
        "GRAPH_BG": "#16213e", "EDGE": "#7c8ac7", "EDGE_DIM": "#39456b",
        "NODE_TEXT": "#e6e9f2", "NODE_TEXT_DIM": "#5b6785",
        "NODE_LINE": "#16213e", "NODE_DIM": "#3a4568",
    },
    "light": {
        "BG": "#f4f6fb", "PANEL": "#ffffff", "PANEL_HOVER": "#eef2fb",
        "BORDER": "#d3dae8", "TEXT": "#1b2340", "MUTED": "#5c6885",
        "HEADER": "#33406b", "ACCENT": "#0b74c4",
        "GRAPH_BG": "#ffffff", "EDGE": "#5666a6", "EDGE_DIM": "#c4ccdf",
        "NODE_TEXT": "#1b2340", "NODE_TEXT_DIM": "#9aa3bd",
        "NODE_LINE": "#ffffff", "NODE_DIM": "#c4ccdf",
    },
}

# Active structural colors (module globals; reassigned by apply_theme). These
# default to dark and are read at call time by the render helpers below.
_active = dict(PALETTES["dark"])
BG = _active["BG"]
PANEL = _active["PANEL"]
PANEL_HOVER = _active["PANEL_HOVER"]
BORDER = _active["BORDER"]
TEXT = _active["TEXT"]
MUTED = _active["MUTED"]
HEADER = _active["HEADER"]
ACCENT = _active["ACCENT"]


def apply_theme(theme: str = "dark") -> None:
    """Point the module color globals at the chosen palette (dark|light)."""
    global _active, BG, PANEL, PANEL_HOVER, BORDER, TEXT, MUTED, HEADER, ACCENT
    _active = dict(PALETTES.get(theme, PALETTES["dark"]))
    BG, PANEL, PANEL_HOVER = _active["BG"], _active["PANEL"], _active["PANEL_HOVER"]
    BORDER, TEXT, MUTED = _active["BORDER"], _active["TEXT"], _active["MUTED"]
    HEADER, ACCENT = _active["HEADER"], _active["ACCENT"]


def current_palette() -> dict:
    """Active palette dict (structural + GRAPH/NODE/EDGE keys) for the figure."""
    return dict(_active)

LAYER_COLORS = {
    "EXTERNAL": "#ff6b6b",
    "BRONZE": "#cd7f32",
    "SILVER": "#c0c0c0",
    "GOLD": "#ffd700",
    "CONSUMER": "#4fc3f7",
    "UNKNOWN": "#8892b0",
}
_DARK_TEXT_LAYERS = {"SILVER", "GOLD", "CONSUMER"}

STATUS_COLORS = {"granted": "#00a972", "missing": "#ff3621", "unexpected": "#ffab00"}
STATUS_ICONS = {
    "granted": "check-circle",
    "missing": "x-circle",
    "unexpected": "alert-triangle",
}

# Tag pill colors by role (spec).
TAG_COLORS = {
    ROLE_DOMAIN: "#077a9d",
    ROLE_ABAC: "#ab4057",
    "sensitivity_high": "#ff3621",
    "sensitivity_confidential": "#ffab00",
    "sensitivity_public": "#00a972",
    "sensitivity_default": "#ffab00",
    "other": "#8892b0",
}


def inject_theme(theme: str = "dark") -> None:
    apply_theme(theme)
    st.markdown(
        f"""
        <style>
        .stApp {{ background: {BG}; color: {TEXT}; }}
        .stApp, .stApp p, .stApp span, .stApp label, .stApp div {{
            color: {TEXT}; }}
        [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
            background: {BG}; }}
        section[data-testid="stSidebar"] {{ background: {PANEL};
            border-right: 1px solid {BORDER}; }}
        section[data-testid="stSidebar"] * {{ color: {TEXT}; }}

        /* --- Native chrome follows the theme (fixes half-applied light) --- */
        header[data-testid="stHeader"] {{ background: {BG} !important; }}
        header[data-testid="stHeader"] * {{ color: {TEXT} !important; }}
        [data-testid="stToolbar"] *, [data-testid="stMainMenu"] svg {{
            color: {TEXT} !important; fill: {TEXT} !important; }}
        [data-testid="stDecoration"] {{
            background: linear-gradient(90deg, {ACCENT}, {BORDER}) !important; }}
        /* buttons */
        .stButton > button {{ background: {PANEL} !important;
            color: {TEXT} !important; border: 1px solid {BORDER} !important;
            border-radius: 8px !important; }}
        .stButton > button:hover {{ border-color: {ACCENT} !important;
            color: {ACCENT} !important; }}
        /* radio (theme toggle) */
        .stRadio label, .stRadio div, .stRadio span {{ color: {TEXT} !important; }}
        /* Streamlit status boxes readable on either background */
        [data-testid="stAlert"] {{ background: {PANEL} !important;
            color: {TEXT} !important; }}

        /* --- Filters / inputs: dark, high-contrast, friendly --- */
        div[data-baseweb="select"] > div {{
            background: {BG} !important; border: 1px solid {BORDER} !important;
            border-radius: 8px !important; color: {TEXT} !important; }}
        div[data-baseweb="select"] * {{ color: {TEXT} !important; }}
        div[data-baseweb="select"] svg {{ fill: {MUTED} !important; }}
        /* dropdown popover menu */
        div[data-baseweb="popover"] li,
        ul[role="listbox"] li {{
            background: {PANEL} !important; color: {TEXT} !important; }}
        div[data-baseweb="popover"] li:hover,
        ul[role="listbox"] li:hover {{ background: {PANEL_HOVER} !important; }}
        /* text/number inputs */
        .stTextInput input, .stNumberInput input, .stTextArea textarea {{
            background: {BG} !important; color: {TEXT} !important;
            border: 1px solid {BORDER} !important; border-radius: 8px !important; }}
        /* Widget surfaces Streamlit fills from the dark secondaryBackgroundColor
           — force them to the active theme so filters aren't black in light mode. */
        div[data-baseweb="select"] > div, div[data-baseweb="base-input"],
        div[data-baseweb="input"], .stTextInput > div > div,
        .stNumberInput > div > div, .stMultiSelect > div > div {{
            background: {BG} !important; color: {TEXT} !important; }}
        /* Hammer: baseweb paints the dark background on a NESTED div, so force
           every div (and input) inside the select control to the theme. */
        div[data-baseweb="select"], div[data-baseweb="select"] div,
        div[data-baseweb="select"] input {{
            background-color: {BG} !important; color: {TEXT} !important; }}
        /* Open dropdown menu container + options. */
        div[data-baseweb="popover"] div, ul[role="listbox"] {{
            background-color: {PANEL} !important; }}
        div[data-baseweb="popover"] li, ul[role="listbox"] li {{
            background-color: {PANEL} !important; color: {TEXT} !important; }}
        /* slider */
        div[data-baseweb="slider"] [role="slider"] {{
            background: {ACCENT} !important; }}
        .stSlider label, .stSelectbox label, .stMultiSelect label {{
            color: {TEXT} !important; }}
        /* tabs — a proper nav bar, not cramped defaults */
        div[data-baseweb="tab-list"] {{ gap: 6px;
            border-bottom: 1px solid {BORDER}; margin-bottom: 6px; }}
        button[data-baseweb="tab"] {{ color: {MUTED} !important;
            font-size: .95rem; font-weight: 600;
            padding: 10px 20px !important; }}
        button[data-baseweb="tab"]:hover {{ color: {TEXT} !important; }}
        button[data-baseweb="tab"][aria-selected="true"] {{
            color: {ACCENT} !important; }}
        div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] {{
            background-color: {ACCENT} !important; }}

        .uc-title {{ font-size: 1.9rem; font-weight: 700; margin: 0;
            color: {TEXT}; display:flex; align-items:center; gap:10px; }}
        .uc-subtitle {{ color: {MUTED}; margin: 2px 0 18px 0; }}
        .uc-header {{ text-transform: uppercase; letter-spacing: 1px;
            font-weight: 700; font-size: .74rem; color: {HEADER};
            margin: 16px 0 4px 0; }}
        .uc-section {{ font-weight: 700; font-size: 1.05rem; margin: 6px 0 10px 0;
            display:flex; align-items:center; gap:8px; }}
        .uc-panel {{ background: {PANEL}; border: 1px solid {BORDER};
            border-radius: 12px; padding: 16px 18px; margin-bottom: 18px; }}

        .uc-stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr);
            gap: 14px; margin-bottom: 22px; }}
        .uc-stat {{ background: {PANEL}; border: 1px solid {BORDER};
            border-radius: 12px; padding: 16px 18px; }}
        .uc-stat-val {{ font-size: 2rem; font-weight: 700; line-height: 1; }}
        .uc-stat-lbl {{ color: {MUTED}; font-size: .72rem; text-transform: uppercase;
            letter-spacing: 1px; margin-top: 8px; display:flex;
            align-items:center; gap:6px; }}

        .uc-legend {{ display:flex; flex-wrap:wrap; gap:14px; margin-bottom:10px; }}
        .uc-legend-item {{ display:flex; align-items:center; gap:6px;
            font-size:.8rem; color:{MUTED}; }}
        .uc-swatch {{ width:14px; height:14px; border-radius:4px;
            border:1px solid {BORDER}; }}

        .uc-chain {{ border:1px solid {BORDER}; border-radius:8px;
            padding:8px 10px; margin-bottom:8px; font-size:.82rem; }}
        .uc-code {{ font-family: monospace; color:{TEXT}; }}
        .uc-muted {{ color:{MUTED}; }}

        .uc-pill {{ display:inline-block; padding:2px 8px; border-radius:6px;
            font-size:.72rem; font-weight:600; margin:2px 4px 2px 0; }}

        table.uc {{ width:100%; border-collapse:collapse; font-size:.82rem; }}
        table.uc th {{ text-align:left; text-transform:uppercase;
            letter-spacing:1px; font-size:.68rem; color:{MUTED};
            padding:8px 10px; border-bottom:1px solid {BORDER}; }}
        table.uc td {{ padding:8px 10px; border-bottom:1px solid {BORDER};
            vertical-align:middle; }}
        table.uc tr.issue td {{ background: rgba(255,54,33,0.08); }}
        table.uc tr.dim td {{ opacity:0.3; }}
        /* Streamlit paints <code> with the (dark) secondaryBackgroundColor from
           config.toml, which stays dark in light mode → unreadable. Retint every
           <code> (table names, column names, recommendation SQL) to the theme. */
        .stApp code, table.uc code {{ font-family:monospace;
            background:{PANEL_HOVER} !important; color:{TEXT} !important;
            padding:1px 5px; border-radius:4px; }}

        .uc-alert {{ border-radius:12px; padding:14px 18px; margin-bottom:14px;
            display:flex; align-items:center; gap:10px; }}
        .uc-alert-red {{ background: rgba(255,54,33,0.12);
            border:1px solid {STATUS_COLORS['missing']}; }}
        .uc-alert-green {{ background: rgba(0,169,114,0.12);
            border:1px solid {STATUS_COLORS['granted']}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else "—"


def pill(text: str, color: str, dark_text: bool = False) -> str:
    fg = "#1a1a2e" if dark_text else "#ffffff"
    return (
        f'<span class="uc-pill" style="background:{color};color:{fg};">'
        f"{esc(text)}</span>"
    )


def layer_pill(layer: str) -> str:
    color = LAYER_COLORS.get(layer, MUTED)
    return pill(layer, color, dark_text=layer in _DARK_TEXT_LAYERS)


def tag_pill(tag: dict) -> str:
    """Color a tag pill by role + value per the design spec."""
    role = tag.get("role", "other")
    value = str(tag.get("value", "")).lower()
    if role == ROLE_DOMAIN:
        color, dark = TAG_COLORS[ROLE_DOMAIN], False
    elif role == ROLE_ABAC:
        color, dark = TAG_COLORS[ROLE_ABAC], False
    elif role == ROLE_SENSITIVITY:
        if tag.get("high") or value in ("pii", "high", "restricted"):
            color, dark = TAG_COLORS["sensitivity_high"], False
        elif value in ("confidential", "internal"):
            color, dark = TAG_COLORS["sensitivity_confidential"], True
        elif value in ("public", "open"):
            color, dark = TAG_COLORS["sensitivity_public"], False
        else:
            color, dark = TAG_COLORS["sensitivity_default"], True
    else:
        color, dark = TAG_COLORS["other"], False
    label = f"{tag.get('name')}: {tag.get('value')}"
    return pill(label, color, dark_text=dark)


# Principal-type presentation (user / group / service_principal / unknown).
PRINCIPAL_META = {
    "user": ("user", "#4fc3f7", "User"),
    "group": ("users", "#00a972", "Group"),
    "service_principal": ("server", "#b18bff", "Service Principal"),
    "unknown": ("info", MUTED, "Unknown"),
}

# Source-of-access presentation.
SOURCE_META = {
    "DIRECT": ("shield", "Direct", "#4fc3f7"),
    "SCHEMA": ("building", "Schema", "#ffab00"),
    "CATALOG": ("building", "Catalog", "#ff8c00"),
    "OWNER": ("key", "Owner", "#00a972"),
    "METASTORE": ("building", "Metastore", "#b18bff"),
}

SEVERITY_COLORS = {"high": "#ff3621", "medium": "#ffab00", "low": "#8892b0"}


def principal_pill(name, ptype: str) -> str:
    """A grantee with a type-coded icon + label."""
    icon_name, color, label = PRINCIPAL_META.get(
        ptype, PRINCIPAL_META["unknown"])
    ic = icon(icon_name, size=13, color=color)
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;">'
        f'{ic}<span class="uc-code">{esc(name)}</span>'
        f'<span class="uc-muted" style="font-size:.68rem;">{label}</span></span>'
    )


def source_badge(source) -> str:
    """Badge showing where an effective grant comes from."""
    if not source:
        return '<span class="uc-muted">—</span>'
    icon_name, label, color = SOURCE_META.get(
        source, ("info", str(source).title(), MUTED))
    return pill(label, color, dark_text=source in ("SCHEMA", "CATALOG",
                                                    "OWNER"))


def severity_pill(severity: str) -> str:
    color = SEVERITY_COLORS.get(severity, MUTED)
    return pill(severity.title(), color, dark_text=severity == "medium")


def pillar_badge(pillar: str) -> str:
    return pill(pillar, "#077a9d")


def status_cell(status: str) -> str:
    color = STATUS_COLORS.get(status, MUTED)
    ic = icon(STATUS_ICONS.get(status, "alert-triangle"), size=15, color=color)
    label = {"granted": "Granted", "missing": "No Grants",
             "unexpected": "Unexpected"}.get(status, status)
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f'color:{color};font-weight:600;">{ic}{label}</span>'
    )


def stat_card(value, label: str, color: str, icon_name: str) -> str:
    ic = icon(icon_name, size=13, color=MUTED)
    return (
        f'<div class="uc-stat">'
        f'<div class="uc-stat-val" style="color:{color};">{value}</div>'
        f'<div class="uc-stat-lbl">{ic}{esc(label)}</div></div>'
    )


def section(title: str, icon_name: str) -> None:
    ic = icon(icon_name, size=18, color="#4fc3f7")
    st.markdown(f'<div class="uc-section">{ic}{esc(title)}</div>',
                unsafe_allow_html=True)
