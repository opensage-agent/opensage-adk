"""Unit tests for OpenSageAgent prompt helpers."""

from __future__ import annotations

from opensage.agents.opensage_agent import ToolLoader


def test_generate_sandbox_structure_description_neo4j_is_memory_neutral() -> None:
    text = ToolLoader.generate_sandbox_structure_description({"neo4j"})
    assert "Neo4j (Databases & Schemas)" in text
    assert "File Memory Layout" not in text
    assert "short_term/" not in text
    assert "traj.json" not in text
    assert "Shared Knowledge Schema" not in text
    assert "Query long-term memory:" not in text
    assert "memory_management_agent" not in text
