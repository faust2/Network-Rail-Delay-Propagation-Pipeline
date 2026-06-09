import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_network_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load the causal propagation network tables produced by
    build_causal_propagation_network.py.
    """
    conn = sqlite3.connect(DB_PATH)

    edges = pd.read_sql_query(
        """
        SELECT
            source_train_id,
            affected_train_id,
            n_causal_events,
            n_locations,
            mean_time_gap,
            mean_post_worsening,
            mean_incremental_worsening,
            edge_weight,
            sample_location
        FROM causal_propagation_edges
        """,
        conn,
    )

    nodes = pd.read_sql_query(
        """
        SELECT
            train_id,
            node_role,
            out_degree,
            in_degree,
            out_weight,
            in_weight,
            total_weight
        FROM causal_propagation_nodes
        """,
        conn,
    )

    conn.close()
    return edges, nodes


def filter_network(
    edges: pd.DataFrame,
    nodes: pd.DataFrame,
    top_n_edges: int = 25,
    min_edge_weight: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Keep a readable subnetwork for plotting.

    You can either:
    - keep the top N strongest edges
    - optionally also require a minimum edge weight
    """
    work = edges.copy()

    work = work.sort_values(by="edge_weight", ascending=False)

    if min_edge_weight is not None:
        work = work[work["edge_weight"] >= min_edge_weight].copy()

    work = work.head(top_n_edges).copy()

    keep_trains = set(work["source_train_id"]).union(set(work["affected_train_id"]))
    node_sub = nodes[nodes["train_id"].isin(keep_trains)].copy()

    return work, node_sub


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    """
    Build a directed NetworkX graph from the causal propagation tables.
    """
    G = nx.DiGraph()

    for _, row in nodes.iterrows():
        G.add_node(
            row["train_id"],
            node_role=row["node_role"],
            total_weight=row["total_weight"],
            out_weight=row["out_weight"],
            in_weight=row["in_weight"],
        )

    for _, row in edges.iterrows():
        G.add_edge(
            row["source_train_id"],
            row["affected_train_id"],
            edge_weight=row["edge_weight"],
            n_causal_events=row["n_causal_events"],
            sample_location=row["sample_location"],
            mean_post_worsening=row["mean_post_worsening"],
        )

    return G


def role_to_marker_label(role: str) -> str:
    if role == "SOURCE_ONLY":
        return "Source"
    if role == "SINK_ONLY":
        return "Sink"
    if role == "INTERMEDIATE":
        return "Intermediate"
    return "Other"


def role_to_size(role: str, total_weight: float) -> float:
    base = {
        "SOURCE_ONLY": 900,
        "SINK_ONLY": 800,
        "INTERMEDIATE": 1000,
        "ISOLATED": 500,
    }.get(role, 500)

    return base + 80 * float(total_weight)


def draw_network(
    G: nx.DiGraph,
    output_path: Path,
    title: str = "Causal Propagation Network",
    show_edge_labels: bool = True,
) -> None:
    """
    Draw and save the directed causal propagation network.
    """
    if G.number_of_nodes() == 0 or G.number_of_edges() == 0:
        raise ValueError("Graph is empty; nothing to plot.")

    plt.figure(figsize=(16, 11))

    # spring_layout is a good default for readable network diagrams
    pos = nx.spring_layout(G, seed=42, k=1.2)

    # Group nodes by role so we can style them separately
    role_groups: dict[str, list[str]] = {}
    for node, data in G.nodes(data=True):
        role = data.get("node_role", "OTHER")
        role_groups.setdefault(role, []).append(node)

    # We are not specifying colors per the plotting constraint,
    # so roles are distinguished primarily by shape and label.
    role_to_shape = {
        "SOURCE_ONLY": "s",      # square
        "SINK_ONLY": "o",        # circle
        "INTERMEDIATE": "D",     # diamond
        "ISOLATED": "^",         # triangle
    }

    for role, node_list in role_groups.items():
        sizes = [
            role_to_size(
                G.nodes[n].get("node_role", "OTHER"),
                G.nodes[n].get("total_weight", 0),
            )
            for n in node_list
        ]

        nx.draw_networkx_nodes(
            G,
            pos,
            nodelist=node_list,
            node_size=sizes,
            node_shape=role_to_shape.get(role, "o"),
            alpha=0.85,
            linewidths=1.0,
            edgecolors="black",
            label=role_to_marker_label(role),
        )

    # Edge widths based on edge_weight
    edge_weights = [G[u][v].get("edge_weight", 1.0) for u, v in G.edges()]
    max_w = max(edge_weights) if edge_weights else 1.0
    edge_widths = [1.0 + 5.0 * (w / max_w) for w in edge_weights]

    nx.draw_networkx_edges(
        G,
        pos,
        width=edge_widths,
        alpha=0.6,
        arrows=True,
        arrowsize=18,
        connectionstyle="arc3,rad=0.08",
    )

    # Node labels
    nx.draw_networkx_labels(
        G,
        pos,
        font_size=8,
    )

    # Edge labels kept short: sample location only
    if show_edge_labels:
        edge_labels = {
            (u, v): G[u][v].get("sample_location", "")
            for u, v in G.edges()
        }
        nx.draw_networkx_edge_labels(
            G,
            pos,
            edge_labels=edge_labels,
            font_size=7,
            rotate=False,
        )

    plt.title(title, fontsize=15)
    plt.legend(frameon=True)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()


def print_plot_summary(edges: pd.DataFrame, nodes: pd.DataFrame, output_path: Path) -> None:
    print("\nPlot subnetwork summary:")
    print(f"Nodes plotted: {len(nodes)}")
    print(f"Edges plotted: {len(edges)}")
    print(f"Saved figure to: {output_path}")


def main() -> None:
    top_n_edges = 25
    min_edge_weight = None
    show_edge_labels = True

    edges, nodes = load_network_tables()
    sub_edges, sub_nodes = filter_network(
        edges,
        nodes,
        top_n_edges=top_n_edges,
        min_edge_weight=min_edge_weight,
    )

    if sub_edges.empty or sub_nodes.empty:
        print("No network data available to plot after filtering.")
        return

    G = build_graph(sub_edges, sub_nodes)

    output_path = OUTPUT_DIR / "causal_propagation_network.png"
    draw_network(
        G,
        output_path=output_path,
        title="Causal Delay Propagation Network (Top Edges)",
        show_edge_labels=show_edge_labels,
    )

    print_plot_summary(sub_edges, sub_nodes, output_path)


if __name__ == "__main__":
    main()