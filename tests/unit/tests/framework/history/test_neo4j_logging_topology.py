from __future__ import annotations

from types import SimpleNamespace

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


def test_compute_agent_mem_dir_uses_nested_session_layout() -> None:
    invocation_context = SimpleNamespace(
        session=SimpleNamespace(
            id="sess-1",
            state={"_mem_agent_dir": "/mem/short_term/Agent_Alpha__sess-1"},
        ),
        agent=SimpleNamespace(name="Agent Alpha"),
    )

    agent_mem_dir = neo4j_logging._compute_agent_mem_dir(invocation_context)

    assert agent_mem_dir == "/mem/short_term/Agent_Alpha__sess-1"


def test_compute_child_session_mem_dir_nests_under_parent() -> None:
    parent_context = SimpleNamespace(
        session=SimpleNamespace(
            id="root-session",
            state={"_mem_agent_dir": "/mem/short_term/root_agent__root-session"},
        ),
        agent=SimpleNamespace(name="root_agent"),
    )

    child_mem_dir = neo4j_logging._compute_child_session_mem_dir(
        parent_context,
        child_agent_name="child agent",
        child_session_id="child-session",
    )

    assert (
        child_mem_dir
        == "/mem/short_term/root_agent__root-session/child_agent__child-session"
    )


def test_inject_runtime_file_memory_context_adds_session_specific_block() -> None:
    agent = SimpleNamespace(
        _memory_management=SimpleNamespace(value="file"),
        instruction="Base instruction",
    )

    original_instruction = neo4j_logging._inject_runtime_file_memory_context(
        agent,
        session_id="sess-42",
        agent_mem_dir="/mem/short_term/agent__sess-42",
    )

    assert original_instruction == "Base instruction"
    assert "sess-42" in agent.instruction
    assert "/mem/short_term/agent__sess-42" in agent.instruction
    assert "traj.json" in agent.instruction
    assert "TODO.md" in agent.instruction
