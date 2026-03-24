"""Unit tests for OpenSageAgent prompt helpers."""

from __future__ import annotations

from opensage.agents.opensage_agent import MemoryManagement, ToolLoader


def test_generate_sandbox_structure_description_neo4j_without_memory_management() -> (
    None
):
    text = ToolLoader.generate_sandbox_structure_description({"neo4j"})
    assert "Neo4j (Databases & Schemas)" in text
    assert "short_term/" in text
    assert "<agent_name>__<session_id>" in text
    assert "/mem/long_term/knowledge/knowledge.json" in text
    assert "TODO.md" in text
    assert "traj.json" in text
    assert "Shared Knowledge Schema" in text
    assert "Query long-term memory:" not in text
    assert "memory_management_agent" not in text


def test_generate_sandbox_structure_description_neo4j_with_memory_management() -> None:
    text = ToolLoader.generate_sandbox_structure_description(
        {"neo4j"}, memory_management=MemoryManagement.DATABASE
    )
    assert "Neo4j (Databases & Schemas)" in text
    assert "Querying long-term memory and short-term memory" in text
    assert "memory_management_agent" in text
