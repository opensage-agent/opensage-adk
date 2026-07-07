"""Workflow specification data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union

Step = Union[
    "TaskDef",
    "WorkflowSpec",
    "SequentialSpec",
    "ParallelSpec",
    "BoundedSpec",
    "LoopSpec",
]


@dataclass
class TaskDef:
    """Single task definition (leaf node). Runs an agent or a tool."""

    id: str
    agent: str | None = None
    tool: str | None = None
    when: str | None = None
    input_from: str | None = None
    output_schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.agent is None and self.tool is None:
            raise ValueError("Task must specify agent or tool")
        if self.agent is not None and self.tool is not None:
            raise ValueError("Task cannot specify both agent and tool")


@dataclass
class SequentialSpec:
    """Run steps in order: a, then b, then c."""

    steps: tuple[Step, ...]


@dataclass
class ParallelSpec:
    """Run steps in parallel."""

    steps: tuple[Step, ...]


@dataclass
class BoundedSpec:
    """Run a single task with concurrency limit (bounded parallelism)."""

    task: TaskDef
    limit: int
    map_over: str | None = None


@dataclass
class LoopSpec:
    """Run steps (a, b, c, ...) in sequence. After each iteration, evaluate check.

    check: Expression to decide when to break. Evaluated in context where each
    step's result is available by task id. Truthy -> break, falsy -> retry.
    Examples: "review.approved", "review.result == True", "lint.passed"
    """

    steps: tuple[Step, ...]
    check: str
    max_retries: int = 3


@dataclass
class WorkflowSpec:
    """Workflow definition. Steps run sequentially. Each step can be a task or workflow."""

    name: str
    steps: tuple[Step, ...]
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def task(
    id: str,
    agent: str | None = None,
    tool: str | None = None,
    when: str | None = None,
    input_from: str | None = None,
    output_schema: dict[str, Any] | None = None,
) -> TaskDef:
    """Create a task definition. Specify agent or tool (one required)."""
    return TaskDef(
        id=id,
        agent=agent,
        tool=tool,
        when=when,
        input_from=input_from,
        output_schema=output_schema,
    )


def sequential(*steps: Step) -> SequentialSpec:
    """Run steps in order: sequential(a, b, c)."""
    return SequentialSpec(steps=steps)


def parallel(*steps: Step) -> ParallelSpec:
    """Run steps in parallel."""
    return ParallelSpec(steps=steps)


def bounded(
    task_def: TaskDef,
    limit: int,
    map_over: str | None = None,
) -> BoundedSpec:
    """Run task with concurrency limit."""
    return BoundedSpec(task=task_def, limit=limit, map_over=map_over)


def loop(
    *steps: Step,
    check: str,
    max_retries: int = 3,
) -> LoopSpec:
    """Run steps in sequence. If check is falsy, retry from first step.

        check: Expression with step results by id. Truthy -> break.
        Examples:
            loop(a, b, c, check="review.approved")
            loop(a, b, c, check="review.result == True")
            loop(a, b, check="lint.passed")

    Raises:
      ValueError: Raised when this operation fails."""
    if not steps:
        raise ValueError("loop requires at least one step")
    return LoopSpec(steps=steps, check=check, max_retries=max_retries)


def workflow(
    name: str,
    *steps: Step,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
) -> WorkflowSpec:
    """Create a workflow. Steps run sequentially. Pass tasks or workflows as steps."""
    return WorkflowSpec(
        name=name,
        steps=steps,
        inputs=inputs,
        outputs=outputs,
    )


# Examples

# 1.

workflow(
    "simple_pipeline",
    task("preprocess", agent="preprocessor"),
    task("analyze", agent="analyzer", input_from="preprocess.result"),
    task("report", agent="reporter", input_from="analyze.result"),
)

# 2.
workflow(
    "vuln_scan",
    bounded(
        task(
            "detection",
            agent="vuln_detector",
            output_schema={"has_vuln": bool, "func_id": str},
        ),
        limit=100,
        map_over="repo.functions",
    ),
)

# 3.

workflow(
    "multi_agent",
    sequential(
        task("gather", agent="gatherer"),
        parallel(
            task("analyze_a", agent="analyzer_a", input_from="gather.result"),
            task("analyze_b", agent="analyzer_b", input_from="gather.result"),
        ),
    ),
)

# 4.

detect = workflow(
    "detect",
    task("scan", agent="vuln_detector"),
)

patch = workflow(
    "patch",
    loop(
        task("draft", agent="patcher"),
        task("verify", agent="safety_checker"),
        check="verify.approved",
        max_retries=2,
    ),
)

main = workflow(
    "full_pipeline",
    detect,
    patch,
)

# 5.
workflow(
    "search_and_summarize",
    task("search", tool="google_search"),
    task("summarize", agent="summarizer", input_from="search.result"),
)

# 6. Loop with explicit check expression (step results by id)
workflow(
    "code_review",
    loop(
        task("draft", agent="code_writer"),
        task("lint", tool="linter"),
        task("review", agent="reviewer"),
        check="review.approved",
        max_retries=3,
    ),
)
