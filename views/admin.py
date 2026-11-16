"""UCViz Admin panel — catalog access, connection test, layer & tag config.

Admin-only: access is gated on the caller's group membership (config
``admin_groups``). Config edits are written back to ``config/app_config.yaml``
when the filesystem is writable; otherwise they persist for the session and can
be downloaded for redeploy.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import ui
from modules.db import (
    current_user,
    load_config,
    run_query,
    save_config,
    viewer_in_any_group,
)
from modules.external_sources import (
    link_source_to_table,
    list_external_sources,
    register_external_source,
)
from modules.icons import icon

ui.inject_theme()


def is_admin(cfg: dict, user: dict) -> bool:
    """True if the signed-in user is in one of ``admin_groups``.

    Checked in SQL as the viewer (``is_member`` / ``is_account_group_member``)
    so it doesn't depend on the app SP being able to read SCIM group
    membership; falls back to the SCIM groups when there is no viewer token.
    Fails closed.
    """
    admin_groups = list(cfg.get("admin_groups", []))
    member = viewer_in_any_group(admin_groups)
    if member is not None:
        return member
    wanted = {g.lower() for g in admin_groups}
    return bool(wanted & {g.lower() for g in user.get("groups", [])})


def all_catalogs() -> list[str]:
    """List catalogs visible to the caller via system.information_schema."""
    try:
        df = run_query(
            "SELECT catalog_name FROM system.information_schema.catalogs "
            "ORDER BY catalog_name"
        )
        return df["catalog_name"].astype(str).tolist() if not df.empty else []
    except Exception:
        return []


def persist(cfg: dict):
    """Save config, falling back to session + download when read-only."""
    if save_config(cfg):
        st.success("Configuration saved to config/app_config.yaml.")
    else:
        st.session_state["ucviz_config_override"] = cfg
        import yaml

        st.warning(
            "Filesystem is read-only — changes are held for this session. "
            "Download the YAML below and redeploy to persist."
        )
        st.download_button(
            "Download app_config.yaml",
            data=yaml.safe_dump(cfg, sort_keys=False),
            file_name="app_config.yaml",
            mime="text/yaml",
        )


def main():
    cfg = st.session_state.get("ucviz_config_override") or load_config()
    user = current_user()

    st.markdown(
        f'<div class="uc-title">{icon("sliders", size=24, color="#4fc3f7")}'
        " UCViz Admin</div>"
        '<div class="uc-subtitle">Catalog access, layer mapping, tag roles, '
        "and connection testing.</div>",
        unsafe_allow_html=True,
    )

    if not is_admin(cfg, user):
        st.error("You are not a UCViz administrator. Ask an admin to add one "
                 "of your groups to `admin_groups` in the config.")
        st.caption(f"Signed in as: {user.get('user_name') or 'unknown'} · "
                   "not a member of: "
                   f"{', '.join(cfg.get('admin_groups', [])) or '(none set)'}")
        return

    tab_cat, tab_conn, tab_layers, tab_tags, tab_ext = st.tabs(
        ["Catalog Access", "Connection Test", "Layer Mapping",
         "Tag Definitions", "External Sources"]
    )

    # ---- Catalog Access ------------------------------------------------- #
    with tab_cat:
        ui.section("Catalog Access Manager", "database")
        st.caption("Choose which catalogs UCViz may scan. Empty = every "
                   "catalog the service principal can see.")
        available = all_catalogs()
        current = [c for c in cfg.get("catalogs", []) if c in available] \
            if available else cfg.get("catalogs", [])
        chosen = st.multiselect(
            "Catalogs to scan", options=available or cfg.get("catalogs", []),
            default=current,
        )
        max_hops = st.slider("Default max lineage hops", 1, 10,
                             int(cfg.get("max_hops", 5)))
        max_graph_tables = st.slider(
            "Max tables before narrowing is required", 10, 200,
            int(cfg.get("max_graph_tables", 50)),
            help="A whole-schema pick larger than this must be narrowed "
                 "(layer / tag / name / group) or drilled to one table.")
        if st.button("Save catalog settings", type="primary"):
            cfg["catalogs"] = chosen
            cfg["max_hops"] = max_hops
            cfg["max_graph_tables"] = max_graph_tables
            persist(cfg)

    # ---- Connection Test ------------------------------------------------ #
    with tab_conn:
        ui.section("Connection Test", "plug")
        st.caption("Verify the service principal can read the system tables "
                   "and each configured catalog.")
        if st.button("Run connection test", type="primary"):
            checks = []

            def check(label, query):
                try:
                    run_query(query)
                    checks.append((label, True, ""))
                except Exception as exc:
                    checks.append((label, False, str(exc)[:200]))

            check("system.access.table_lineage",
                  "SELECT 1 FROM system.access.table_lineage LIMIT 1")
            check("system.access.column_lineage",
                  "SELECT 1 FROM system.access.column_lineage LIMIT 1")
            check("system.information_schema.tables",
                  "SELECT 1 FROM system.information_schema.tables LIMIT 1")
            for view in ("catalog_privileges", "schema_privileges",
                         "table_privileges"):
                check(f"system.information_schema.{view}",
                      f"SELECT 1 FROM system.information_schema.{view} LIMIT 1")
            for cat in cfg.get("catalogs", []):
                safe = cat.replace("`", "")
                check(f"catalog: {cat}",
                      f"SELECT 1 FROM `{safe}`.information_schema.tables LIMIT 1")

            rows = []
            for label, ok, detail in checks:
                rows.append(
                    "<tr><td>" + ui.esc(label) + "</td><td>"
                    + ui.status_cell("granted" if ok else "missing")
                    + "</td><td class='uc-muted'>" + ui.esc(detail or "OK")
                    + "</td></tr>"
                )
            st.markdown(
                '<table class="uc"><tr><th>Check</th><th>Result</th>'
                f'<th>Detail</th></tr>{"".join(rows)}</table>',
                unsafe_allow_html=True,
            )

    # ---- Layer Mapping -------------------------------------------------- #
    with tab_layers:
        ui.section("Layer Mapping", "layers")
        st.caption("Ordered rules: the first substring that matches a table's "
                   "full name sets its layer. Top rule wins.")
        rules_df = pd.DataFrame(cfg.get("layer_rules", []) or
                                [{"match": "", "layer": "BRONZE"}])
        edited = st.data_editor(
            rules_df, num_rows="dynamic", use_container_width=True,
            column_config={
                "match": st.column_config.TextColumn("Match (substring)"),
                "layer": st.column_config.SelectboxColumn(
                    "Layer",
                    options=["EXTERNAL", "BRONZE", "SILVER", "GOLD", "CONSUMER"],
                ),
            },
            key="layer_editor",
        )
        if st.button("Save layer rules", type="primary"):
            cfg["layer_rules"] = [
                {"match": str(r["match"]), "layer": str(r["layer"])}
                for r in edited.to_dict("records")
                if str(r.get("match", "")).strip()
            ]
            persist(cfg)

    # ---- Tag Definitions ------------------------------------------------ #
    with tab_tags:
        ui.section("Tag Definitions", "tag")
        st.caption("Map tag keys to a role so pills are colored consistently. "
                   "Comma-separated substrings, matched case-insensitively.")
        roles = cfg.get("tag_roles", {})
        domain = st.text_input("Domain tag keys",
                               ", ".join(roles.get("domain", [])))
        sensitivity = st.text_input("Sensitivity tag keys",
                                    ", ".join(roles.get("sensitivity", [])))
        abac = st.text_input("ABAC tag keys", ", ".join(roles.get("abac", [])))
        high = st.text_input(
            "High-sensitivity values (render red)",
            ", ".join(cfg.get("high_sensitivity_values", [])),
        )

        def split(text):
            return [t.strip() for t in text.split(",") if t.strip()]

        if st.button("Save tag definitions", type="primary"):
            cfg["tag_roles"] = {
                "domain": split(domain),
                "sensitivity": split(sensitivity),
                "abac": split(abac),
            }
            cfg["high_sensitivity_values"] = split(high)
            persist(cfg)

    # ---- External Sources ---------------------------------------------- #
    with tab_ext:
        ui.section("External Sources", "network")
        st.caption("Register a source system as UC External Metadata and link "
                   "it upstream to a bronze table via the External Lineage API.")

        existing = list_external_sources()
        if existing:
            rows = "".join(
                f"<tr><td>{ui.esc(s['name'])}</td>"
                f"<td>{ui.esc(s['system_type'])}</td>"
                f"<td class='uc-muted'>{ui.esc(s['url'])}</td></tr>"
                for s in existing
            )
            st.markdown('<table class="uc"><tr><th>Name</th><th>Type</th>'
                        f'<th>URL</th></tr>{rows}</table>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="uc-muted">No external metadata objects '
                        "registered (or API unavailable).</div>",
                        unsafe_allow_html=True)

        if not cfg.get("enable_external_source_writes"):
            st.caption("Registering sources is disabled — UCViz is read-only. "
                       "Set `enable_external_source_writes: true` in the "
                       "config to allow it.")
            return
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Source name")
            system_type = st.text_input("System type", value="OTHER")
            desc = st.text_input("Description")
            url = st.text_input("URL")
            if st.button("Register source"):
                res = register_external_source(name, system_type, desc, url)
                st.write(res)
        with c2:
            src = st.text_input("Link — source name")
            tgt = st.text_input("Link — target table (catalog.schema.table)")
            if st.button("Create lineage link"):
                res = link_source_to_table(src, tgt)
                st.write(res)


# Streamlit executes page files top-to-bottom on navigation.
main()
