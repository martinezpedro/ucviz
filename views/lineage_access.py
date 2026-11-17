"""UCViz — Lineage & Access page (data asset -> lineage + who has access).

A read-only Streamlit app that visualizes lineage, permissions, and tags for any
Unity Catalog table by querying system tables through a SQL warehouse. Nothing
is hardcoded to a customer: catalogs, layers, groups, and tag roles are all
dynamic or admin-configured.
"""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from modules import ui
from modules.access_scope import visible_catalogs, visible_schemas
from modules.db import load_config, viewer_email
from modules.icons import icon
from modules.lineage_traversal import (
    build_lineage_graph,
    build_visible_graph,
    classify_layer,
    direct_neighbors,
    lineage_chain,
    lineage_stats,
    list_catalogs,
    list_schemas,
    list_tables_in_catalog,
    list_tables_in_schema,
    search_tables,
)
from modules.permissions_lookup import (
    STATUS_UNEXPECTED,
    annotate_principal_types,
    build_permissions_frame,
    build_table_metadata,
    find_permission_gaps,
    list_grantees,
    list_schema_grantees,
    permissions_stats,
    scope_grants,
    tables_for_grantee,
)
from modules.pii_scan import (
    STATUS_MASKED,
    STATUS_UNCLASSIFIED,
    pii_stats,
    scan_columns,
)
from modules.principals import build_principal_lookup
from modules.governance import build_health_findings, health_stats
from modules.tags_lookup import (
    build_tags_summary,
    list_schema_tag_values,
    tables_with_tag,
    tags_stats,
)
from modules.visualization import build_flow_elements, catalog_colors
from streamlit_flow import streamlit_flow
from streamlit_flow.layouts import ManualLayout
from streamlit_flow.state import StreamlitFlowState


LAYER_FILTERS = {
    "All Layers": None,
    "Gold Only": {"GOLD"},
    "Silver + Gold": {"SILVER", "GOLD"},
    "Bronze to Gold": {"BRONZE", "SILVER", "GOLD"},
    "External to Gold": {"EXTERNAL", "BRONZE", "SILVER", "GOLD"},
}

# Sentinels for the "all" picker options.
ALL_CATALOGS = "★ All catalogs"
ALL_SCHEMAS = "★ All schemas"
ALL_TABLES = "★ All tables in this schema"
ALL_TABLES_CATALOG = "★ All tables in this catalog"
ALL_TABLE_OPTIONS = (ALL_TABLES, ALL_TABLES_CATALOG)

# Table-picker placeholders (no real table selected).
NO_TABLES = "(no tables found)"
SEARCH_PROMPT = "(search for a table above)"
PICK_PROMPT = "(pick a table from the matches)"
NO_PICK = ("(none)", NO_TABLES, SEARCH_PROMPT, PICK_PROMPT)

# Single-table lineage modes.
MODE_TRACE = "Full trace (cross-catalog)"
MODE_STEP = "Step-by-step (real-time API)"
DIRECTIONS = {"Upstream + downstream": "both", "Upstream only": "upstream"}

# Fallback cap on how many schema tables may seed the lineage graph before the
# user must narrow the scope. Overridden by config ``max_graph_tables``.
MAX_GRAPH_TABLES_DEFAULT = 50

# Cap on rendered permission rows (a whole schema can have thousands of grants).
MAX_PERM_ROWS = 500


# --------------------------------------------------------------------------- #
# Cached data loaders
# --------------------------------------------------------------------------- #
# NOTE: every cache is keyed on ``viewer`` so on-behalf-of-user results (which
# depend on the caller's own privileges) are never shared across viewers.
@st.cache_data(ttl=600, show_spinner=False)
def _catalogs(viewer: str, allowed: tuple[str, ...]) -> list[str]:
    return list_catalogs(list(allowed))


@st.cache_data(ttl=600, show_spinner=False)
def _schemas(viewer: str, catalog: str) -> list[str]:
    return list_schemas(catalog)


@st.cache_data(ttl=300, show_spinner=False)
def _tables_in(viewer: str, catalog: str, schema: str) -> list[str]:
    return list_tables_in_schema(catalog, schema)


@st.cache_data(ttl=300, show_spinner=False)
def _tables_in_catalog(viewer: str, catalog: str) -> list[str]:
    return list_tables_in_catalog(catalog)


@st.cache_data(ttl=300, show_spinner=False)
def _search_tables(viewer: str, term: str,
                   allowed: tuple[str, ...]) -> list[str]:
    return search_tables(term, list(allowed))


@st.cache_data(ttl=300, show_spinner=False)
def _scope_grants(viewer: str, catalog: str | None, schema: str | None,
                  include_inherited: bool):
    df = scope_grants(catalog, schema, include_inherited)
    return annotate_principal_types(df, _principal_types())


@st.cache_data(ttl=300, show_spinner=False)
def _neighbors(viewer: str, node: str) -> dict:
    # One-hop upstream/downstream for a node, cached — powers click-to-expand.
    return direct_neighbors(node)


# Loaders that back the large-schema narrowing gate (one query each, cached).
@st.cache_data(ttl=300, show_spinner=False)
def _schema_tag_values(viewer: str, catalog: str, schema: str | None,
                       roles_key: str) -> list[str]:
    cfg = load_config()
    return list_schema_tag_values(catalog, schema, cfg.get("tag_roles", {}))


@st.cache_data(ttl=300, show_spinner=False)
def _schema_grantees(viewer: str, catalog: str,
                     schema: str | None) -> list[str]:
    return list_schema_grantees(catalog, schema)


@st.cache_data(ttl=300, show_spinner=False)
def _tables_with_tag(viewer: str, catalog: str, schema: str | None,
                     tag_name: str,
                     tag_value: str) -> tuple[str, ...]:
    return tuple(tables_with_tag(catalog, schema, tag_name, tag_value))


@st.cache_data(ttl=300, show_spinner=False)
def _tables_for_grantee(viewer: str, catalog: str, schema: str | None,
                        grantee: str) -> tuple[str, ...]:
    return tuple(tables_for_grantee(catalog, schema, grantee))


@st.cache_data(ttl=300, show_spinner=False)
def _lineage(viewer: str, targets: tuple[str, ...], max_hops: int,
             layer_rules_key: str, direction: str):
    cfg = load_config()
    tgt_list = list(targets)
    graph = build_lineage_graph(tgt_list, max_hops, cfg.get("layer_rules"),
                                direction=direction)
    return graph, lineage_chain(graph), lineage_stats(graph, tgt_list)


@st.cache_data(ttl=3600, show_spinner=False)
def _principal_types() -> dict:
    # SCIM directory is account-level (viewer-independent), so this is cached
    # once for all viewers with a long TTL.
    return build_principal_lookup()


@st.cache_data(ttl=300, show_spinner=False)
def _permissions(viewer: str, tables: tuple[str, ...], layer_rules_key: str):
    cfg = load_config()
    tbl_list = list(tables)
    meta = build_table_metadata(tbl_list)
    perms = build_permissions_frame(tbl_list, cfg.get("layer_rules"), meta)
    perms = annotate_principal_types(perms, _principal_types())
    return perms, find_permission_gaps(perms), permissions_stats(perms), meta


@st.cache_data(ttl=300, show_spinner=False)
def _tags(viewer: str, tables: tuple[str, ...], roles_key: str):
    cfg = load_config()
    summary = build_tags_summary(
        list(tables), cfg.get("tag_roles", {}),
        cfg.get("high_sensitivity_values", []),
    )
    return summary, tags_stats(summary)


@st.cache_data(ttl=300, show_spinner=False)
def _pii(viewer: str, tables: tuple[str, ...], roles_key: str,
         _tags_summary: dict):
    # _tags_summary is prefixed with '_' so Streamlit does not try to hash it;
    # the cache key is viewer + tables + roles_key (which fully determine it).
    findings = scan_columns(list(tables), _tags_summary)
    return findings, pii_stats(findings)


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def render_stats(l_stats, p_stats, t_stats, pii_st=None, h_stats=None):
    pii_st = pii_st or {}
    h_stats = h_stats or {}
    warn = h_stats.get("total", 0)
    cards = (
        ui.stat_card(l_stats.get("total_hops", 0), "Total Hops", "#4fc3f7",
                     "git-branch")
        + ui.stat_card(l_stats.get("tables_in_chain", 0), "Tables in Chain",
                       "#00a972", "database")
        + ui.stat_card(p_stats.get("groups_with_access", 0),
                       "Principals w/ Access", "#ffab00", "users")
        + ui.stat_card(warn, "Health Warnings",
                       "#ff3621" if warn else "#00a972", "shield-alert")
    )
    st.markdown(f'<div class="uc-stat-grid">{cards}</div>',
                unsafe_allow_html=True)

    cards2 = (
        ui.stat_card(t_stats.get("distinct_tags", 0), "Distinct Tags",
                     "#077a9d", "tag")
        + ui.stat_card(t_stats.get("rls_tables", 0), "Tables w/ RLS",
                       "#ab4057", "filter-rows")
        + ui.stat_card(t_stats.get("masked_columns", 0), "Masked Columns",
                       "#ffab00", "lock")
        + ui.stat_card(pii_st.get("unclassified_pii", 0),
                       "Unclassified PII", "#ff3621", "eye")
    )
    st.markdown(f'<div class="uc-stat-grid">{cards2}</div>',
                unsafe_allow_html=True)


def render_legend():
    order = ["EXTERNAL", "BRONZE", "SILVER", "GOLD", "CONSUMER"]
    items = "".join(
        f'<div class="uc-legend-item">'
        f'<span class="uc-swatch" style="background:{ui.LAYER_COLORS[l]};">'
        f"</span>{l.title()}</div>"
        for l in order
    )
    st.markdown(f'<div class="uc-legend">{items}</div>', unsafe_allow_html=True)


def render_catalog_legend(graph):
    """Catalog stripe colors — shown when the lineage spans catalogs."""
    colors = catalog_colors(graph)
    if not colors:
        return
    items = "".join(
        f'<div class="uc-legend-item">'
        f'<span class="uc-swatch" style="background:{c};"></span>'
        f'<span class="uc-code">{ui.esc(cat)}</span></div>'
        for cat, c in colors.items()
    )
    label = ("Catalogs in this lineage" if len(colors) > 1
             else "Catalog")
    st.markdown(f'<div class="uc-legend"><span class="uc-muted">{label}:'
                f"</span>{items}</div>", unsafe_allow_html=True)


def render_scope_grants(viewer, catalog, schema):
    """Catalog- and schema-level grants + owners for the picked scope."""
    if catalog:
        where = f"{catalog}.{schema}" if schema else f"{catalog} (all schemas)"
    else:
        where = "all catalogs (catalog level only)"
    st.markdown(
        f'<div class="uc-muted" style="margin:4px 0 8px;">Who was granted what '
        f'on <span class="uc-code">{ui.esc(where)}</span> itself — catalog and '
        "schema grants plus owners, independent of the lineage graph. Table "
        "grants are in the <b>Access</b> tab.</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])
    with c1:
        inherited = st.checkbox(
            "Include inherited", key="scope_inherited",
            help="Also show catalog grants echoed on each schema.")
    with c2:
        needle = st.text_input("Filter principal", key="scope_filter",
                               placeholder="filter by principal…",
                               label_visibility="collapsed")
    try:
        df = _scope_grants(viewer, catalog, schema, inherited)
    except Exception as exc:
        st.warning(f"Catalog/schema grant lookup failed: {exc}")
        return
    if needle.strip() and not df.empty:
        df = df[df["grantee"].astype(str).str.lower().str.contains(
            needle.strip().lower(), regex=False)]
    if df.empty:
        st.markdown('<div class="uc-muted">No catalog- or schema-level grants '
                    "visible for this scope.</div>", unsafe_allow_html=True)
        return
    n_cat = int((df["level"] == "CATALOG").sum())
    n_sch = int((df["level"] == "SCHEMA").sum())
    st.markdown(
        f'<div class="uc-muted" style="margin-bottom:6px;">{n_cat} catalog-level '
        f"and {n_sch} schema-level rows · "
        f'{df["grantee"].nunique()} distinct principals.</div>',
        unsafe_allow_html=True)
    header = ("<tr><th>Level</th><th>Securable</th><th>Principal</th>"
              "<th>Privilege</th><th>Access Via</th><th>Grantor</th></tr>")
    body = []
    for _, r in df.head(MAX_PERM_ROWS).iterrows():
        body.append(
            "<tr>"
            f"<td>{ui.pill(r['level'].title(), '#077a9d')}</td>"
            f'<td><code>{ui.esc(r["securable"])}</code></td>'
            f"<td>{ui.principal_pill(r['grantee'], r.get('principal_type'))}</td>"
            f"<td>{ui.esc(r['privilege'])}</td>"
            f"<td>{ui.source_badge(r.get('source'))}</td>"
            f'<td class="uc-muted">{ui.esc(r.get("grantor"))}</td></tr>'
        )
    st.markdown(f'<table class="uc">{header}{"".join(body)}</table>',
                unsafe_allow_html=True)
    if len(df) > MAX_PERM_ROWS:
        st.markdown(f'<div class="uc-muted" style="margin-top:6px;">Showing '
                    f"{MAX_PERM_ROWS} of {len(df)} rows — filter by principal "
                    "or pick a schema to narrow.</div>", unsafe_allow_html=True)


def render_chain(chain, tags_summary):
    if not chain:
        st.markdown('<div class="uc-chain uc-muted">No upstream hops found.'
                    "</div>", unsafe_allow_html=True)
        return
    blocks = []
    for hop in chain:
        tinfo = tags_summary.get(hop["target"], {})
        badges = ""
        if tinfo.get("rls"):
            badges += ui.pill("RLS", "#ab4057")
        if tinfo.get("cls"):
            badges += ui.pill(f"CLS x{tinfo['cls']}", "#ffab00", dark_text=True)
        blocks.append(
            f'<div class="uc-chain">'
            f'{ui.layer_pill(hop["source_layer"])} '
            f'<span class="uc-code">{ui.esc(hop["source"])}</span><br>'
            f'<span class="uc-muted">to </span>'
            f'{ui.layer_pill(hop["target_layer"])} '
            f'<span class="uc-code">{ui.esc(hop["target"])}</span> {badges}'
            f"</div>"
        )
    st.markdown("".join(blocks), unsafe_allow_html=True)


def render_permissions(perms, layer_filter, group_filter):
    if perms.empty:
        st.markdown('<div class="uc-muted">No permission rows to display.</div>',
                    unsafe_allow_html=True)
        return
    header = ("<tr><th>Table</th><th>Layer</th><th>Principal</th>"
              "<th>Access Via</th><th>Privilege</th><th>Status</th></tr>")
    body = []
    shown = 0
    matched = 0
    for _, r in perms.iterrows():
        if layer_filter is not None and r["layer"] not in layer_filter:
            continue
        if group_filter != "All Groups" and r.get("grantee") != group_filter:
            # Filtered-out group rows are hidden entirely in large views.
            if not (not r["has_access"] or r["status"] == STATUS_UNEXPECTED):
                continue
        matched += 1
        if shown >= MAX_PERM_ROWS:
            continue
        classes = []
        if not r["has_access"] or r["status"] == STATUS_UNEXPECTED:
            classes.append("issue")
        if group_filter != "All Groups" and r.get("grantee") != group_filter:
            classes.append("dim")
        cls = f' class="{" ".join(classes)}"' if classes else ""
        principal = (ui.principal_pill(r["grantee"], r.get("principal_type"))
                     if r.get("grantee") else '<span class="uc-muted">—</span>')
        body.append(
            f"<tr{cls}>"
            f'<td><code>{ui.esc(r["table"])}</code></td>'
            f"<td>{ui.layer_pill(r['layer'])}</td>"
            f"<td>{principal}</td>"
            f"<td>{ui.source_badge(r.get('source'))}</td>"
            f"<td>{ui.esc(r['privilege'])}</td>"
            f"<td>{ui.status_cell(r['status'])}</td></tr>"
        )
        shown += 1
    if not body:
        st.markdown('<div class="uc-muted">No rows match the current filters.'
                    "</div>", unsafe_allow_html=True)
        return
    if matched > shown:
        st.markdown(
            f'<div class="uc-muted" style="margin-bottom:6px;">Showing '
            f"{shown} of {matched} grant rows — use the layer/group filters or "
            "pick a single table to narrow.</div>",
            unsafe_allow_html=True,
        )
    st.markdown(f'<table class="uc">{header}{"".join(body)}</table>',
                unsafe_allow_html=True)


def render_tags(tags_summary, layer_filter, perms):
    # Map table -> layer for filtering.
    layer_of = {}
    if not perms.empty:
        layer_of = dict(zip(perms["table"], perms["layer"]))
    any_shown = False
    rows = []
    for table, info in tags_summary.items():
        if layer_filter is not None and layer_of.get(table) not in layer_filter:
            continue
        pills = "".join(ui.tag_pill(t) for t in info["tags"])
        col_pills = "".join(
            ui.tag_pill({**t, "name": f'{t["column"]}.{t["name"]}'})
            for t in info["column_tags"]
        )
        sec = ""
        if info.get("rls"):
            sec += ui.pill("RLS", "#ab4057")
        if info.get("cls"):
            sec += ui.pill(f"CLS x{info['cls']}", "#ffab00", dark_text=True)
        if not (pills or col_pills or sec):
            continue
        any_shown = True
        rows.append(
            f"<tr><td><code>{ui.esc(table)}</code></td>"
            f"<td>{pills or '<span class=uc-muted>—</span>'}</td>"
            f"<td>{col_pills or '<span class=uc-muted>—</span>'}</td>"
            f"<td>{sec or '<span class=uc-muted>—</span>'}</td></tr>"
        )
    if not any_shown:
        st.markdown('<div class="uc-muted">No tags, RLS, or masks found on the '
                    "tables in this chain.</div>", unsafe_allow_html=True)
        return
    header = ("<tr><th>Table</th><th>Table Tags</th><th>Column Tags</th>"
              "<th>Security</th></tr>")
    st.markdown(f'<table class="uc">{header}{"".join(rows)}</table>',
                unsafe_allow_html=True)


def render_pii_scan(findings, layer_filter, perms):
    if findings is None or findings.empty:
        ic = icon("check-circle", size=18, color=ui.STATUS_COLORS["granted"])
        st.markdown(f'<div class="uc-alert uc-alert-green">{ic}'
                    "No columns matched sensitive-data patterns in this chain."
                    "</div>", unsafe_allow_html=True)
        return

    layer_of = dict(zip(perms["table"], perms["layer"])) if not perms.empty else {}

    # Apply the layer filter for consistency with the rest of the page.
    rows_all = [r for _, r in findings.iterrows()
                if layer_filter is None or layer_of.get(r["table"]) in layer_filter]
    unclassified = [r for r in rows_all if r["status"] == STATUS_UNCLASSIFIED]

    n = len(unclassified)
    if n:
        ic = icon("alert-triangle", size=18, color=ui.STATUS_COLORS["missing"])
        st.markdown(
            f'<div class="uc-alert uc-alert-red">{ic}'
            f"<b>{n} column{'s' if n != 1 else ''} look like sensitive data "
            "but are not classified or masked</b> — review and tag/mask below."
            "</div>", unsafe_allow_html=True)
    else:
        ic = icon("check-circle", size=18, color=ui.STATUS_COLORS["granted"])
        st.markdown(f'<div class="uc-alert uc-alert-green">{ic}'
                    "All detected sensitive columns are already classified or "
                    "masked.</div>", unsafe_allow_html=True)

    sev_color = {"high": "#ff3621", "medium": "#ffab00", "low": "#8892b0"}
    status_color = {STATUS_UNCLASSIFIED: "#ff3621", "Classified": "#00a972",
                    STATUS_MASKED: "#077a9d"}
    header = ("<tr><th>Table</th><th>Column</th><th>Type</th>"
              "<th>Likely Category</th><th>Why</th><th>Status</th>"
              "<th>Recommendation</th></tr>")
    body = []
    shown = 0
    for r in rows_all:
        if shown >= MAX_PERM_ROWS:
            break
        issue = r["status"] == STATUS_UNCLASSIFIED
        cls = " class='issue'" if issue else ""
        body.append(
            f"<tr{cls}>"
            f'<td><code>{ui.esc(r["table"].split(".")[-1])}</code></td>'
            f'<td><code>{ui.esc(r["column"])}</code></td>'
            f'<td class="uc-muted">{ui.esc(r["data_type"])}</td>'
            f"<td>{ui.pill(r['category'], sev_color.get(r['severity'], ui.MUTED), dark_text=r['severity']=='medium')}</td>"
            f'<td class="uc-muted">{ui.esc(r["why"])}</td>'
            f"<td>{ui.pill(r['status'], status_color.get(r['status'], ui.MUTED), dark_text=r['status']==STATUS_MASKED)}</td>"
            f'<td><code>{ui.esc(r["recommendation"])}</code></td></tr>'
        )
        shown += 1
    st.markdown(f'<table class="uc">{header}{"".join(body)}</table>',
                unsafe_allow_html=True)
    if len(rows_all) > shown:
        st.markdown(f'<div class="uc-muted" style="margin-top:6px;">Showing '
                    f"{shown} of {len(rows_all)} flagged columns.</div>",
                    unsafe_allow_html=True)


def render_table_health(findings):
    """Consolidated per-table governance warnings, most severe first."""
    n = 0 if findings is None or findings.empty else len(findings)
    if n == 0:
        ic = icon("check-circle", size=18, color=ui.STATUS_COLORS["granted"])
        st.markdown(f'<div class="uc-alert uc-alert-green">{ic}'
                    "No governance warnings for the table(s) in view — access, "
                    "classification, masking, ownership and docs all look "
                    "healthy.</div>", unsafe_allow_html=True)
        return
    high = int((findings["severity"] == "high").sum())
    ic = icon("shield-alert", size=18, color=ui.STATUS_COLORS["missing"])
    st.markdown(
        f'<div class="uc-alert uc-alert-red">{ic}'
        f"<b>{n} warning{'s' if n != 1 else ''}</b>"
        f"{f' ({high} high severity)' if high else ''} "
        "correlated to the table(s) in view — Well-Architected governance "
        "and security checks.</div>",
        unsafe_allow_html=True,
    )
    header = ("<tr><th>Severity</th><th>Table</th><th>Pillar</th>"
              "<th>Finding</th><th>Recommendation</th></tr>")
    rows = "".join(
        f"<tr class='{'issue' if r['severity'] == 'high' else ''}'>"
        f"<td>{ui.severity_pill(r['severity'])}</td>"
        f'<td><code>{ui.esc(r["table"].split(".")[-1])}</code></td>'
        f"<td>{ui.pillar_badge(r['pillar'])}</td>"
        f'<td><b>{ui.esc(r["check"])}</b><br>'
        f'<span class="uc-muted">{ui.esc(r["issue"])}</span></td>'
        f'<td><code>{ui.esc(r["recommendation"])}</code></td></tr>'
        for _, r in findings.iterrows()
    )
    st.markdown(f'<table class="uc">{header}{rows}</table>',
                unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def _seed_widget(key, value, options):
    """On first load, seed a widget's state from a query param (if valid)."""
    if value and key not in st.session_state and value in options:
        st.session_state[key] = value


def _prune_widget(key, options, fallback=None):
    """Replace a stale stored value (not in ``options``) on a cascade.

    Overwrite rather than delete: deleting left the browser showing the old
    choice (e.g. a schema from the previous catalog) while the app had
    already moved on. ``fallback`` is used when it is a valid option.
    """
    if key in st.session_state and st.session_state[key] not in options:
        st.session_state[key] = (fallback if fallback in options
                                 else options[0])


def _default_widget(key, value):
    """Pre-select ``value`` when nothing is stored yet (keeps the old default
    of "first real catalog/schema" now that an "All" option sits on top)."""
    if value and key not in st.session_state:
        st.session_state[key] = value


def sidebar(cfg, viewer, qp):
    with st.sidebar:
        st.markdown(
            f'<div class="uc-title" style="font-size:1.3rem;">'
            f'{icon("network", size=24, color="#4fc3f7")} UCViz</div>'
            '<div class="uc-subtitle" style="margin-bottom:14px;">'
            "Unity Catalog Visualizer</div>",
            unsafe_allow_html=True,
        )

        # Catalog -> schema -> table cascade (the metastore is far too large
        # for a single flat dropdown — verified at ~500k tables live). Each
        # picker is seeded from the URL (?catalog=…&schema=…&table=…) on first
        # load so a browser refresh restores the selection. "All catalogs" /
        # "All schemas" widen the scope — customers that split medallion layers
        # by catalog pick a Gold table from any catalog this way.
        allowed = tuple(cfg.get("catalogs", []))
        st.markdown('<div class="uc-header">Catalog</div>',
                    unsafe_allow_html=True)
        try:
            catalogs = visible_catalogs(cfg, viewer,
                                        _catalogs(viewer, allowed))
        except Exception as exc:
            st.error(f"Could not list catalogs: {exc}")
            catalogs = []
        cat_opts = ([ALL_CATALOGS] + catalogs) if catalogs else ["(none)"]
        _seed_widget("sel_catalog", qp.get("catalog"), cat_opts)
        _prune_widget("sel_catalog", cat_opts,
                      catalogs[0] if catalogs else None)
        _default_widget("sel_catalog", catalogs[0] if catalogs else None)
        catalog = st.selectbox("Catalog", options=cat_opts,
                               key="sel_catalog", label_visibility="collapsed")
        all_catalogs = catalog == ALL_CATALOGS
        real_catalog = bool(catalog) and catalog not in ("(none)", ALL_CATALOGS)
        # Switching a single catalog -> All catalogs keeps the table on screen.
        prev_catalog = st.session_state.get("_prev_catalog")
        st.session_state["_prev_catalog"] = catalog
        carried = st.session_state.get("sel_table")
        if not (all_catalogs and prev_catalog not in (None, ALL_CATALOGS)
                and carried and carried.count(".") == 2):
            carried = None

        st.markdown('<div class="uc-header">Schema</div>',
                    unsafe_allow_html=True)
        if all_catalogs:
            schemas, sch_opts = [], [ALL_SCHEMAS]
        else:
            schemas = (visible_schemas(cfg, viewer, catalog,
                                       _schemas(viewer, catalog))
                       if real_catalog else [])
            sch_opts = ([ALL_SCHEMAS] + schemas) if schemas else ["(none)"]
        _seed_widget("sel_schema", qp.get("schema"), sch_opts)
        _prune_widget("sel_schema", sch_opts, schemas[0] if schemas else None)
        _default_widget("sel_schema", schemas[0] if schemas else None)
        schema = st.selectbox("Schema", options=sch_opts, key="sel_schema",
                              label_visibility="collapsed",
                              disabled=all_catalogs)

        q_table = qp.get("table")
        if all_catalogs:
            st.markdown('<div class="uc-header">Table (any catalog)</div>',
                        unsafe_allow_html=True)
            if (q_table and q_table.count(".") == 2
                    and "table_search" not in st.session_state):
                st.session_state["table_search"] = q_table.split(".")[-1]
            if carried:
                st.session_state["table_search"] = carried.split(".")[-1]
            term = st.text_input("Find a table", key="table_search",
                                 placeholder="table name contains…",
                                 label_visibility="collapsed")
            tables = (_search_tables(viewer, term.strip(), allowed)
                      if term.strip() else [])
            if q_table and q_table.count(".") == 2 and q_table not in tables:
                tables = [q_table] + tables
            if carried and carried not in tables:
                tables = [carried] + tables
            # A prompt first, so a search never auto-traces its first match.
            options = ([PICK_PROMPT] + tables) if tables else [SEARCH_PROMPT]
        elif schema == ALL_SCHEMAS:
            st.markdown('<div class="uc-header">Table</div>',
                        unsafe_allow_html=True)
            tables = _tables_in_catalog(viewer, catalog) if real_catalog else []
            options = ([ALL_TABLES_CATALOG] + tables) if tables else [NO_TABLES]
        else:
            st.markdown('<div class="uc-header">Table</div>',
                        unsafe_allow_html=True)
            tables = (_tables_in(viewer, catalog, schema)
                      if schema and schema != "(none)" else [])
            options = ([ALL_TABLES] + tables) if tables else [NO_TABLES]
        _seed_widget("sel_table", q_table, options)
        _prune_widget("sel_table", options)
        selected = st.selectbox("Table", options=options, key="sel_table",
                                label_visibility="collapsed",
                                help="Pick one table, or the whole scope.")

        st.markdown('<div class="uc-header">Lineage</div>',
                    unsafe_allow_html=True)
        mode = st.radio(
            "Lineage mode", [MODE_TRACE, MODE_STEP], key="sel_lineage_mode",
            label_visibility="collapsed",
            help="Full trace walks system.access.table_lineage (plus the "
                 "real-time API) across catalogs up to Max Hops. Step-by-step "
                 "starts at the table and reveals one hop per click.")
        direction = "upstream"
        if mode == MODE_TRACE:
            dir_label = st.radio("Direction", list(DIRECTIONS),
                                 key="sel_direction", horizontal=True,
                                 label_visibility="collapsed")
            direction = DIRECTIONS[dir_label]

        st.markdown('<div class="uc-header">Max Lineage Hops</div>',
                    unsafe_allow_html=True)
        max_hops = st.slider("Max hops", 1, 10, int(cfg.get("max_hops", 5)),
                             key="sel_max_hops", label_visibility="collapsed")

    # The display filters (Layer / Group) live in the results fragment so
    # changing them reruns only the results — not this catalog→table cascade.
    return selected, catalog, schema, tables, max_hops, mode, direction


# --------------------------------------------------------------------------- #
# Large-schema narrowing gate
# --------------------------------------------------------------------------- #
def _gate_panel(msg: str):
    ic = icon("alert-triangle", size=18, color=ui.STATUS_COLORS["missing"])
    ui.section("Narrow the Scope", "search")
    st.markdown(f'<div class="uc-alert uc-alert-red">{ic}{msg}</div>',
                unsafe_allow_html=True)


def narrow_large_schema(viewer, catalog, schema, base, limit, cfg):
    """Render the narrowing controls for an over-limit schema and apply them.

    Returns ``(targets, scope_label, blocked)``. When ``blocked`` is True the
    graph must not be drawn — a gate panel has been rendered explaining why.
    """
    layer_rules = cfg.get("layer_rules")
    roles_key = str(cfg.get("tag_roles"))
    layers_present = sorted({classify_layer(t, layer_rules) for t in base})
    scope_name = f"{catalog}.{schema}" if schema else catalog
    noun = "schema" if schema else "catalog"

    with st.sidebar:
        st.markdown('<div class="uc-header">Narrow Scope (required)</div>',
                    unsafe_allow_html=True)
        st.caption(f"{len(base)} tables in this {noun} — narrow to "
                   f"{limit} or fewer to graph, or pick a single table above.")
        n_layer = st.selectbox("Narrow — layer", ["Any"] + layers_present,
                               key="narrow_layer", label_visibility="collapsed")
        try:
            tag_opts = _schema_tag_values(viewer, catalog, schema, roles_key)
        except Exception:
            tag_opts = []
        n_tag = st.selectbox("Narrow — tag", ["Any"] + tag_opts,
                             key="narrow_tag", label_visibility="collapsed",
                             help=f"Domain / sensitivity tag values in this {noun}.")
        try:
            grantee_opts = _schema_grantees(viewer, catalog, schema)
        except Exception:
            grantee_opts = []
        n_group = st.selectbox("Narrow — group", ["Any"] + grantee_opts,
                               key="narrow_group", label_visibility="collapsed",
                               help="Only tables this principal can reach.")
        n_name = st.text_input("Narrow — name contains", key="narrow_name",
                               placeholder="table name contains…",
                               label_visibility="collapsed")

    # AND-combine the active filters over the schema's tables.
    subset = list(base)
    if n_layer != "Any":
        subset = [t for t in subset if classify_layer(t, layer_rules) == n_layer]
    if n_name.strip():
        nl = n_name.strip().lower()
        subset = [t for t in subset if nl in t.split(".")[-1].lower()]
    if n_tag != "Any" and "=" in n_tag:
        tname, tval = n_tag.split("=", 1)
        allowed = set(_tables_with_tag(viewer, catalog, schema, tname, tval))
        subset = [t for t in subset if t in allowed]
    if n_group != "Any":
        allowed = set(_tables_for_grantee(viewer, catalog, schema, n_group))
        subset = [t for t in subset if t in allowed]

    label = (f"{scope_name} — {len(subset)} of {len(base)} tables "
             "(filtered)")

    if not subset:
        _gate_panel("No tables match the current <b>Narrow Scope</b> filters. "
                    "Loosen a filter in the sidebar or pick a single table.")
        return [], label, True
    if len(subset) <= limit:
        return subset, label, False

    # Still over the limit — offer an explicit opt-in truncation.
    with st.sidebar:
        escape = st.checkbox(f"Graph the first {limit} anyway",
                             key="narrow_escape")
    if escape:
        st.warning(f"Graphing the first {limit} of {len(subset)} matching "
                   "tables — narrow further to see the rest.")
        return (subset[:limit],
                f"{scope_name} — first {limit} of {len(subset)}", False)

    _gate_panel(
        f"<b>{len(subset)} tables</b> match the current filters — more than the "
        f"{limit}-table graph limit. Use the <b>Narrow Scope</b> filters in the "
        "sidebar (layer, tag, group, or name), pick a single table above, or "
        f"tick <b>Graph the first {limit} anyway</b>."
    )
    return [], label, True


# --------------------------------------------------------------------------- #
# Results fragment
#
# Wrapping the display filters + focus banner + tabs in a fragment means that
# changing a filter, clicking a graph node, or hitting "Show all" reruns ONLY
# this block — not the whole app, and never the (already-cached) data fetch.
# The catalog→schema→table→hops cascade stays outside so it still triggers a
# full rerun when the underlying data actually needs to change.
# --------------------------------------------------------------------------- #
@st.fragment
def render_results(graph, chain, tags_summary, perms, pii_findings, health,
                   tables, progressive=False, viewer="", scope=(None, None)):
    with st.sidebar:
        st.markdown('<div class="uc-header">Filter by Layer</div>',
                    unsafe_allow_html=True)
        layer_choice = st.selectbox("Layer", options=list(LAYER_FILTERS.keys()),
                                    key="layer_choice",
                                    label_visibility="collapsed")
        st.markdown('<div class="uc-header">Filter by Group</div>',
                    unsafe_allow_html=True)
        group_choice = st.selectbox(
            "Group", options=["All Groups"] + list_grantees(perms),
            key="group_choice", label_visibility="collapsed")
        st.markdown('<div class="uc-header">Graph Selection</div>',
                    unsafe_allow_html=True)
        multi = st.checkbox(
            "Multi-select nodes", key="multi_select",
            help="On: each node you click is added to the selection. "
                 "Off: clicking a node selects just that one.")
        st.divider()
        st.caption("New here? See **Help → User Guide** (left nav) for what "
                   "each control and tab does.")
    layer_filter = LAYER_FILTERS[layer_choice]

    # Selection is a SET of nodes. streamlit-flow only reports one clicked node
    # at a time, so multi-select is accumulated on our side. Validate the stored
    # selection against the currently-visible graph.
    selected = [n for n in st.session_state.get("sel_nodes", [])
                if n in graph.nodes]
    focus = set(selected)

    if selected:
        names = ", ".join(n.split(".")[-1] for n in selected)
        fcol, bcol = st.columns([6, 1])
        with fcol:
            st.markdown(
                f'<div class="uc-muted" style="margin:6px 0;">Inspecting '
                f'<span class="uc-code">{ui.esc(names)}</span> — the Access, '
                "Table Health, Tags and Sensitive Data tabs are scoped to "
                f"{'these tables' if len(selected) > 1 else 'this table'}.</div>",
                unsafe_allow_html=True)
        with bcol:
            if st.button("Clear", key="clear_focus", use_container_width=True):
                st.session_state["sel_nodes"] = []
                if "focus" in st.query_params:
                    del st.query_params["focus"]
                if progressive:
                    root = st.session_state.get("flow_root")
                    st.session_state["flow_expanded"] = [root] if root else []
                st.rerun()

    if focus:
        perms_v = perms[perms["table"].isin(focus)] if not perms.empty else perms
        pii_v = (pii_findings[pii_findings["table"].isin(focus)]
                 if not pii_findings.empty else pii_findings)
        health_v = (health[health["table"].isin(focus)]
                    if not health.empty else health)
        tags_v = {k: v for k, v in tags_summary.items() if k in focus}
    else:
        perms_v, pii_v, tags_v, health_v = (perms, pii_findings, tags_summary,
                                            health)

    (tab_lin, tab_access, tab_scope, tab_health, tab_tags,
     tab_pii) = st.tabs(["Lineage", "Access", "Catalog & Schema Grants",
                         "Table Health", "Tags", "Sensitive Data"])

    with tab_lin:
        hint = ("Click a node to select it — it highlights and scopes the other "
                "tabs. Toggle <b>Multi-select</b> (sidebar) to pick several; "
                "use <b>Clear</b> to deselect.")
        if progressive:
            hint += " ＋ nodes reveal their next hop when clicked."
        st.markdown(f'<div class="uc-muted" style="margin:4px 0 8px;">{hint}</div>',
                    unsafe_allow_html=True)
        render_legend()
        render_catalog_legend(graph)
        sel = None
        try:
            nodes, edges = build_flow_elements(
                graph, focus=focus, palette=ui.current_palette())
            # Key on node set + selection so selecting re-mounts the component
            # (the selected-node ring updates); a display-filter change keeps the
            # same key so drag positions survive. Deterministic layered positions
            # + ManualLayout mean re-mounts never overlap.
            node_sig = "|".join(sorted(graph.nodes))
            focus_sig = "|".join(sorted(focus))
            flow_key = "ucflow_" + hashlib.md5(
                (node_sig + "##" + focus_sig).encode()).hexdigest()[:10]
            fs = streamlit_flow(
                flow_key, StreamlitFlowState(nodes, edges),
                layout=ManualLayout(), fit_view=True, height=680,
                get_node_on_click=True, show_controls=True,
            )
            sel = getattr(fs, "selected_id", None)
        except Exception as exc:
            st.error(f"Graph rendering failed: {exc}")

        # Apply a click. Single mode: the clicked node replaces the selection.
        # Multi mode: it's added if not already selected. Guard so an unchanged
        # selection doesn't refire (loop-safe); deselect is via Clear.
        if sel and sel in graph.nodes:
            cur = list(selected)
            changed = False
            if multi:
                if sel not in cur:
                    cur.append(sel)
                    changed = True
            elif cur != [sel]:
                cur = [sel]
                changed = True
            if changed:
                if progressive and len(sel.split(".")) == 3:
                    exp = set(st.session_state.get("flow_expanded", []))
                    exp.add(sel)  # additive — reveal next hop
                    st.session_state["flow_expanded"] = list(exp)
                st.session_state["sel_nodes"] = cur
                st.query_params["focus"] = cur[0] if cur else ""
                st.rerun()

        ui.section("Lineage Chain", "git-branch")
        render_chain(chain, tags_summary)

    with tab_access:
        a_stats = permissions_stats(perms_v)
        st.markdown(
            f'<div class="uc-muted" style="margin:4px 0 8px;">'
            f'{a_stats.get("users", 0)} users · {a_stats.get("groups", 0)} '
            f'groups · {a_stats.get("service_principals", 0)} service '
            "principals with access. <b>Access Via</b> shows a direct table "
            "grant, an inherited schema/catalog grant, or ownership.</div>",
            unsafe_allow_html=True,
        )
        render_permissions(perms_v, layer_filter, group_choice)

    with tab_scope:
        render_scope_grants(viewer, *scope)

    with tab_health:
        render_table_health(health_v)

    with tab_tags:
        render_tags(tags_v, layer_filter, perms_v)

    with tab_pii:
        render_pii_scan(pii_v, layer_filter, perms_v)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    qp = st.query_params
    ui.inject_theme()

    # Restore a previously-selected table (from ?focus=) on first load.
    if ("focus" in qp and qp.get("focus")
            and "sel_nodes" not in st.session_state):
        st.session_state["sel_nodes"] = [qp.get("focus")]

    cfg = load_config()
    viewer = viewer_email()
    (selected, catalog, schema, scope_tables, max_hops, lineage_mode,
     direction) = sidebar(cfg, viewer, qp)

    # Persist the selection in the URL so a browser refresh restores this view.
    st.query_params.update({
        "catalog": catalog if catalog and catalog != "(none)" else "",
        "schema": schema if schema and schema != "(none)" else "",
        "table": selected if selected and selected not in NO_PICK else "",
    })

    st.markdown(
        f'<div class="uc-title">{icon("git-branch", size=26, color="#4fc3f7")}'
        " Unity Catalog Visualizer</div>"
        '<div class="uc-subtitle">Lineage, permissions, and tags for any UC '
        "table, schema or catalog — read directly from system tables.</div>",
        unsafe_allow_html=True,
    )

    # Catalog / schema scope for the grant view (None == "All …").
    scope_catalog = (catalog if catalog not in ("(none)", ALL_CATALOGS, None)
                     else None)
    scope_schema = (schema if scope_catalog and schema not in
                    ("(none)", ALL_SCHEMAS, None) else None)
    has_scope = scope_catalog is not None or catalog == ALL_CATALOGS

    def scope_only(message: str):
        # No table to graph — still show catalog/schema-level grants.
        st.info(message)
        if has_scope:
            ui.section("Catalog & Schema Grants", "key")
            render_scope_grants(viewer, scope_catalog, scope_schema)

    if not selected or selected in NO_PICK:
        if selected == PICK_PROMPT:
            scope_only("Pick one of the matching tables in the sidebar to "
                       "trace its lineage.")
        elif selected == SEARCH_PROMPT:
            scope_only("Type part of a table name in the sidebar to search "
                       "every catalog, then pick the table to trace.")
        else:
            scope_only("No tables available here. Confirm the SQL warehouse "
                       "is bound and you (or the app service principal) have "
                       "USE CATALOG on at least one catalog (configure them "
                       "in the Admin page).")
        return

    # Resolve the lineage targets: one table, or the whole schema/catalog. A
    # scope larger than the configured limit is gated behind a narrowing step
    # so the graph stays digestible instead of silently truncating.
    limit = int(cfg.get("max_graph_tables", MAX_GRAPH_TABLES_DEFAULT))
    schema_mode = selected in ALL_TABLE_OPTIONS
    if schema_mode:
        base = list(scope_tables)
        scope_name = (f"{scope_catalog}.{scope_schema}" if scope_schema
                      else scope_catalog)
        if len(base) > limit:
            targets, scope_label, blocked = narrow_large_schema(
                viewer, scope_catalog, scope_schema, base, limit, cfg)
            if blocked:
                ui.section("Catalog & Schema Grants", "key")
                render_scope_grants(viewer, scope_catalog, scope_schema)
                return
        else:
            targets = base
            scope_label = (f"{scope_name} — {len(base)} table"
                           f"{'s' if len(base) != 1 else ''}")
    else:
        targets = [selected]
        scope_label = selected

    st.markdown(
        f'<div class="uc-subtitle" style="margin-top:-10px;">Scope: '
        f'<span class="uc-code">{ui.esc(scope_label)}</span></div>',
        unsafe_allow_html=True,
    )

    layer_rules_key = str(cfg.get("layer_rules"))
    roles_key = str(cfg.get("tag_roles"))
    progressive = (not schema_mode) and lineage_mode == MODE_STEP

    prev_root = st.session_state.get("flow_root")
    if not schema_mode and prev_root != selected:
        # New table picked: reset expansion + focus. On a fresh session
        # (prev_root None) keep the focus restored from ?focus=.
        st.session_state["flow_root"] = selected
        st.session_state["flow_expanded"] = [selected]
        if prev_root is not None:
            st.session_state["sel_nodes"] = []

    if progressive:
        # Single table, step-by-step: progressive expand from this root via
        # the real-time API.
        root = selected
        expanded = st.session_state.get("flow_expanded", [root])
        try:
            graph = build_visible_graph(
                root, expanded, lambda n: _neighbors(viewer, n),
                cfg.get("layer_rules"))
        except Exception as exc:
            st.error(f"Lineage traversal failed: {exc}")
            return
        chain = lineage_chain(graph)
        l_stats = lineage_stats(graph, [root])
    else:
        # Full trace: bounded BFS over system.access.table_lineage + the
        # real-time API, across catalogs. A whole-schema/catalog pick (past the
        # narrowing gate) traces upstream only, as before.
        walk = "upstream" if schema_mode else direction
        try:
            with st.spinner("Tracing lineage across catalogs…"):
                graph, chain, l_stats = _lineage(
                    viewer, tuple(targets), max_hops, layer_rules_key, walk)
        except Exception as exc:
            st.error(f"Lineage traversal failed: {exc}")
            return
    for msg in graph.graph.get("warnings", []):
        st.warning(msg)

    # Only real UC tables (3-part names) get permission/tag lookups; external
    # origins (e.g. SAP) and file paths are shown in the graph but aren't UC
    # securables, so they're excluded from the grant/gap analysis.
    tables = tuple(n for n in graph.nodes if len(str(n).split(".")) == 3)

    try:
        perms, gaps, p_stats, table_meta = _permissions(viewer, tables,
                                                         layer_rules_key)
    except Exception as exc:
        st.warning(f"Permission lookup failed: {exc}")
        perms, gaps, p_stats, table_meta = (
            pd.DataFrame(), pd.DataFrame(),
            {"groups_with_access": 0, "permission_gaps": 0}, {})

    try:
        tags_summary, t_stats = _tags(viewer, tables, roles_key)
    except Exception as exc:
        st.warning(f"Tag lookup failed: {exc}")
        tags_summary, t_stats = {}, {"distinct_tags": 0, "rls_tables": 0,
                                     "masked_columns": 0}

    try:
        pii_findings, pii_st = _pii(viewer, tables, roles_key, tags_summary)
    except Exception as exc:
        st.warning(f"Sensitive-data scan failed: {exc}")
        pii_findings, pii_st = pd.DataFrame(), {"sensitive_columns": 0,
                                                "unclassified_pii": 0}

    # Consolidated per-table governance findings (pure — no queries).
    try:
        health = build_health_findings(list(tables), tags_summary,
                                       pii_findings, table_meta, perms)
    except Exception as exc:
        st.warning(f"Health checks failed: {exc}")
        health = pd.DataFrame()
    h_stats = health_stats(health)

    # Stats reflect the whole scope and don't depend on the display filters, so
    # they render once here, outside the fragment.
    render_stats(l_stats, p_stats, t_stats, pii_st, h_stats)

    # Everything filter/focus-driven lives in a fragment: changing a filter or
    # clicking a node reruns only the results, keeping the cascade + cached data.
    # The grants tab follows the catalog/schema the table lives in when a
    # single table is picked under "All catalogs".
    if scope_catalog is None and not schema_mode and selected.count(".") == 2:
        scope = (selected.split(".")[0], selected.split(".")[1])
    else:
        scope = (scope_catalog, scope_schema)
    render_results(graph, chain, tags_summary, perms, pii_findings, health,
                   tables, progressive, viewer, scope)


# Executed by st.navigation in app.py (the entrypoint router).
main()
