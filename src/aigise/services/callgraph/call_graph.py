# download codeql here https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz
# decompress it and copy callgraph_queries to the codeql directory

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
from neomodel import db

from aigise.sandbox import BaseSandbox
from aigise.sandbox.docker_config import DockerConfig
from aigise.sandbox_manager import SandboxManager
from aigise.utils.project_info import PROJECT_PATH


def restart_neo4j() -> str:
    """
    Invoke the restart_neo4j.sh script via bash and return its stdout.
    Raises RuntimeError on non-zero exit.
    """
    script = (
        PROJECT_PATH
        / "src"
        / "aigise"
        / "services"
        / "callgraph"
        / "callgraph_neo4j"
        / "restart_neo4j.sh"
    ).resolve()
    workdir = script.parent

    # ensure it's executable
    script.chmod(0o755)

    # run it
    result = subprocess.run(
        ["bash", str(script)], cwd=workdir, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"restart_neo4j.sh failed (code {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def create_sandbox_with_codeql_mount(codeql_dir: str, image_name: str) -> BaseSandbox:
    """
    Create a NativeDockerSandbox with CodeQL directory mounted.
    Returns the configured sandbox.
    """
    # Build DockerConfig using bind volume syntax
    cfg = DockerConfig(
        image=image_name,
        timeout=300,
        volumes=[f"{codeql_dir}:/surfi/codeql:rw"],
    )

    # Use SandboxManager with a dedicated sandbox type
    session_id = f"codeql-{random.randint(1, 1_000_000_000)}"
    sandbox = SandboxManager.get_sandbox(
        session_id=session_id,
        docker_config=cfg,
        sandbox_type="codeql",
        backend="native",
    )
    return sandbox


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


def get_and_upload_call_graph(codeql_dir: str, image_name: str, build_command: str):
    """
    Generate and upload call graph using the specified sandbox.

    Args:
        codeql_dir: Path to CodeQL installation directory
        image_name: Docker image name to use
        build_command: Command to build the project
    """
    sandbox = None
    session_id = f"codeql-{random.randint(1, 1_000_000_000)}"
    try:
        sandbox = create_sandbox_with_codeql_mount(codeql_dir, image_name)

        # Build CodeQL database with the specified build command
        build_codeql_database_command = (
            "/surfi/codeql/codeql database create /work/.surfi-codeql-database "
            "--language=cpp --overwrite --threads=$(nproc) "
            f"--command='{build_command}'"
        )
        res, exit_code = sandbox.run_command_in_container(build_codeql_database_command)
        if exit_code != 0:
            raise ValueError("Error creating codeql database")

        # Run CodeQL query to get the direct call graph
        call_graph_command = (
            "/surfi/codeql/codeql query run --database=/work/.surfi-codeql-database "
            "--output=/work/direct_callgraph.bqrs /surfi/codeql/callgraph_queries/directCalls.ql"
        )
        res, exit_code = sandbox.run_command_in_container(call_graph_command)
        if exit_code != 0:
            raise ValueError("Error creating call graph")

        # Decode the direct call graph results
        decode_call_graph_command = (
            "/surfi/codeql/codeql bqrs decode /work/direct_callgraph.bqrs "
            "--format=csv --output=/work/results.csv"
        )

        res, exit_code = sandbox.run_command_in_container(decode_call_graph_command)
        if exit_code != 0:
            raise ValueError("Error decoding call graph")

        # Construct call graph with indirect calls (determined by function signature)
        # step 1. find all function pointer accesses
        fp_command = (
            "/surfi/codeql/codeql query run --database=/work/.surfi-codeql-database "
            "--output=/work/fp_accesses.bqrs /surfi/codeql/callgraph_queries/funcPtrAccesses.ql"
        )
        res, exit_code = sandbox.run_command_in_container(fp_command)
        if exit_code != 0:
            raise ValueError("Error finding function pointer accesses")

        # Decode the function pointer accesses
        decode_fp_command = (
            "/surfi/codeql/codeql bqrs decode /work/fp_accesses.bqrs "
            "--format=csv --output=/work/fp_accesses.csv"
        )
        res, exit_code = sandbox.run_command_in_container(decode_fp_command)
        if exit_code != 0:
            raise ValueError("Error decoding function pointer accesses")

        # step 2. find all expr calls
        expr_command = (
            "/surfi/codeql/codeql query run --database=/work/.surfi-codeql-database "
            "--output=/work/expr_calls.bqrs /surfi/codeql/callgraph_queries/exprCalls.ql"
        )
        res, exit_code = sandbox.run_command_in_container(expr_command)
        if exit_code != 0:
            raise ValueError("Error finding expression calls")

        # Decode the expression calls
        decode_expr_command = (
            "/surfi/codeql/codeql bqrs decode /work/expr_calls.bqrs "
            "--format=csv --output=/work/expr_calls.csv"
        )
        res, exit_code = sandbox.run_command_in_container(decode_expr_command)
        if exit_code != 0:
            raise ValueError("Error decoding expression calls")

        # step 3. find all possible indirect calls by matching expr calls and function pointer accesses
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

        db.set_connection(
            f"bolt://{os.getenv('NEO4J_USER')}:{os.getenv('NEO4J_PASSWORD')}@{os.getenv('NEO4J_URI_SUFFIX')}"
        )

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

            CALL {
            WITH row, caller, callee
            WHERE row.call_type = 'direct'
            MERGE (caller)-[r:DIRECT_CALLS]->(callee)
            ON CREATE SET r.call_loc = row.call_loc
            }

            CALL {
            WITH row, caller, callee
            WHERE row.call_type = 'maybe_indirect'
            MERGE (caller)-[r:MAYBE_INDIRECT_CALLS]->(callee)
            ON CREATE SET r.call_loc = row.call_loc
            }
        """
        db.cypher_query(cypher, {"rows": rows})

    except Exception as e:
        print(f"[WARN] Failed to process: {e}")
        import traceback

        traceback.print_exc()
    finally:
        SandboxManager.cleanup_sandbox(session_id)
