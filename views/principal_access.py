"""UCViz Principal Access — the reverse view: principal -> data.

Pick a group, user or service principal (no catalog/schema needed) and see
everything it can reach, drilling Catalog -> Schema -> Table, with the privilege,
whether it is granted directly or inherited from a parent, ownership, and — for
users — which group the access comes through.
"""

from __future__ import annotations

import streamlit as st

from modules import ui
from modules.access_scope import visible_catalogs
from modules.db import load_config, viewer_email
from modules.icons import icon
from modules.permissions_lookup import (
    LEVEL_CATALOG,
    LEVEL_SCHEMA,
    LEVEL_TABLE,
    SOURCE_OWNER,
)
from modules.principal_access import (
    list_known_grantees,
    principal_access,
    summarize,
)
from modules.principals import (
    TYPE_GROUP,
    TYPE_SP,
    TYPE_USER,
    build_principal_lookup,
    classify_principal,
    list_directory_principals,
    user_groups,
)

ui.inject_theme()

TYPE_FILTERS = {"All": None, "Groups": TYPE_GROUP, "Users": TYPE_USER,
                "Service principals": TYPE_SP}
MAX_ROWS = 500


# --------------------------------------------------------------------------- #
# Cached loaders (keyed on viewer — results depend on the query identity)
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=3600, show_spinner=False)
def _directory() -> list[dict]:
    return list_directory_principals()


@st.cache_data(ttl=3600, show_spinner=False)
def _lookup() -> dict:
    return build_principal_lookup()


@st.cache_data(ttl=600, show_spinner=False)
def _known_grantees(viewer: str, catalogs: tuple[str, ...]) -> list[str]:
    return list_known_grantees(list(catalogs))


@st.cache_data(ttl=600, show_spinner=False)
def _user_groups(user: str) -> list[str]:
    return user_groups(user)


@st.cache_data(ttl=300, show_spinner=False)
def _access(viewer: str, grantees: tuple[tuple[str, str], ...],
            catalogs: tuple[str, ...]):
    return principal_access(dict(grantees), list(catalogs))


# --------------------------------------------------------------------------- #
# Render helpers
# --------------------------------------------------------------------------- #
def _privs(direct, inherited, owner) -> str:
    out = ""
    if owner:
        out += ui.source_badge(SOURCE_OWNER) + " "
    out += "".join(ui.pill(p, "#077a9d") for p in direct)
    out += "".join(ui.pill(f"{p} (inherited)", "#39456b") for p in inherited)
    return out or '<span class="uc-muted">—</span>'


def _via(via) -> str:
    return " ".join(
        '<span class="uc-muted">direct</span>' if v == "direct"
        else f'<span class="uc-code">{ui.esc(v)}</span>' for v in via
    ) or '<span class="uc-muted">direct</span>'


def _table(header: list[str], rows: list[str]):
    head = "".join(f"<th>{h}</th>" for h in header)
    st.markdown(f'<table class="uc"><tr>{head}</tr>{"".join(rows)}</table>',
                unsafe_allow_html=True)


def _drill(label: str, options: list[str], key: str) -> str:
    """Drill-down selectbox that tolerates its options changing underneath."""
    opts = ["—"] + options
    if st.session_state.get(key, "—") not in opts:
        st.session_state[key] = "—"  # overwrite so the browser updates too
    return st.selectbox(label, opts, key=key)


def _picker(cfg, viewer, catalogs):
    """Principal picker; returns (grantee, type)."""
    directory = _directory()
    lookup = _lookup()
    names = {p["grantee"]: p["name"] for p in directory}
    try:
        known = _known_grantees(viewer, tuple(catalogs))
    except Exception as exc:
        st.warning(f"Could not list grantees: {exc}")
        known = []
    options = sorted(set(known) | set(names))
    ptype = {g: classify_principal(g, lookup) for g in options}

    c1, c2, c3 = st.columns([1.2, 2.4, 2.4])
    with c1:
        kind = st.selectbox("Type", list(TYPE_FILTERS), key="pa_type")
    want = TYPE_FILTERS[kind]
    shown = [g for g in options if want is None or ptype[g] == want]
    with c2:
        q = st.query_params.get("principal")
        if q in shown and "pa_pick" not in st.session_state:
            st.session_state["pa_pick"] = q
        if st.session_state.get("pa_pick", "—") not in ["—"] + shown:
            st.session_state["pa_pick"] = "—"
        pick = st.selectbox(
            "Principal", ["—"] + shown, key="pa_pick",
            format_func=lambda g: g if g == "—" else (
                f"{names.get(g, g)} · "
                f"{ui.PRINCIPAL_META.get(ptype[g], ui.PRINCIPAL_META['unknown'])[2]}"))
    with c3:
        # A shared/refreshed URL may name a principal that isn't in the
        # dropdown (e.g. a user with only table grants) — restore it here.
        q = st.query_params.get("principal")
        if q and q not in shown and "pa_typed" not in st.session_state:
            st.session_state["pa_typed"] = q
        typed = st.text_input(
            "…or type one", key="pa_typed",
            placeholder="user email, group name, or SP application id")
    grantee = typed.strip() or (pick if pick != "—" else "")
    if not grantee:
        return None, None
    return grantee, classify_principal(grantee, lookup)


def main():
    cfg = load_config()
    viewer = viewer_email()
    catalogs = visible_catalogs(cfg, viewer, list(cfg.get("catalogs") or []))

    st.markdown(
        f'<div class="uc-title">{icon("users", size=24, color="#4fc3f7")}'
        " Principal Access</div>"
        '<div class="uc-subtitle">Pick a group, user or service principal and '
        "see every catalog, schema and table it can reach — direct grants, "
        "inherited grants and ownership.</div>",
        unsafe_allow_html=True,
    )

    grantee, ptype = _picker(cfg, viewer, catalogs)
    if not grantee:
        st.info("Choose a principal above (or type a user email / group / "
                "service-principal application id).")
        return
    st.query_params["principal"] = grantee

    grantees = {grantee: ""}
    if ptype == TYPE_USER:
        c1, c2 = st.columns(2)
        with c1:
            via_groups = st.checkbox(
                "Include access through group membership", value=True,
                key="pa_groups",
                help="Direct SCIM group memberships. Nested groups are not "
                     "expanded.")
        with c2:
            via_all = st.checkbox(
                "Include `account users`", value=False, key="pa_all_users",
                help="Every user is implicitly in `account users`. Off by "
                     "default: on a shared metastore it matches most catalogs.")
        if via_groups:
            found = _user_groups(grantee)
            for g in found:
                grantees.setdefault(g, f"group: {g}")
            if not found:
                st.caption("No group memberships could be read for this user "
                           "— the app service principal needs permission to "
                           "read SCIM group membership (workspace admin), or "
                           "the user is in no groups. Showing direct access "
                           "only.")
        if via_all:
            grantees.setdefault("account users", "group: account users")

    try:
        with st.spinner("Reading grants and ownership…"):
            frame, truncated = _access(viewer, tuple(sorted(grantees.items())),
                                       tuple(catalogs))
    except Exception as exc:
        st.error(f"Access lookup failed: {exc}")
        return
    if truncated:
        st.warning("Results hit the per-query row limit — some objects may be "
                   "missing. Restrict the scan in Admin → Catalog Access.")
    if frame.empty:
        st.markdown(
            f'<div class="uc-alert uc-alert-green">'
            f'{icon("info", size=18, color=ui.MUTED)}'
            f"No grants or ownership found for <b>{ui.esc(grantee)}</b> on "
            "objects visible to this app.</div>", unsafe_allow_html=True)
        return

    groups_used = [g for g in grantees if g != grantee]
    if groups_used:
        st.caption("Including groups: " + ", ".join(groups_used))

    cats = summarize(frame, LEVEL_CATALOG, ["catalog"], ["catalog", "schema"])
    n_sch = frame.dropna(subset=["schema"]).drop_duplicates(
        ["catalog", "schema"]).shape[0]
    n_tbl = frame.dropna(subset=["table"]).drop_duplicates(
        ["catalog", "schema", "table"]).shape[0]
    n_own = int((frame["source"] == SOURCE_OWNER).sum())
    cards = (ui.stat_card(len(cats), "Catalogs", "#4fc3f7", "database")
             + ui.stat_card(n_sch, "Schemas", "#00a972", "layers")
             + ui.stat_card(n_tbl, "Tables", "#ffab00", "table")
             + ui.stat_card(n_own, "Owned Objects", "#b18bff", "key"))
    st.markdown(f'<div class="uc-stat-grid">{cards}</div>',
                unsafe_allow_html=True)

    # ---- Level 1: catalogs ---------------------------------------------- #
    ui.section("Catalogs", "database")
    _table(["Catalog", "Privileges on catalog", "Via", "Schemas w/ access"], [
        f'<tr><td><code>{ui.esc(r["catalog"])}</code></td>'
        f"<td>{_privs(r['direct'], r['inherited'], r['owner'])}</td>"
        f"<td>{_via(r['via'])}</td><td>{r['children']}</td></tr>"
        for _, r in cats.iterrows()
    ])

    catalog = _drill("Drill into catalog", cats["catalog"].tolist(), "pa_cat")
    if catalog == "—":
        return

    # ---- Level 2: schemas ----------------------------------------------- #
    in_cat = frame[frame["catalog"] == catalog]
    schs = summarize(in_cat, LEVEL_SCHEMA, ["catalog", "schema"],
                     ["catalog", "schema", "table"])
    ui.section(f"Schemas in {catalog}", "layers")
    if schs.empty:
        st.markdown('<div class="uc-muted">Access is at catalog level only — '
                    "no schema- or table-level grants.</div>",
                    unsafe_allow_html=True)
        return
    _table(["Schema", "Privileges on schema", "Via", "Tables w/ access"], [
        f'<tr><td><code>{ui.esc(r["schema"])}</code></td>'
        f"<td>{_privs(r['direct'], r['inherited'], r['owner'])}</td>"
        f"<td>{_via(r['via'])}</td><td>{r['children']}</td></tr>"
        for _, r in schs.iterrows()
    ])

    schema = _drill("Drill into schema", schs["schema"].tolist(), "pa_sch")
    if schema == "—":
        return

    # ---- Level 3: tables ------------------------------------------------ #
    in_sch = in_cat[in_cat["schema"] == schema]
    tbls = summarize(in_sch, LEVEL_TABLE, ["catalog", "schema", "table"])
    ui.section(f"Tables in {catalog}.{schema}", "table")
    show_inherited = st.checkbox(
        "Show inherited table access", value=True, key="pa_inh",
        help="Off: only tables with a direct grant or ownership.")
    if not show_inherited and not tbls.empty:
        tbls = tbls[tbls["direct"].map(bool) | tbls["owner"]]
    if tbls.empty:
        st.markdown('<div class="uc-muted">No table-level access in this '
                    "schema.</div>", unsafe_allow_html=True)
        return
    rows = [
        f'<tr><td><code>{ui.esc(r["table"])}</code></td>'
        f"<td>{_privs(r['direct'], r['inherited'], r['owner'])}</td>"
        f"<td>{_via(r['via'])}</td></tr>"
        for _, r in tbls.head(MAX_ROWS).iterrows()
    ]
    _table(["Table", "Privileges", "Via"], rows)
    if len(tbls) > MAX_ROWS:
        st.caption(f"Showing {MAX_ROWS} of {len(tbls)} tables.")


main()
