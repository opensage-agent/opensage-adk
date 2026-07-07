from __future__ import annotations

import json
from pathlib import Path

from google.adk.sessions.session import Session

from opensage.orchestration.persistence import scan_instance_tree


def _write_instance(
    root: Path,
    relative_dir: str,
    *,
    session_id: str,
    agent_name: str,
    parent_session_id: str | None,
) -> None:
    inst_dir = root / relative_dir
    inst_dir.mkdir(parents=True)
    (inst_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "agent_name": agent_name,
                "parent_session_id": parent_session_id,
                "app_name": "app",
                "user_id": "user",
            }
        ),
        encoding="utf-8",
    )
    session = Session(
        id=session_id,
        app_name="app",
        user_id="user",
        state={},
        events=[],
    )
    (inst_dir / "traj.json").write_text(
        session.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def test_scan_instance_tree_accepts_review_instances_root(tmp_path: Path) -> None:
    instances_root = tmp_path / "copied-session" / "instances"
    _write_instance(
        instances_root,
        "root-sid",
        session_id="root-sid",
        agent_name="root_agent",
        parent_session_id=None,
    )
    _write_instance(
        instances_root,
        "root-sid/child-sid",
        session_id="child-sid",
        agent_name="worker",
        parent_session_id="root-sid",
    )

    tree = scan_instance_tree(instances_root, root_session_id="root-sid")

    assert tree["root"]["session_id"] == "root-sid"
    assert [a["session_id"] for a in tree["agents_flat"]] == [
        "root-sid",
        "child-sid",
    ]
    assert tree["agents_flat"][1]["parent_session_id"] == "root-sid"


def test_scan_instance_tree_uses_metadata_parent_for_flat_layout(
    tmp_path: Path,
) -> None:
    instances_root = tmp_path / "session" / "instances"
    _write_instance(
        instances_root,
        "root-sid",
        session_id="root-sid",
        agent_name="root_agent",
        parent_session_id=None,
    )
    _write_instance(
        instances_root,
        "child-a",
        session_id="child-a",
        agent_name="duplicate_worker_name",
        parent_session_id="root-sid",
    )
    _write_instance(
        instances_root,
        "child-b",
        session_id="child-b",
        agent_name="duplicate_worker_name",
        parent_session_id="root-sid",
    )

    tree = scan_instance_tree(instances_root, root_session_id="root-sid")

    assert [a["session_id"] for a in tree["agents_flat"]] == [
        "root-sid",
        "child-a",
        "child-b",
    ]
    assert [a["parent_session_id"] for a in tree["agents_flat"][1:]] == [
        "root-sid",
        "root-sid",
    ]
