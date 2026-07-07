"""Unit tests for OpenSageAgent prompt helpers."""

from __future__ import annotations

from opensage.agents.opensage_agent import ToolLoader


def test_generate_sandbox_structure_description_neo4j_is_memory_neutral() -> None:
    text = ToolLoader.generate_sandbox_structure_description({"neo4j"})
    # Neo4j section is included.
    assert "### Neo4j" in text
    # The `analysis` database is mentioned (only live database).
    assert "analysis" in text
    # No leftover language about removed memory subsystems.
    assert "QACache" not in text
    assert "AgentRun" not in text
    assert "history" not in text.split("Neo4j")[1].split("###")[0]
    assert "memory" not in text.split("Neo4j")[1].split("###")[0]
    # Generic file-memory clutter that was never really shown here.
    assert "File Memory Layout" not in text
    assert "Shared Knowledge Schema" not in text
    assert "memory_management_agent" not in text
