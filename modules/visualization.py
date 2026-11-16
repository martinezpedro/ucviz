"""streamlit-flow rendering of the lineage DiGraph — UC-style curved connectors.

Builds ``StreamlitFlowNode`` / ``StreamlitFlowEdge`` lists from a NetworkX lineage
graph: nodes are layer-colored with a ＋/－ expand marker; edges use ``smoothstep``
(rounded) connectors with arrowheads, so the graph reads like Catalog Explorer's
lineage. Each table node also carries a thick left stripe in its **catalog's**
color, so a lineage that crosses catalogs (bronze/silver/gold catalogs) reads
at a glance. Layout (left-to-right layering) and click handling are done by the
component in ``app.py``; this module only shapes the elements.
"""

from __future__ import annotations

from streamlit_flow.elements import StreamlitFlowEdge, StreamlitFlowNode

from modules.lineage_traversal import (
    LAYER_BRONZE,
    LAYER_CONSUMER,
    LAYER_EXTERNAL,
    LAYER_GOLD,
    LAYER_SILVER,
    LAYER_UNKNOWN,
)

LAYER_COLORS = {
    LAYER_EXTERNAL: "#ff6b6b",
    LAYER_BRONZE: "#cd7f32",
    LAYER_SILVER: "#c0c0c0",
    LAYER_GOLD: "#ffd700",
    LAYER_CONSUMER: "#4fc3f7",
    LAYER_UNKNOWN: "#8892b0",
}
# Layers whose fill is light enough to need dark node text.
_DARK_TEXT_LAYERS = {LAYER_SILVER, LAYER_GOLD, LAYER_CONSUMER}

# Fallback palette (dark). ``build_flow_elements`` takes a live palette
# (ui.current_palette()) so colors track the theme.
_DEFAULT_GRAPH = {
    "GRAPH_BG": "#16213e", "ACCENT": "#4fc3f7", "EDGE": "#7c8ac7",
    "NODE_TEXT": "#e6e9f2",
}

_MARK = {"+": "＋ ", "-": "－ ", "": ""}

# Catalog stripe colors — distinct from the layer fills so the two encodings
# don't collide. Assigned in sorted-catalog order, cycling past eight.
CATALOG_PALETTE = ["#4fc3f7", "#b388ff", "#00e5a0", "#ff8a65", "#f06292",
                   "#aed581", "#4dd0e1", "#ba68c8"]


def catalog_colors(graph) -> dict:
    """``{catalog: color}`` for every catalog present in the graph."""
    cats = sorted({graph.nodes[n].get("catalog") for n in graph.nodes} - {None})
    return {c: CATALOG_PALETTE[i % len(CATALOG_PALETTE)]
            for i, c in enumerate(cats)}


def _short_label(full_name: str) -> str:
    return full_name.split(".")[-1] if full_name else full_name


def build_flow_elements(graph, focus=None, palette=None):
    """Return ``(nodes, edges)`` as streamlit-flow elements for the lineage graph.

    ``focus`` is a set holding the currently-inspected node (accent ring). Each
    node's ``expand`` graph attribute drives the ＋ (collapsed) / － (expanded)
    marker prefixed to its label.
    """
    p = {**_DEFAULT_GRAPH, **(palette or {})}
    accent = p["ACCENT"]
    edge_color = p["EDGE"]
    focus = focus or set()
    cat_colors = catalog_colors(graph)

    # Deterministic layered positions: x by medallion level, y stacked within a
    # level (centered). Paired with ManualLayout so nodes never pile up at (0,0).
    by_level: dict[int, list] = {}
    for n in graph.nodes:
        lvl = graph.nodes[n].get("level", 2)
        by_level.setdefault(lvl, []).append(n)
    pos_map = {}
    for lvl, ns in by_level.items():
        # Group same-catalog nodes together within a layer column.
        ns = sorted(ns, key=lambda n: (graph.nodes[n].get("catalog") or "", n))
        offset = (len(ns) - 1) / 2.0
        for i, n in enumerate(ns):
            pos_map[n] = (lvl * 340.0, (i - offset) * 110.0)

    nodes = []
    for n in graph.nodes:
        layer = graph.nodes[n].get("layer", LAYER_UNKNOWN)
        color = LAYER_COLORS.get(layer, LAYER_COLORS[LAYER_UNKNOWN])
        system = graph.nodes[n].get("system")
        mark = graph.nodes[n].get("expand", "")
        short = _short_label(n)
        label = f"{system}: {short}" if system else short
        content = f"{_MARK.get(mark, '')}{label}"
        focused = n in focus
        text_color = "#10151f" if layer in _DARK_TEXT_LAYERS else "#ffffff"
        catalog = graph.nodes[n].get("catalog")
        if catalog:
            # Italic in the node's own text color (readable on every layer
            # fill); long names truncated so they don't wrap the node.
            short_cat = catalog if len(catalog) <= 24 else catalog[:23] + "…"
            content = f"{content}\n\n*{short_cat.replace('_', chr(92) + '_')}*"
        style = {
            "background": color,
            "color": text_color,
            "border": (f"3px solid {accent}" if focused
                       else "1px solid rgba(0,0,0,0.25)"),
            "borderRadius": "8px",
            "fontFamily": "monospace",
            "fontSize": "12px",
            "fontWeight": "600",
            "padding": "6px 12px",
            "width": 210,
            "boxShadow": (f"0 0 0 3px {accent}55" if focused else "none"),
        }
        if catalog:
            style["borderLeft"] = f"8px solid {cat_colors[catalog]}"
        nodes.append(StreamlitFlowNode(
            id=n, pos=pos_map.get(n, (0, 0)), data={"content": content},
            node_type="default", source_position="right",
            target_position="left", style=style, draggable=True,
        ))

    edges = []
    for s, t in graph.edges:
        lit = s in focus or t in focus
        ecolor = accent if lit else edge_color
        edges.append(StreamlitFlowEdge(
            id=f"{s}->{t}", source=s, target=t, edge_type="smoothstep",
            animated=lit,
            marker_end={"type": "arrowclosed", "color": ecolor,
                        "width": 16, "height": 16},
            style={"stroke": ecolor, "strokeWidth": 2},
        ))
    return nodes, edges
