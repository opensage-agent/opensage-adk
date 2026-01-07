"""Unit tests for ToolLoader enabled_skills resolution."""

from __future__ import annotations

from pathlib import Path

from aigise.agents.aigise_agent import ToolLoader


def _write_skill_md(path: Path, *, name: str, description: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                f"# {name}",
                "",
                description,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_enabled_skills_exact_top_level_vs_child(tmp_path: Path) -> None:
    # Layout:
    #   root/
    #     fuzz/SKILL.md                 (grouping)
    #     fuzz/run-fuzzing-campaign/
    #       SKILL.md                    (child tool)
    root = tmp_path
    fuzz_dir = root / "fuzz"
    fuzz_dir.mkdir(parents=True)
    _write_skill_md(fuzz_dir / "SKILL.md", name="fuzz", description="Fuzz toolset")

    child_dir = fuzz_dir / "run-fuzzing-campaign"
    child_dir.mkdir(parents=True)
    _write_skill_md(
        child_dir / "SKILL.md",
        name="run-fuzzing-campaign",
        description="Run fuzzing campaign",
    )

    # enabled_skills=["fuzz"] loads only top-level fuzz/SKILL.md
    loader = ToolLoader(search_paths=[root], enabled_skills=["fuzz"])
    meta = loader.load_tools()
    assert [m.get("path") for m in meta] == ["fuzz"]

    # enabled_skills=["fuzz/run-fuzzing-campaign"] loads only child SKILL.md
    loader = ToolLoader(
        search_paths=[root], enabled_skills=["fuzz/run-fuzzing-campaign"]
    )
    meta = loader.load_tools()
    assert [m.get("path") for m in meta] == ["fuzz/run-fuzzing-campaign"]


def test_enabled_skills_all_loads_top_level_only(tmp_path: Path) -> None:
    root = tmp_path

    # top-level grouping skills
    (root / "fuzz").mkdir()
    _write_skill_md(root / "fuzz" / "SKILL.md", name="fuzz", description="Fuzz toolset")

    (root / "retrieval").mkdir()
    _write_skill_md(
        root / "retrieval" / "SKILL.md",
        name="retrieval",
        description="Retrieval toolset",
    )

    # child tool should NOT be loaded by enabled_skills="all"
    (root / "fuzz" / "run-fuzzing-campaign").mkdir(parents=True)
    _write_skill_md(
        root / "fuzz" / "run-fuzzing-campaign" / "SKILL.md",
        name="run-fuzzing-campaign",
        description="Run fuzzing campaign",
    )

    loader = ToolLoader(search_paths=[root], enabled_skills="all")
    meta = loader.load_tools()
    assert sorted(m.get("path") for m in meta) == ["fuzz", "retrieval"]
