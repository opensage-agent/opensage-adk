import argparse

import networkx as nx


def update_graphml(graphml_path: str, output_path: str):
    graph = nx.read_graphml(graphml_path)
    # change labelV to labels
    for node in graph.nodes(data=True):
        if "labelV" in node[1]:
            node[1]["labels"] = ":" + node[1].pop("labelV")

    # change labelE to label
    for u, v, data in graph.edges(data=True):
        if "labelE" in data:
            data["label"] = data.pop("labelE")

    nx.write_graphml(graph, output_path, named_key_ids=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Update GraphML file for Neo4j import."
    )
    parser.add_argument("input", help="Path to the input GraphML file.")
    parser.add_argument("output", help="Path to the output updated GraphML file.")
    args = parser.parse_args()

    update_graphml(args.input, args.output)
