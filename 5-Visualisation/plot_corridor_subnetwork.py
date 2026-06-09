import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

DB_PATH = "data/railway.db"
OUTPUT_DIR = Path("outputs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_corridor_edges(corridor_locations: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load causal propagation edges whose sample location lies in a chosen corridor,
    and then load the corresponding node metadata.
    """
    conn = sqlite3.connect(DB_PATH)

    placeholders = ",".join(["?"] * len(corridor_locations))
    edge_query = f"""
    SELECT
        source_train_id,
        affected_train_id,
        edge_weight,
        n_causal_events,
        mean_post_worsening,
        sample_location
    FROM causal_propagation_edges
    WHERE sample_location IN ({placeholders})
    ORDER BY edge_weight DESC;
    """
    edges = pd.read_sql_query(edge_query, conn, params=corridor_locations)

    node_ids = set(edges["source_train_id"]).union(set(edges["affected_train_id"]))
    if not node_ids:
        conn.close()
        return edges, pd.DataFrame()

    node_placeholders = ",".join(["?"] * len(node_ids))
    node_query = f"""
    SELECT
        train_id,
        node_role,
        total_weight
    FROM causal_propagation_nodes
    WHERE train_id IN ({node_placeholders});
    """
    nodes = pd.read_sql_query(node_query, conn, params=list(node_ids))

    conn.close()
    return edges, nodes


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    """
    Build a directed NetworkX graph for the selected corridor.
    """
    G = nx.DiGraph()

    for _, row in nodes.iterrows():
        G.add_node(
            row["train_id"],
            node_role=row["node_role"],
            total_weight=row["total_weight"],
        )

    for _, row in edges.iterrows():
        G.add_edge(
            row["source_train_id"],
            row["affected_train_id"],
            edge_weight=row["edge_weight"],
            sample_location=row["sample_location"],
            n_causal_events=row["n_causal_events"],
            mean_post_worsening=row["mean_post_worsening"],
        )

    return G


def main() -> None:
    corridor_locations = [
        "STEVENAGE",
        "HITCHIN",
        "SANDY",
        "BIGGLESWADE",
        "WOOLMER GREEN JN.",
        "DIGSWELL JN.",
    ]

    top_n_edges = 20

    edges, nodes = load_corridor_edges(corridor_locations)

    if edges.empty or nodes.empty:
        print("No corridor edges found.")
        return

    edges = edges.head(top_n_edges).copy()

    keep_nodes = set(edges["source_train_id"]).union(set(edges["affected_train_id"]))
    nodes = nodes[nodes["train_id"].isin(keep_nodes)].copy()

    G = build_graph(edges, nodes)

    plt.figure(figsize=(14, 10))
    pos = nx.spring_layout(G, seed=42, k=1.0)

    role_to_shape = {
        "SOURCE_ONLY": "s",
        "SINK_ONLY": "o",
        "INTERMEDIATE": "D",
        "ISOLATED": "^",
    }

    for role, shape in role_to_shape.items():
        role_nodes = [n for n, d in G.nodes(data=True) if d.get("node_role") == role]
        if not role_nodes:
            continue

        sizes = [700 + 70 * G.nodes[n].get("total_weight", 0) for n in role_nodes]

        nx.draw_networkx_nodes(
            G,
            pos,
            nodelist=role_nodes,
            node_shape=shape,
            node_size=sizes,
            alpha=0.85,
            edgecolors="black",
            linewidths=1.0,
            label=role.replace("_", " ").title(),
        )

    edge_weights = [G[u][v]["edge_weight"] for u, v in G.edges()]
    max_w = max(edge_weights) if edge_weights else 1.0
    widths = [1.0 + 4.0 * (w / max_w) for w in edge_weights]

    nx.draw_networkx_edges(
        G,
        pos,
        width=widths,
        alpha=0.6,
        arrows=True,
        arrowsize=18,
        connectionstyle="arc3,rad=0.08",
    )

    nx.draw_networkx_labels(G, pos, font_size=8)

    edge_labels = {(u, v): G[u][v]["sample_location"] for u, v in G.edges()}
    nx.draw_networkx_edge_labels(
        G,
        pos,
        edge_labels=edge_labels,
        font_size=7,
        rotate=False,
    )

    plt.title("Corridor-Specific Causal Propagation Subnetwork")
    plt.legend(frameon=True)
    plt.axis("off")
    plt.tight_layout()

    output_path = OUTPUT_DIR / "corridor_causal_subnetwork.png"
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    print(f"Nodes plotted: {len(nodes)}")
    print(f"Edges plotted: {len(edges)}")
    print(f"Saved figure to: {output_path}")


if __name__ == "__main__":
    main()