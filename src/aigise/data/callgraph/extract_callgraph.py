# download codeql here https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz
# decompress it and copy the codeql folder to PROJECT_PATH/src/aigise/data/

import csv
import os
import subprocess
import tempfile
from collections import defaultdict
from uuid import uuid4

import pandas as pd
from neomodel import db

from aigise.config.config_dataclass import ContainerConfig
from aigise.sandbox import BaseSandbox
from aigise.session import get_aigise_session
from aigise.utils.project_info import PROJECT_PATH


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


def get_and_upload_call_graph(
    codeql_dir: str, image_name: str, build_command: str, aigise_session_id: str
):
    """
    Generate and upload call graph using the specified sandbox.

    Args:
        codeql_dir: Path to CodeQL installation directory
        image_name: Docker image name to use
        build_command: Command to build the project
    """
    sandbox = None
    sandbox_session_id = None
    try:
        sandbox, sandbox_session_id = create_sandbox_with_codeql_mount(
            codeql_dir, image_name
        )

        sandbox.run_command_in_container(
            ["bash", "/surfi/codeql/callgraph_queries/run_queries.sh", build_command]
        )

        # find all possible indirect calls by matching expr calls and function pointer accesses
        with tempfile.TemporaryDirectory() as output_subdir:
            # Retrieve and process call graph file
            results_csv_path = os.path.join(output_subdir, "results.csv")
            sandbox.copy_file_from_container("/work/results.csv", results_csv_path)

            expr_calls_path = os.path.join(output_subdir, "expr_calls.csv")
            sandbox.copy_file_from_container("/work/expr_calls.csv", expr_calls_path)

            fp_accesses_path = os.path.join(output_subdir, "fp_accesses.csv")
            sandbox.copy_file_from_container("/work/fp_accesses.csv", fp_accesses_path)

            # 1. Load the main call graph DataFrame
            df = pd.read_csv(results_csv_path, header=0)
            df["call_type"] = "direct"

            # 2. Load expr_calls and fp_accesses
            expr_calls = load_expr_calls(expr_calls_path)
            fp_funcs = load_fp_accesses(fp_accesses_path)
            indirect_edges = match_edges(expr_calls, fp_funcs)

            # 3. Convert indirect_edges to a DataFrame
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

        # Setup Neo4j connection using session-based approach
        try:
            aigise_session = get_aigise_session(aigise_session_id)
            config = aigise_session.config.get_config()
            neo4j_config = config.neo4j
            connection_string = (
                f"bolt://{neo4j_config.user}:{neo4j_config.password}@{neo4j_config.uri}"
            )
        except Exception:
            # Fallback to environment variables for backward compatibility
            user = os.getenv("NEO4J_USER")
            password = os.getenv("NEO4J_PASSWORD")
            uri_suffix = os.getenv("NEO4J_URI_SUFFIX")
            connection_string = f"bolt://{user}:{password}@{uri_suffix}"

        db.set_connection(connection_string)

        # constraints + indexes
        constraints = [
            """
            CREATE CONSTRAINT function_key IF NOT EXISTS
            FOR (f:Function) REQUIRE (f.name, f.path) IS UNIQUE
            """,
            """
            CREATE INDEX direct_calls_loc IF NOT EXISTS
            FOR ()-[r:DIRECT_CALLS]-() ON (r.call_loc)
            """,
            """
            CREATE INDEX maybe_indirect_calls_loc IF NOT EXISTS
            FOR ()-[r:MAYBE_INDIRECT_CALLS]-() ON (r.call_loc)
            """,
        ]
        for stmt in constraints:
            db.cypher_query(stmt)

        rows = df.to_dict("records")
        cypher = """
            UNWIND $rows AS row
            MERGE (caller:Function { name: row.caller_name, path: row.caller_path })
            ON CREATE SET caller.start = toInteger(row.caller_start),
                            caller.end   = toInteger(row.caller_end)
            MERGE (callee:Function { name: row.callee_name, path: row.callee_path })
            ON CREATE SET callee.start = toInteger(row.callee_start),
                            callee.end   = toInteger(row.callee_end)

            FOREACH (ignored IN CASE WHEN row.call_type = 'direct' THEN [1] ELSE [] END |
                MERGE (caller)-[r:DIRECT_CALLS]->(callee)
                ON CREATE SET r.call_loc = row.call_loc
            )

            FOREACH (ignored IN CASE WHEN row.call_type = 'maybe_indirect' THEN [1] ELSE [] END |
                MERGE (caller)-[r:MAYBE_INDIRECT_CALLS]->(callee)
                ON CREATE SET r.call_loc = row.call_loc
            )
        """
        db.cypher_query(cypher, {"rows": rows})

    except Exception as e:
        print(f"[WARN] Failed to process: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Cleanup session and its resources
        if sandbox_session_id:
            from aigise.session import cleanup_aigise_session

            cleanup_aigise_session(sandbox_session_id)
