from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

import pandas as pd

from opensage.session.neo4j_client import AsyncNeo4jClient

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
                expr_calls_dict[cid]["start_col"] = int(row.get("start_col", 0))
                expr_calls_dict[cid]["end_line"] = int(row.get("end_line", 0))
                expr_calls_dict[cid]["end_col"] = int(row.get("end_col", 0))
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
                    "start_col": int(row.get("start_col", 0)),
                    "end_line": int(row.get("end_line", 0)),
                    "end_col": int(row.get("end_col", 0)),
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
                        "caller_start_line": call["start_line"],
                        "caller_start_col": call["start_col"],
                        "caller_end_line": call["end_line"],
                        "caller_end_col": call["end_col"],
                        "caller_name": call["name"],
                        "callee_name": func["name"],
                        "callee_path": func["callee_path"],
                        "callee_start_line": func["start_line"],
                        "callee_start_col": func["start_col"],
                        "callee_end_line": func["end_line"],
                        "callee_end_col": func["end_col"],
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
    # Ensure numeric columns are consistent types to avoid sort errors
    for col in df.select_dtypes(include=["object", "float"]).columns:
        if "line" in col or "col" in col:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
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
    n4j_client: AsyncNeo4jClient,
    codeql_out_dir: str,
    create_not_found_nodes: bool = True,
):
    df = load_codeql_results(codeql_out_dir)

    def get_key(row, prefix: str) -> tuple:
        return (
            str(row[f"{prefix}_name"]),
            str(row[f"{prefix}_path"]),
            int(row[f"{prefix}_start_line"]),
            int(row[f"{prefix}_start_col"]),
            int(row[f"{prefix}_end_line"]),
            int(row[f"{prefix}_end_col"]),
        )

    # 1. match methods
    methods = set()
    for _, row in df.iterrows():
        methods.add(get_key(row, "caller"))
        methods.add(get_key(row, "callee"))
    # method map to its id
    methods = sorted(methods)
    method_map = {key: idx for idx, key in enumerate(methods)}
    df["caller_id"] = df.apply(
        lambda row: method_map[get_key(row, "caller")],
        axis=1,
    )
    df["callee_id"] = df.apply(
        lambda row: method_map[get_key(row, "callee")],
        axis=1,
    )
    rows = [
        {
            "idx": idx,
            "name": name,
            "path": path,
            "start_line": start,
            "start_col": start_col,
            "end_line": end,
            "end_col": end_col,
        }
        for idx, (name, path, start, start_col, end, end_col) in enumerate(methods)
    ]
    logger.info(f"Found {len(rows)} unique methods")

    cypher = """
    UNWIND $rows AS row
    OPTIONAL MATCH (m:METHOD)
    WHERE m.name = row.name AND
        row.path ENDS WITH m.filename AND
        m.lineNumber <= row.end_line AND
        m.lineNumberEnd >= row.start_line
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
            if not create_not_found_nodes:
                logger.warning(f"No method matched for idx {idx}: {methods[idx]}")
                continue
            # create a new method node
            new_id = f"codeql_{idx}"
            logger.debug(f"Creating missing method node: {methods[idx]}")
            create_cypher = """
            CREATE (m:METHOD)
            SET m.id = $id,
                m.name = $name,
                m.fullName = $name,
                m.filename = $path,
                m.lineNumber = $start_line,
                m.columnNumber = $start_col,
                m.lineNumberEnd = $end_line,
                m.columnNumberEnd = $end_col
            """
            await n4j_client.run_query(
                create_cypher,
                {
                    "id": new_id,
                    "name": methods[idx][0],
                    "path": methods[idx][1],
                    "start_line": methods[idx][2],
                    "start_col": methods[idx][3],
                    "end_line": methods[idx][4],
                    "end_col": methods[idx][5],
                },
            )
            id_map[idx] = new_id
            continue

        if len(method_ids) > 1:
            logger.warning(f"Multiple methods matched for idx {idx}: {method_ids}")
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
    if not rows:
        return

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
        MATCH (n: METHOD {isExternal: true})
        MATCH (m: METHOD {isExternal: false})
        WHERE n.name = m.name and
            (n.signature = "<unresolvedSignature>" or
                (n.signature = m.signature))
        MERGE (n)-[:MAYBE_IDENTICAL]->(m)
        MERGE (n)<-[:MAYBE_IDENTICAL]-(m)
        RETURN count(*) as rel_count
        """
        res = await n4j_client.run_query(cypher)
        logger.info(f"Created {res[0]['rel_count']} MAYBE_IDENTICAL edges")


# async def import_joern_cpg(n4j_client: AsyncNeo4jClient, graphml_path: str):
#     cypher = f"""
#     CALL apoc.import.graphml("file://{graphml_path}", {{readLabels: true, storeNodeIds: true}})
#     YIELD nodes, relationships, properties, time
#     RETURN nodes, relationships, properties, time
#     """
#     res = await n4j_client.run_query(cypher)
#     logger.info(
#         f"Imported {res[0]['nodes']} nodes, {res[0]['relationships']} relationships, {res[0]['properties']} properties, in {res[0]['time']} ms from {graphml_path}"
#     )


async def import_joern_callgraph(n4j_client: AsyncNeo4jClient, json_outdir: str):
    for node in ["CALL", "METHOD"]:
        res = await n4j_client.run_query(
            f"""
            CALL apoc.periodic.iterate(
            '
            // Stream the JSON array
            CALL apoc.load.json("file://{json_outdir}/{node}.json") YIELD value
            RETURN value
            ',
            '
            // For each JSON object:
            WITH value
            WITH value._label AS label,
                 toString(value._id) AS id,
                 apoc.map.removeKeys(value, ["_label", "_id"]) AS props
            WITH label, apoc.map.merge(props, {{id: id}}) AS propsWithId
            CALL apoc.create.node([label], propsWithId) YIELD node
            RETURN 1
            ',
            {{batchSize: 10000, parallel: true}}
            );
            """
        )
        logger.info(f"Imported {node} node: {res}")
        # create index on n.id
        await n4j_client.run_query(
            f"CREATE INDEX IF NOT EXISTS FOR (n:{node}) ON (n.id)"
        )
    logger.info("Waiting for indexes to come online...")
    await n4j_client.run_query("CALL db.awaitIndexes(300)")
    logger.info("All indexes are now online")
    for n, m, rel in [
        ("METHOD", "CALL", "CONTAINS"),
        ("CALL", "METHOD", "CALL"),
    ]:
        res = await n4j_client.run_query(
            f"""
            CALL apoc.periodic.iterate(
            '
            // Stream the JSON array
            CALL apoc.load.json("file://{json_outdir}/r_{n}-{m}-{rel}.json") YIELD value
            RETURN value
            ',
            '
            // For each JSON object:
            WITH value
            MATCH (a:{n} {{id: toString(value._1)}}), (b:{m} {{id: toString(value._2)}})
            CREATE (a)-[:{rel}]->(b)
            RETURN 1
            ',
            {{batchSize: 10000, parallel: true}}
            );
            """
        )
        logger.info(f"Imported {rel} relationship: {res}")
