import csv
import logging
from collections import defaultdict
from pathlib import Path

import pandas as pd

from aigise.services.neo4j.client import AsyncNeo4jClient

logger = logging.getLogger(__name__)


def load_expr_calls(expr_calls_path):
    """
    Parse expr_calls.csv. Deduplicate rows and aggregate arguments for each call site,
    with args sorted by argIdx.
    """
    seen = set()  # Track unique rows to avoid duplicates
    expr_calls_dict = defaultdict(
        lambda: {
            "id": None,
            "cid": None,
            "caller_path": None,
            "args": [],
            "start_line": None,
            "end_line": None,
            "name": None,
        }
    )
    # Temporarily store arguments for each cid by argIdx
    args_per_cid = defaultdict(dict)

    with open(expr_calls_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Create a unique identifier for the entire row to detect duplicates
            row_key = tuple(row.items())
            if row_key in seen:
                continue  # Skip duplicate rows
            seen.add(row_key)

            cid = row["cid"]
            # Initialize metadata for this call site if not already present
            if expr_calls_dict[cid]["id"] is None:
                expr_calls_dict[cid]["id"] = row["id"]
                expr_calls_dict[cid]["cid"] = row["cid"]
                expr_calls_dict[cid]["caller_path"] = row.get("caller_path", "")
                expr_calls_dict[cid]["start_line"] = int(row.get("start_line", 0))
                expr_calls_dict[cid]["end_line"] = int(row.get("end_line", 0))
                expr_calls_dict[cid]["name"] = row["name"]
            # Store argument for this cid and argIdx, stripping whitespace
            argidx = int(row["argIdx"])
            args_per_cid[cid][argidx] = row["arg"].strip()

    # Aggregate arguments for each call site, sorted by argIdx
    for cid, call in expr_calls_dict.items():
        args = [args_per_cid[cid][idx] for idx in sorted(args_per_cid[cid])]
        call["args"] = args

    return list(expr_calls_dict.values())


def load_fp_accesses(fp_accesses_path):
    """
    Parse fp_accesses.csv. Splits the param string into a list.
    """
    fp_funcs = []
    with open(fp_accesses_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            param = row.get("param", "").strip()
            param_list = (
                [p.strip() for p in param.split(",") if p.strip()] if param else []
            )
            fp_funcs.append(
                {
                    "name": row["name"],
                    "callee_path": row.get("callee_path", ""),
                    "start_line": int(row.get("start_line", 0)),
                    "end_line": int(row.get("end_line", 0)),
                    "params": param_list,
                }
            )
    return fp_funcs


def param_types(params):
    """
    Extract only the type part from parameter definitions,
    stripping off any 'const' qualifiers and the variable name.
    E.g.:
      "const ngx_queue_t * s"    -> "ngx_queue_t *"
      "char const * const foo"   -> "char *"
    """
    res = []
    for p in params:
        # trim whitespace
        t = p.strip()
        # split into words
        parts = t.split()
        # drop the last word (the variable name)
        if len(parts) > 1:
            parts = parts[:-1]
        # filter out all 'const'
        parts = [w for w in parts if w != "const"]
        # re‐join
        typ = " ".join(parts)
        res.append(typ)
    return res


def match_edges(expr_calls, fp_funcs):
    """
    Match indirect function call edges between expr_calls and fp_funcs.

    For each expr_call, check if the parameter types match any function in fp_funcs.
    If matched, create an edge from the caller (expr_call) to the callee (fp_func).
    """
    matched_edges = []
    for call in expr_calls:
        call_types = call["args"]
        for func in fp_funcs:
            func_types = param_types(func["params"])
            if call_types == func_types:
                matched_edges.append(
                    {
                        "caller_path": call["caller_path"],
                        "caller_start": call["start_line"],
                        "caller_end": call["end_line"],
                        "caller_name": call["name"],
                        "callee_name": func["name"],
                        "callee_path": func["callee_path"],
                        "callee_start": func["start_line"],
                        "callee_end": func["end_line"],
                        "call_loc": call["cid"],
                        "direct": False,
                    }
                )
    return matched_edges


def load_codeql_results(out_dir: str) -> pd.DataFrame:
    out_dir = Path(out_dir)
    results_csv_path = out_dir / "results.csv"
    expr_calls_path = out_dir / "expr_calls.csv"
    fp_accesses_path = out_dir / "fp_accesses.csv"
    # 1. Load the main call graph DataFrame from direct calls
    df = pd.read_csv(results_csv_path, header=0)
    df["call_type"] = "direct"

    # 2. Load expr_calls and fp_accesses, then match indirect calls
    expr_calls = load_expr_calls(expr_calls_path)
    fp_funcs = load_fp_accesses(fp_accesses_path)
    indirect_edges = match_edges(expr_calls, fp_funcs)

    # 3. insert indirect edges into the DataFrame
    if indirect_edges:
        indirect_df = pd.DataFrame(indirect_edges)
        # Ensure columns match for concat (provide missing columns as needed)
        for col in df.columns:
            if col not in indirect_df.columns:
                indirect_df[col] = None
        indirect_df = indirect_df[df.columns]  # Reorder columns
        indirect_df["call_type"] = "maybe_indirect"

        # 4. Append the indirect edges to the main DataFrame
        df = pd.concat([df, indirect_df], ignore_index=True)

        return df


async def insert_codeql_results_to_cpg(
    n4j_client: AsyncNeo4jClient, codeql_out_dir: str
):
    df = load_codeql_results(codeql_out_dir)

    # 1. match methods
    methods = set()
    for _, row in df.iterrows():
        methods.add(
            (
                row["caller_name"],
                row["caller_path"],
                row["caller_start"],
                row["caller_end"],
            )
        )
        methods.add(
            (
                row["callee_name"],
                row["callee_path"],
                row["callee_start"],
                row["callee_end"],
            )
        )
    # method map to its id
    methods = sorted(methods)
    method_map = {
        (name, path, start, end): idx
        for idx, (name, path, start, end) in enumerate(methods)
    }
    df["caller_id"] = df.apply(
        lambda row: method_map[
            (
                row["caller_name"],
                row["caller_path"],
                row["caller_start"],
                row["caller_end"],
            )
        ],
        axis=1,
    )
    df["callee_id"] = df.apply(
        lambda row: method_map[
            (
                row["callee_name"],
                row["callee_path"],
                row["callee_start"],
                row["callee_end"],
            )
        ],
        axis=1,
    )
    rows = [
        {
            "idx": idx,
            "name": name,
            "path": path,
            "start_line": start,
            "end_line": end,
        }
        for idx, (name, path, start, end) in enumerate(methods)
    ]
    logger.info(f"Found {len(rows)} unique methods")

    cypher = """
    UNWIND $rows AS row
    OPTIONAL MATCH (m:METHOD)
    WHERE m.NAME = row.name AND
        row.path ENDS WITH m.FILENAME AND
        m.LINE_NUMBER <= row.end_line AND
        m.LINE_NUMBER_END >= row.start_line
    RETURN row.idx as idx, collect(m.id) as method_ids
    """
    results = await n4j_client.run_query(cypher, {"rows": rows})
    id_map = {}
    for res in results:
        idx = res["idx"]
        method_ids = res["method_ids"]
        if methods[idx][1].startswith("/usr"):
            continue
        if not method_ids:
            raise ValueError(f"No method matched for idx {idx}: {methods[idx]}")
        if len(method_ids) > 1:
            raise ValueError(f"Multiple methods matched for idx {idx}: {method_ids}")
        if method_ids:
            id_map[idx] = method_ids[0]

    # create edges
    rows = []  # start_id, end_id, call_type
    rename_call_type = {
        "direct": "CG_CALL",
        "maybe_indirect": "CG_MAYBE_INDIRECT_CALL",
    }
    for _, row in df.iterrows():
        caller_id = id_map.get(row["caller_id"])
        callee_id = id_map.get(row["callee_id"])
        if caller_id is None or callee_id is None:
            continue
        rows.append(
            {
                "start_id": caller_id,
                "end_id": callee_id,
                "call_type": rename_call_type[row["call_type"]],
            }
        )

    logger.info(f"Inserting {len(rows)} call graph edges")

    cypher = """
    UNWIND $rows AS row
    MATCH (a:METHOD {id: row.start_id}), (b:METHOD {id: row.end_id})
    WHERE a.id = row.start_id AND b.id = row.end_id
    CALL apoc.merge.relationship(a, row.call_type, {}, {}, b, {})
    YIELD rel
    RETURN count(rel) as rel_count
    """
    res = await n4j_client.run_query(cypher, {"rows": rows})
    logger.info(f"Created {res[0]['rel_count']} call graph edges")


async def update_joern_cpg(
    n4j_client: AsyncNeo4jClient, fix_identical_methods: bool = False
):
    # create CG_CALL edges
    cypher = """
    MATCH (a:METHOD)-[:CONTAINS]->(:CALL)-[:CALL]->(b:METHOD)
    MERGE (a)-[:CG_CALL]->(b)
    RETURN count(*) as rel_count
    """

    res = await n4j_client.run_query(cypher)
    logger.info(f"Created {res[0]['rel_count']} CG_CALL edges")

    if fix_identical_methods:
        cypher = """
        MATCH (n: METHOD {IS_EXTERNAL: true})
        MATCH (m: METHOD {IS_EXTERNAL: false})
        WHERE n.NAME = m.NAME and
            (n.SIGNATURE = "<unresolvedSignature>" or
                (n.SIGNATURE = m.SIGNATURE))
        MERGE (n)-[:MAYBE_IDENTICAL]->(m)
        MERGE (n)<-[:MAYBE_IDENTICAL]-(m)
        RETURN count(*) as rel_count
        """
        res = await n4j_client.run_query(cypher)
        logger.info(f"Created {res[0]['rel_count']} MAYBE_IDENTICAL edges")


async def import_joern_cpg(n4j_client: AsyncNeo4jClient, graphml_path: str):
    cypher = f"""
    CALL apoc.import.graphml("file:///{graphml_path}", {{readLabels: true, storeNodeIds: true}})
    YIELD nodes, relationships, properties, time
    RETURN nodes, relationships, properties, time
    """
    res = await n4j_client.run_query(cypher)
    logger.info(
        f"Imported {res[0]['nodes']} nodes, {res[0]['relationships']} relationships, {res[0]['properties']} properties, in {res[0]['time']} ms from {graphml_path}"
    )
