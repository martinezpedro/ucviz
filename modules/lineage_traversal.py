"""Backwards BFS lineage traversal over ``system.access.table_lineage``.

Starting from a target table, walk upstream (target -> source) one hop at a
time, tracking visited nodes to avoid cycles, and build a NetworkX DiGraph
whose edges point in the direction of data flow (source -> target).

The walk is **not** scoped to a catalog: ``table_lineage`` is metastore-wide,
so a Gold table in a ``*_gold`` catalog traces back through ``*_silver`` and
``*_bronze`` catalogs (customers that split medallion layers by catalog).

Layer classification is fully config-driven (admin-managed rules) so nothing is
hardcoded to a particular customer's catalog names.
"""

from __future__ import annotations

import networkx as nx

from modules.db import run_query

LAYER_EXTERNAL = "EXTERNAL"
LAYER_BRONZE = "BRONZE"
LAYER_SILVER = "SILVER"
LAYER_GOLD = "GOLD"
LAYER_CONSUMER = "CONSUMER"
LAYER_UNKNOWN = "UNKNOWN"

LAYER_LEVEL = {
    LAYER_EXTERNAL: 0,
    LAYER_BRONZE: 1,
    LAYER_SILVER: 2,
    LAYER_GOLD: 3,
    LAYER_CONSUMER: 4,
    LAYER_UNKNOWN: 2,
}


def classify_layer(full_name: str, layer_rules: list[dict] | None = None) -> str:
    """Classify a table name into a layer using admin-configured rules.

    ``layer_rules`` is an ordered list of ``{"match": substr, "layer": LAYER}``;
    the first case-insensitive substring hit wins. Falls back to UNKNOWN.
    """
    name = (full_name or "").lower()
    for rule in layer_rules or []:
        match = str(rule.get("match", "")).lower()
        if match and match in name:
            layer = str(rule.get("layer", LAYER_UNKNOWN)).upper()
            return layer if layer in LAYER_LEVEL else LAYER_UNKNOWN
    return LAYER_UNKNOWN


def list_tables(catalogs: list[str] | None = None) -> list[str]:
    """Return fully-qualified tables for the dropdown.

    When ``catalogs`` is empty, scans metastore-wide via
    ``system.information_schema.tables``; otherwise unions each catalog's own
    information schema (which only requires access to that catalog).
    """
    if catalogs:
        frames = []
        for cat in catalogs:
            safe = cat.replace("`", "")
            q = f"""
                SELECT table_catalog, table_schema, table_name
                FROM `{safe}`.information_schema.tables
                WHERE table_schema <> 'information_schema'
            """
            try:
                frames.append(run_query(q))
            except Exception:
                continue
        if not frames:
            return []
        import pandas as pd

        df = pd.concat(frames, ignore_index=True)
    else:
        q = """
            SELECT table_catalog, table_schema, table_name
            FROM system.information_schema.tables
            WHERE table_schema <> 'information_schema'
              AND table_catalog <> 'system'
        """
        df = run_query(q)

    if df.empty:
        return []
    names = (
        df["table_catalog"].astype(str)
        + "."
        + df["table_schema"].astype(str)
        + "."
        + df["table_name"].astype(str)
    )
    return sorted(names.unique().tolist())


# --------------------------------------------------------------------------- #
# Cascading table picker
#
# A metastore can hold hundreds of thousands of tables (this was verified live:
# 3.6k catalogs / 500k tables), which is far too many for a single dropdown, so
# the picker resolves catalog -> schema -> table and scopes each query.
# --------------------------------------------------------------------------- #
def list_catalogs(allowed: list[str] | None = None) -> list[str]:
    """List catalogs, optionally restricted to the admin-configured set."""
    try:
        df = run_query(
            "SELECT catalog_name FROM system.information_schema.catalogs "
            "WHERE catalog_name <> 'system' ORDER BY catalog_name"
        )
    except Exception:
        return list(allowed or [])
    if df.empty:
        return list(allowed or [])
    cats = df["catalog_name"].astype(str).tolist()
    if allowed:
        allowed_set = set(allowed)
        cats = [c for c in cats if c in allowed_set]
    return cats


def list_schemas(catalog: str) -> list[str]:
    """List schemas within a catalog (excluding information_schema)."""
    safe = (catalog or "").replace("`", "")
    if not safe:
        return []
    try:
        df = run_query(
            f"SELECT schema_name FROM `{safe}`.information_schema.schemata "
            "WHERE schema_name <> 'information_schema' ORDER BY schema_name"
        )
    except Exception:
        return []
    return df["schema_name"].astype(str).tolist() if not df.empty else []


def list_tables_in_catalog(catalog: str, limit: int = 5000) -> list[str]:
    """List fully-qualified tables across every schema of one catalog."""
    safe_c = (catalog or "").replace("`", "")
    if not safe_c:
        return []
    try:
        df = run_query(
            f"SELECT table_schema, table_name "
            f"FROM `{safe_c}`.information_schema.tables "
            "WHERE table_schema <> 'information_schema' "
            f"ORDER BY table_schema, table_name LIMIT {int(limit)}"
        )
    except Exception:
        return []
    if df.empty:
        return []
    return [f"{catalog}.{s}.{t}" for s, t in
            zip(df["table_schema"].astype(str), df["table_name"].astype(str))]


def search_tables(term: str, catalogs: list[str] | None = None,
                  limit: int = 200) -> list[str]:
    """Find tables in *any* catalog whose name contains ``term``.

    Powers the "All catalogs" picker. Optionally restricted to the
    admin-configured catalogs.
    """
    term = (term or "").strip()
    if not term:
        return []
    params = {"term": f"%{term.lower()}%"}
    cat_clause = ""
    if catalogs:
        keys = []
        for i, c in enumerate(catalogs):
            params[f"c{i}"] = c
            keys.append(f":c{i}")
        cat_clause = f" AND table_catalog IN ({', '.join(keys)})"
    try:
        df = run_query(
            "SELECT table_catalog, table_schema, table_name "
            "FROM system.information_schema.tables "
            "WHERE table_schema <> 'information_schema' "
            "AND table_catalog <> 'system' "
            "AND lower(table_name) LIKE :term" + cat_clause +
            f" ORDER BY table_catalog, table_schema, table_name LIMIT {int(limit)}",
            params,
        )
    except Exception:
        return []
    if df.empty:
        return []
    return [f"{c}.{s}.{t}" for c, s, t in zip(
        df["table_catalog"].astype(str), df["table_schema"].astype(str),
        df["table_name"].astype(str))]


def list_tables_in_schema(catalog: str, schema: str) -> list[str]:
    """List fully-qualified tables within one catalog.schema."""
    safe_c = (catalog or "").replace("`", "")
    safe_s = (schema or "").replace("'", "")
    if not (safe_c and safe_s):
        return []
    try:
        df = run_query(
            f"SELECT table_name FROM `{safe_c}`.information_schema.tables "
            f"WHERE table_schema = '{safe_s}' ORDER BY table_name"
        )
    except Exception:
        return []
    if df.empty:
        return []
    return [f"{catalog}.{schema}.{t}" for t in df["table_name"].astype(str)]


def _fetch_upstream(frontier: set[str]):
    """Return distinct source->target edges whose target is in ``frontier``.

    One query per BFS hop against ``system.access.table_lineage``. This is read
    through the SQL warehouse, which works under the app's on-behalf-of-user
    ``sql`` scope (the lineage REST API is not covered by that scope). Also
    captures external/path origins via source_path when a table source is null.
    """
    placeholders = ",".join("'" + t.replace("'", "''") + "'" for t in frontier)
    query = f"""
        SELECT
            source_table_full_name,
            MAX(source_path)  AS source_path,
            MAX(source_type)  AS source_type,
            target_table_full_name,
            MAX(entity_type)  AS entity_type,
            MAX(created_by)   AS created_by,
            MAX(event_date)   AS last_seen
        FROM system.access.table_lineage
        WHERE target_table_full_name IN ({placeholders})
          AND (source_table_full_name IS NOT NULL OR source_path IS NOT NULL)
          AND (source_table_full_name IS NULL
               OR source_table_full_name <> target_table_full_name)
          AND event_date >= current_date() - INTERVAL 90 DAYS
        GROUP BY source_table_full_name, target_table_full_name
    """
    return run_query(query)


def _fetch_downstream(frontier: set[str]):
    """Return distinct source->target edges whose *source* is in ``frontier``.

    The consumer-side mirror of ``_fetch_upstream``: given a table, find every
    table built from it (its Silver/Gold consumers). Same system table, filtered
    on ``source_table_full_name`` instead of the target.
    """
    placeholders = ",".join("'" + t.replace("'", "''") + "'" for t in frontier)
    query = f"""
        SELECT
            source_table_full_name,
            target_table_full_name,
            MAX(entity_type)  AS entity_type,
            MAX(created_by)   AS created_by,
            MAX(event_date)   AS last_seen
        FROM system.access.table_lineage
        WHERE source_table_full_name IN ({placeholders})
          AND target_table_full_name IS NOT NULL
          AND source_table_full_name <> target_table_full_name
          AND event_date >= current_date() - INTERVAL 90 DAYS
        GROUP BY source_table_full_name, target_table_full_name
    """
    return run_query(query)


# Cap on REST lineage calls per graph build (protects schema-wide mode).
_REST_BUDGET = 200

# Cap on graph size for a full (non-progressive) trace — a hub table can fan
# out to thousands of consumers; stop growing the frontier past this.
MAX_TRACE_NODES = 300

_SYSTEM_TABLE_WARNING = (
    "system.access.table_lineage could not be read by the query identity — "
    "showing real-time API lineage only (tables the app service principal "
    "cannot see are omitted). Grant SELECT on system.access.table_lineage."
)


# Parallel real-time API calls per BFS hop (HTTP only — SQL stays on the
# calling thread, which owns the reused warehouse connection).
_REST_WORKERS = 8


def _start_rest(fetch, nodes, rest_budget: int):
    """Submit ``fetch(node)`` for up to ``rest_budget`` nodes in parallel.

    Returns ``(pool, futures, remaining_budget)``; collect with
    :func:`_collect_rest` after the hop's SQL query has run, so the HTTP calls
    overlap it instead of adding to it.
    """
    from concurrent.futures import ThreadPoolExecutor

    batch = sorted(nodes)[:max(rest_budget, 0)]
    pool = ThreadPoolExecutor(max_workers=_REST_WORKERS)
    futures = [(n, pool.submit(fetch, n)) for n in batch]
    return pool, futures, rest_budget - len(batch)


def _collect_rest(pool, futures):
    """``[(node, results)]`` in submit order; a failed call yields ``[]``."""
    out = []
    for node, fut in futures:
        try:
            out.append((node, fut.result() or []))
        except Exception:
            out.append((node, []))
    pool.shutdown(wait=False)
    return out


def _fetch_edges(fetch, frontier, warnings: list):
    """Run a system-table fetch, recording (not raising) a read failure."""
    try:
        return fetch(frontier)
    except Exception:
        if _SYSTEM_TABLE_WARNING not in warnings:
            warnings.append(_SYSTEM_TABLE_WARNING)
        return None


def _walk_upstream(graph, seeds, max_hops, visited, external_nodes, path_nodes,
                   rest_budget, warnings):
    """Backwards BFS (target -> source); mutates ``graph`` and the tracking sets.

    ``visited`` gives cycle protection (a node is expanded at most once);
    ``max_hops`` bounds depth and ``MAX_TRACE_NODES`` bounds fan-out.
    Returns the remaining REST budget.
    """
    from modules.lineage_api import KIND_PATH, KIND_TABLE, get_upstreams

    frontier = set(seeds)
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        next_frontier: set[str] = set()
        visited |= frontier

        pool, futures, rest_budget = _start_rest(get_upstreams, frontier,
                                                 rest_budget)
        edges = _fetch_edges(_fetch_upstream, frontier, warnings)
        if edges is not None and not edges.empty:
            for _, r in edges.iterrows():
                target = r["target_table_full_name"]
                source = r["source_table_full_name"]
                if target is None:
                    continue
                if source is None:
                    source = r.get("source_path")
                    if not source:
                        continue
                    path_nodes.add(source)
                    graph.add_edge(source, target)
                    continue
                graph.add_edge(source, target)
                if source not in visited:
                    next_frontier.add(source)

        for target, ups in _collect_rest(pool, futures):
            for up in ups:
                source = up["name"]
                graph.add_edge(source, target)
                if up["kind"] == KIND_TABLE:
                    if source not in visited:
                        next_frontier.add(source)
                elif up["kind"] == KIND_PATH:
                    path_nodes.add(source)
                else:  # KIND_EXTERNAL
                    external_nodes[source] = up.get("system")

        if graph.number_of_nodes() >= MAX_TRACE_NODES:
            _note_truncated(warnings)
            break
        if not next_frontier:
            break
        frontier = next_frontier
    return rest_budget


def _walk_downstream(graph, seeds, max_hops, visited, external_nodes,
                     path_nodes, rest_budget, warnings):
    """Forward BFS (source -> consumer); mutates ``graph`` and tracking sets.

    Returns the remaining REST budget. Finds every table built *from* the seeds.
    """
    from modules.lineage_api import KIND_PATH, KIND_TABLE, get_downstreams

    frontier = set(seeds)
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        next_frontier: set[str] = set()
        visited |= frontier

        pool, futures, rest_budget = _start_rest(get_downstreams, frontier,
                                                 rest_budget)
        edges = _fetch_edges(_fetch_downstream, frontier, warnings)
        if edges is not None and not edges.empty:
            for _, r in edges.iterrows():
                source = r["source_table_full_name"]
                target = r["target_table_full_name"]
                if source is None or target is None:
                    continue
                graph.add_edge(source, target)
                if target not in visited:
                    next_frontier.add(target)

        for source, dns in _collect_rest(pool, futures):
            for dn in dns:
                target = dn["name"]
                graph.add_edge(source, target)
                if dn["kind"] == KIND_TABLE:
                    if target not in visited:
                        next_frontier.add(target)
                elif dn["kind"] == KIND_PATH:
                    path_nodes.add(target)
                else:  # KIND_EXTERNAL
                    external_nodes[target] = dn.get("system")

        if graph.number_of_nodes() >= MAX_TRACE_NODES:
            _note_truncated(warnings)
            break
        if not next_frontier:
            break
        frontier = next_frontier
    return rest_budget


def _note_truncated(warnings: list) -> None:
    msg = (f"Lineage trace stopped at {MAX_TRACE_NODES} tables — lower Max "
           "Lineage Hops or trace upstream only to see a smaller graph.")
    if msg not in warnings:
        warnings.append(msg)


def _annotate_nodes(graph, external_nodes, path_nodes, layer_rules) -> None:
    """Set layer / level / catalog attributes on every node."""
    for node in graph.nodes:
        if node in external_nodes:
            layer = LAYER_EXTERNAL
            graph.nodes[node]["system"] = external_nodes[node]
        elif node in path_nodes:
            layer = LAYER_EXTERNAL
        else:
            layer = classify_layer(node, layer_rules)
        graph.nodes[node]["layer"] = layer
        graph.nodes[node]["level"] = LAYER_LEVEL.get(layer, 2)
        parts = str(node).split(".")
        is_table = (len(parts) == 3 and node not in external_nodes
                    and node not in path_nodes)
        graph.nodes[node]["catalog"] = parts[0] if is_table else None


def build_lineage_graph(
    target_tables, max_hops: int = 6, layer_rules: list[dict] | None = None,
    direction: str = "both",
) -> nx.DiGraph:
    """Walk lineage from one or more targets and build a data-flow DiGraph.

    Hybrid lineage sources, merged per hop:
      * ``system.access.table_lineage`` via SQL (viewer/OBO ``sql`` scope) —
        query & job lineage, works for any catalog the viewer can read.
      * the real-time lineage REST API via the service principal — captures
        declarative-pipeline lineage (streaming tables -> materialized views)
        and registered external origins, which the system table does not record.

    ``direction`` controls the walk:
      * ``"upstream"``   — trace a product back to its sources.
      * ``"downstream"`` — find every table built from the target(s).
      * ``"both"``       — the full neighborhood (default), so a Gold pick shows
        its origins and a Bronze pick shows its Silver/Gold consumers.

    Path/file and external origins are marked EXTERNAL. ``target_tables`` may be
    a single name or a list (schema-wide mode). The walk crosses catalogs;
    every table node carries a ``catalog`` attribute, and any non-fatal issue
    (system table unreadable, size cap hit) is listed in
    ``graph.graph["warnings"]``.
    """
    if isinstance(target_tables, str):
        targets = {target_tables}
    else:
        targets = {t for t in target_tables if t}

    graph = nx.DiGraph()
    for t in targets:
        graph.add_node(t)

    external_nodes: dict[str, str | None] = {}
    path_nodes: set[str] = set()
    visited: set[str] = set()
    warnings: list[str] = []
    rest_budget = _REST_BUDGET

    if direction in ("upstream", "both"):
        rest_budget = _walk_upstream(
            graph, targets, max_hops, visited, external_nodes, path_nodes,
            rest_budget, warnings,
        )
    if direction in ("downstream", "both"):
        # Reset visited so downstream can revisit the seeds as expansion roots.
        rest_budget = _walk_downstream(
            graph, targets, max_hops, set(), external_nodes, path_nodes,
            rest_budget, warnings,
        )

    _annotate_nodes(graph, external_nodes, path_nodes, layer_rules)
    graph.graph["warnings"] = warnings
    return graph


def lineage_chain(graph: nx.DiGraph) -> list[dict]:
    """Return source->target hops ordered left-to-right by source layer."""
    chain = []
    for source, target, data in graph.edges(data=True):
        chain.append(
            {
                "source": source,
                "target": target,
                "source_layer": graph.nodes[source].get("layer", LAYER_UNKNOWN),
                "target_layer": graph.nodes[target].get("layer", LAYER_UNKNOWN),
                "created_by": data.get("created_by"),
                "last_seen": data.get("last_seen"),
            }
        )
    chain.sort(key=lambda h: LAYER_LEVEL.get(h["source_layer"], 2))
    return chain


def lineage_stats(graph: nx.DiGraph, target_tables) -> dict:
    """Compute headline counters (max upstream depth + node count).

    ``target_tables`` may be a single name or a list; depth is the deepest
    upstream path reaching any target.
    """
    if graph.number_of_nodes() == 0:
        return {"total_hops": 0, "tables_in_chain": 0}
    targets = [target_tables] if isinstance(target_tables, str) else list(
        target_tables
    )
    try:
        best = 0
        for tgt in targets:
            if tgt not in graph:
                continue
            depths = dict(nx.single_target_shortest_path_length(graph, tgt))
            if depths:
                best = max(best, max(depths.values()))
        total_hops = best
        return {"total_hops": int(total_hops),
                "tables_in_chain": graph.number_of_nodes()}
    except Exception:
        return {"total_hops": graph.number_of_edges(),
                "tables_in_chain": graph.number_of_nodes()}


# --------------------------------------------------------------------------- #
# Progressive (on-demand) expansion for the UC-style graph
# --------------------------------------------------------------------------- #
def direct_neighbors(table: str) -> dict:
    """Immediate upstream + downstream neighbors of one table (real-time REST).

    Returns ``{"upstream": [{name, kind, system}, ...], "downstream": [...]}``.
    This is the per-node fetch that powers click-to-expand; it reuses the
    lineage-tracking REST helpers (near-real-time, service-principal auth).
    """
    from modules.lineage_api import get_downstreams, get_upstreams

    try:
        ups = get_upstreams(table)
    except Exception:
        ups = []
    try:
        dns = get_downstreams(table)
    except Exception:
        dns = []
    return {"upstream": ups or [], "downstream": dns or []}


def _record_kind(item: dict, external_nodes: dict, path_nodes: set) -> None:
    from modules.lineage_api import KIND_EXTERNAL, KIND_PATH

    name = item.get("name")
    if not name:
        return
    if item.get("kind") == KIND_EXTERNAL:
        external_nodes[name] = item.get("system")
    elif item.get("kind") == KIND_PATH:
        path_nodes.add(name)


def build_visible_graph(
    root: str, expanded, neighbor_fn, layer_rules: list[dict] | None = None,
    max_nodes: int = 150,
) -> nx.DiGraph:
    """Build the progressively-expanded lineage subgraph.

    Starting at ``root``, a node's neighbors are added only when that node is in
    ``expanded`` (a set of full names); ``neighbor_fn(name)`` returns a
    :func:`direct_neighbors`-shaped dict (cached by the caller). Every visible
    node gets an ``expand`` attribute: ``'+'`` (collapsed, has hidden lineage),
    ``'-'`` (expanded), or ``''`` (leaf / external origin). ``max_nodes`` caps
    runaway expansion.
    """
    from modules.lineage_api import KIND_TABLE

    expanded = set(expanded or [])
    graph = nx.DiGraph()
    graph.add_node(root)
    external_nodes: dict[str, str | None] = {}
    path_nodes: set[str] = set()
    visited: set[str] = set()
    queue = [root]

    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        nb = neighbor_fn(node) or {}
        ups = nb.get("upstream", []) or []
        dns = nb.get("downstream", []) or []
        is_expanded = node in expanded
        if is_expanded:
            for u in ups:
                name = u.get("name")
                if not name:
                    continue
                _record_kind(u, external_nodes, path_nodes)
                graph.add_edge(name, node)
                if (u.get("kind") == KIND_TABLE and name not in visited
                        and graph.number_of_nodes() < max_nodes):
                    queue.append(name)
            for d in dns:
                name = d.get("name")
                if not name:
                    continue
                _record_kind(d, external_nodes, path_nodes)
                graph.add_edge(node, name)
                if (d.get("kind") == KIND_TABLE and name not in visited
                        and graph.number_of_nodes() < max_nodes):
                    queue.append(name)
        # Expand marker: '+' means "click to reveal this node's next hop".
        # Expanded and leaf/external nodes carry no marker.
        graph.nodes[node]["expand"] = "+" if ((ups or dns) and not is_expanded) else ""

    _annotate_nodes(graph, external_nodes, path_nodes, layer_rules)
    for node in graph.nodes:
        graph.nodes[node].setdefault("expand", "")
    return graph
