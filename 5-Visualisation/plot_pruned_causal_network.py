import math
import sqlite3
from pathlib import Path

import matplotlib.cm as cm
import matplotlib.colors as colors
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_network_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(DB_PATH)

    edges = pd.read_sql_query(
        """
        SELECT
            source_train_id,
            affected_train_id,
            edge_weight,
            sample_location,
            n_causal_events,
            mean_post_worsening
        FROM causal_propagation_edges
        ORDER BY edge_weight DESC;
        """,
        conn,
    )

    nodes = pd.read_sql_query(
        """
        SELECT
            train_id,
            node_role,
            total_weight,
            out_weight,
            in_weight
        FROM causal_propagation_nodes;
        """,
        conn,
    )

    conn.close()
    return edges, nodes


def select_connected_top_edges(edges: pd.DataFrame, target_edges: int = 12) -> pd.DataFrame:
    """
    Select a connected high-weight subnetwork.

    Starts from the strongest edge, then repeatedly adds the strongest
    remaining edge that touches the current selected node set.
    """
    if edges.empty:
        return edges

    edges = edges.sort_values("edge_weight", ascending=False).reset_index(drop=True)

    selected_indices = [0]
    selected_nodes = {
        edges.loc[0, "source_train_id"],
        edges.loc[0, "affected_train_id"],
    }

    remaining = set(range(1, len(edges)))

    while len(selected_indices) < target_edges and remaining:
        best_idx = None
        best_weight = -1

        for idx in remaining:
            source = edges.loc[idx, "source_train_id"]
            target = edges.loc[idx, "affected_train_id"]
            weight = edges.loc[idx, "edge_weight"]

            touches_current_network = (
                source in selected_nodes
                or target in selected_nodes
            )

            if touches_current_network and weight > best_weight:
                best_idx = idx
                best_weight = weight

        if best_idx is None:
            break

        selected_indices.append(best_idx)
        selected_nodes.add(edges.loc[best_idx, "source_train_id"])
        selected_nodes.add(edges.loc[best_idx, "affected_train_id"])
        remaining.remove(best_idx)

    return edges.loc[selected_indices].copy()


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    keep_nodes = set(edges["source_train_id"]).union(set(edges["affected_train_id"]))
    node_sub = nodes[nodes["train_id"].isin(keep_nodes)].copy()

    G = nx.DiGraph()

    for _, row in node_sub.iterrows():
        G.add_node(
            row["train_id"],
            node_role=row["node_role"],
            total_weight=float(row["total_weight"]),
            out_weight=float(row["out_weight"]),
            in_weight=float(row["in_weight"]),
        )

    for _, row in edges.iterrows():
        G.add_edge(
            row["source_train_id"],
            row["affected_train_id"],
            edge_weight=float(row["edge_weight"]),
            sample_location=row["sample_location"],
            n_causal_events=row["n_causal_events"],
            mean_post_worsening=row["mean_post_worsening"],
        )

    return G


def make_showcase_layout(G: nx.DiGraph) -> dict:
    """
    Presentation-focused hub-and-spoke layout.

    The most central node is placed in the middle, direct neighbours are
    arranged around it, and remaining nodes are placed further outward.
    """
    centre = max(
        G.nodes(),
        key=lambda n: G.nodes[n].get("total_weight", 0) + 2 * G.degree(n),
    )

    pos = {centre: (0.0, 0.0)}

    neighbours = list(set(G.predecessors(centre)).union(set(G.successors(centre))))
    neighbours = sorted(
        neighbours,
        key=lambda n: G.nodes[n].get("total_weight", 0),
        reverse=True,
    )

    radius = 2.9

    if len(neighbours) == 1:
        angles = [0.0]
    else:
        start_angle = math.radians(205)
        end_angle = math.radians(-25)

        angles = [
            start_angle + i * (end_angle - start_angle) / (len(neighbours) - 1)
            for i in range(len(neighbours))
        ]

    for node, angle in zip(neighbours, angles):
        pos[node] = (
            radius * math.cos(angle),
            radius * math.sin(angle),
        )

    remaining = [n for n in G.nodes() if n not in pos]

    for i, node in enumerate(remaining):
        connected = list(G.predecessors(node)) + list(G.successors(node))
        anchor = next((n for n in connected if n in pos), centre)

        ax, ay = pos[anchor]

        if ax == 0 and ay == 0:
            angle = 2 * math.pi * i / max(1, len(remaining))
        else:
            angle = math.atan2(ay, ax)

        pos[node] = (
            ax + 2.0 * math.cos(angle),
            ay + 2.0 * math.sin(angle),
        )

    return pos


def separate_overlapping_nodes(
    pos: dict,
    min_distance: float = 0.85,
    iterations: int = 800,
) -> dict:
    """
    Push nodes apart after layout so markers and labels do not overlap.
    """
    nodes = list(pos.keys())
    pos = {n: list(pos[n]) for n in nodes}

    for _ in range(iterations):
        moved = False

        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                n1 = nodes[i]
                n2 = nodes[j]

                x1, y1 = pos[n1]
                x2, y2 = pos[n2]

                dx = x2 - x1
                dy = y2 - y1
                dist = (dx * dx + dy * dy) ** 0.5

                if dist == 0:
                    dx, dy = 0.01, 0.01
                    dist = (dx * dx + dy * dy) ** 0.5

                if dist < min_distance:
                    push = (min_distance - dist) / 2
                    ux = dx / dist
                    uy = dy / dist

                    pos[n1][0] -= ux * push
                    pos[n1][1] -= uy * push
                    pos[n2][0] += ux * push
                    pos[n2][1] += uy * push

                    moved = True

        if not moved:
            break

    return {n: (coords[0], coords[1]) for n, coords in pos.items()}


def role_style(role: str) -> tuple[str, str, str]:
    if role == "SOURCE_ONLY":
        return "#E76F51", "s", "Source"
    if role == "INTERMEDIATE":
        return "#457B9D", "D", "Intermediate"
    if role == "SINK_ONLY":
        return "#2A9D8F", "o", "Sink"
    return "#999999", "o", "Other"


def node_size(total_weight: float) -> float:
    return 280 + 45 * (max(total_weight, 0) ** 0.5)


def build_label_positions(pos: dict) -> dict:
    label_pos = {}

    for node, (x, y) in pos.items():
        offset_x = 0.18 if x >= 0 else -0.18
        offset_y = 0.18 if y >= 0 else -0.18
        label_pos[node] = (x + offset_x, y + offset_y)

    return label_pos


def draw_network(G: nx.DiGraph, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(18, 11))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#FAFAFA")

    pos = make_showcase_layout(G)

    pos = separate_overlapping_nodes(
        pos,
        min_distance=0.85,
        iterations=800,
    )

    for role in ["SOURCE_ONLY", "INTERMEDIATE", "SINK_ONLY", "OTHER"]:
        role_nodes = [
            n for n, d in G.nodes(data=True)
            if d.get("node_role", "OTHER") == role
        ]

        if not role_nodes:
            continue

        colour, shape, label = role_style(role)

        sizes = [
            node_size(G.nodes[n].get("total_weight", 0))
            for n in role_nodes
        ]

        nx.draw_networkx_nodes(
            G,
            pos,
            nodelist=role_nodes,
            node_color=colour,
            node_shape=shape,
            node_size=sizes,
            edgecolors="#222222",
            linewidths=1.1,
            alpha=0.92,
            label=label,
            ax=ax,
        )

    edge_weights = [
        G[u][v]["edge_weight"]
        for u, v in G.edges()
    ]

    max_weight = max(edge_weights) if edge_weights else 1

    widths = [
        1.0 + 6.0 * (
            G[u][v]["edge_weight"] / max_weight
        ) ** 2
        for u, v in G.edges()
    ]

    norm = colors.Normalize(
        vmin=min(edge_weights),
        vmax=max(edge_weights),
    )

    cmap = cm.Blues

    edge_colours = [
        cmap(norm(G[u][v]["edge_weight"]))
        for u, v in G.edges()
    ]

    nx.draw_networkx_edges(
        G,
        pos,
        width=widths,
        edge_color=edge_colours,
        alpha=0.90,
        arrows=True,
        arrowsize=24,
        arrowstyle="-|>",
        connectionstyle="arc3,rad=0.10",
        ax=ax,
    )

    sm = plt.cm.ScalarMappable(
        cmap=cmap,
        norm=norm,
    )
    sm.set_array([])

    cbar = plt.colorbar(
        sm,
        ax=ax,
        shrink=0.75,
    )

    cbar.set_label(
        "Propagation Edge Weight",
        fontsize=10,
    )

    label_pos = build_label_positions(pos)
    labels = {n: n for n in G.nodes()}

    nx.draw_networkx_labels(
        G,
        label_pos,
        labels=labels,
        font_size=7,
        font_weight="bold",
        font_color="#111111",
        bbox={
            "boxstyle": "round,pad=0.16",
            "facecolor": "white",
            "edgecolor": "#DDDDDD",
            "alpha": 0.88,
        },
        ax=ax,
    )

    strongest_edges = sorted(
        G.edges(),
        key=lambda e: G[e[0]][e[1]]["edge_weight"],
        reverse=True,
    )[:4]

    edge_labels = {
        (u, v): G[u][v].get("sample_location", "")
        for u, v in strongest_edges
    }

    nx.draw_networkx_edge_labels(
        G,
        pos,
        edge_labels=edge_labels,
        font_size=7,
        font_color="#333333",
        rotate=False,
        bbox={
            "boxstyle": "round,pad=0.18",
            "facecolor": "white",
            "edgecolor": "#DDDDDD",
            "alpha": 0.85,
        },
        ax=ax,
    )

    ax.set_title(
        "Connected Causal Delay Propagation Subnetwork",
        fontsize=18,
        fontweight="bold",
        pad=22,
    )

    ax.text(
        0.5,
        -0.04,
        "Connected high-weight propagation subnetwork. Edge width and colour indicate inferred propagation strength.",
        transform=ax.transAxes,
        ha="center",
        fontsize=10,
        color="#555555",
    )

    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.03),
        ncol=3,
        frameon=True,
        facecolor="white",
        edgecolor="#CCCCCC",
    )

    ax.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()


def main() -> None:
    target_edges = 12

    edges, nodes = load_network_tables()
    selected_edges = select_connected_top_edges(edges, target_edges=target_edges)

    if selected_edges.empty:
        print("No edges available.")
        return

    G = build_graph(selected_edges, nodes)

    output_path = OUTPUT_DIR / "connected_top_causal_edges_network_showcase_gradient.png"
    draw_network(G, output_path)

    print(f"Saved showcase connected network plot to: {output_path}")
    print(f"Nodes plotted: {G.number_of_nodes()}")
    print(f"Edges plotted: {G.number_of_edges()}")

    print("\nSelected edges:")
    print(
        selected_edges[
            [
                "source_train_id",
                "affected_train_id",
                "sample_location",
                "edge_weight",
            ]
        ]
    )


if __name__ == "__main__":
    main()