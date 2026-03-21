"""Unit tests for OpenSageAgent prompt helpers."""

from __future__ import annotations

import pytest

from opensage.agents.opensage_agent import ToolLoader


def test_generate_sandbox_structure_description_neo4j_without_memory_management() -> (
    None
):
    text = ToolLoader.generate_sandbox_structure_description({"neo4j"})
    assert "Neo4j (Databases & Schemas)" in text
    assert "/mem/shared/" in text
    assert "knowledge.jsonl" in text
    assert "Shared Knowledge Schema" in text
    assert "Query long-term memory:" not in text
    assert "memory_management_agent" not in text


@pytest.mark.skip(reason="Skipping memory management test for now")
def test_generate_sandbox_structure_description_neo4j_with_memory_management() -> None:
    text = ToolLoader.generate_sandbox_structure_description(
        {"neo4j"}, enable_memory_management=True
    )
    assert "Neo4j (Databases & Schemas)" in text
    assert "Query long-term memory:" in text
    assert "memory_management_agent" in text
