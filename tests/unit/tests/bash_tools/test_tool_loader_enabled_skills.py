"""Unit tests for ToolLoader enabled_skills resolution."""

from __future__ import annotations

from pathlib import Path

from opensage.agents.opensage_agent import ToolLoader


def _write_skill_md(
    path: Path,
    *,
    name: str,
    description: str,
    requires_sandbox: str | None = None,
    usage: str | None = None,
) -> None:
    requires_block = ""
    if requires_sandbox is not None:
        requires_block = "\n".join(
            [
                "## Requires Sandbox",
                "",
                requires_sandbox,
                "",
            ]
        )
    usage_block = ""
    if usage is not None:
        usage_block = "\n".join(
            [
                "## Usage",
                "",
                "```bash",
                usage,
                "```",
                "",
            ]
        )
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
                usage_block,
                requires_block,
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

    # enabled_skills=["fuzz"] loads fuzz and all nested skills under it.
    loader = ToolLoader(search_paths=[root], enabled_skills=["fuzz"])
    meta = loader.load_tools()
    assert sorted(m.get("path") for m in meta) == ["fuzz", "fuzz/run-fuzzing-campaign"]

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


def test_requires_sandbox_section_is_parsed_for_top_level_skill(tmp_path: Path) -> None:
    root = tmp_path
    (root / "fuzz").mkdir()
    _write_skill_md(
        root / "fuzz" / "SKILL.md",
        name="fuzz",
        description="Fuzz toolset",
        usage="scripts/do_fuzz.sh target 60",
        requires_sandbox="fuzz",
    )

    loader = ToolLoader(search_paths=[root], enabled_skills="all")
    meta = loader.load_tools()
    prompt_text, required_sandboxes = ToolLoader.generate_system_prompt_part(meta)

    assert "- path: /bash_tools/fuzz" in prompt_text
    assert "description: Fuzz toolset" in prompt_text
    assert "requires_sandboxes" not in prompt_text
    assert required_sandboxes == {"fuzz"}


def test_requires_sandbox_section_prevents_wrong_inferred_child_sandbox(
    tmp_path: Path,
) -> None:
    # Regression: child tool path prefix ("fuzz/...") should not override the
    # sandbox requirement documented in the SKILL.md.
    root = tmp_path
    (root / "fuzz" / "run-fuzzing-campaign").mkdir(parents=True)
    _write_skill_md(
        root / "fuzz" / "run-fuzzing-campaign" / "SKILL.md",
        name="run-fuzzing-campaign",
        description="Run fuzzing campaign",
        requires_sandbox="main",
    )

    loader = ToolLoader(
        search_paths=[root], enabled_skills=["fuzz/run-fuzzing-campaign"]
    )
    meta = loader.load_tools()
    assert [m.get("path") for m in meta] == ["fuzz/run-fuzzing-campaign"]

    prompt_text, required_sandboxes = ToolLoader.generate_system_prompt_part(meta)
    assert "- path: /bash_tools/fuzz/run-fuzzing-campaign" in prompt_text
    assert "description: Run fuzzing campaign" in prompt_text
    assert required_sandboxes == {"main"}
