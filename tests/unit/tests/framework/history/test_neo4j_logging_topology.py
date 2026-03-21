from __future__ import annotations

from opensage.patches import neo4j_logging


def test_sync_parent_links_for_tree() -> None:
    topology = {
        "agents": [
            {"session_id": "root", "agent_name": "root_agent"},
            {"session_id": "child_a", "agent_name": "child_agent_a"},
            {"session_id": "child_b", "agent_name": "child_agent_b"},
        ],
        "calls": [
            {
                "caller_session_id": "root",
                "caller_agent_name": "root_agent",
                "callee_session_id": "child_a",
                "callee_agent_name": "child_agent_a",
                "query": "analyze A",
            },
            {
                "caller_session_id": "child_a",
                "caller_agent_name": "child_agent_a",
                "callee_session_id": "child_b",
                "callee_agent_name": "child_agent_b",
                "query": "analyze B",
            },
        ],
    }

    neo4j_logging._sync_parent_links(topology)
    by_id = {a["session_id"]: a for a in topology["agents"]}

    assert "lineage" not in by_id["root"]
    assert "lineage" not in by_id["child_a"]
    assert "lineage" not in by_id["child_b"]
    assert by_id["child_a"]["parent_session_id"] == "root"
    assert by_id["child_a"]["parent_agent_name"] == "root_agent"
    assert by_id["child_b"]["parent_session_id"] == "child_a"
    assert by_id["child_b"]["parent_agent_name"] == "child_agent_a"


def test_sync_parent_links_fallback_for_missing_parent() -> None:
    topology = {
        "agents": [
            {
                "session_id": "orphan",
                "agent_name": "orphan_agent",
                "parent_session_id": "missing_parent",
            }
        ],
        "calls": [],
    }

    neo4j_logging._sync_parent_links(topology)
    orphan = topology["agents"][0]

    assert orphan["parent_session_id"] == "missing_parent"


def test_sync_parent_links_cycle_unchanged() -> None:
    topology = {
        "agents": [
            {"session_id": "a", "agent_name": "agent_a", "parent_session_id": "b"},
            {"session_id": "b", "agent_name": "agent_b", "parent_session_id": "a"},
        ],
        "calls": [],
    }

    neo4j_logging._sync_parent_links(topology)
    by_id = {a["session_id"]: a for a in topology["agents"]}

    assert by_id["a"]["parent_session_id"] == "b"
    assert by_id["b"]["parent_session_id"] == "a"
