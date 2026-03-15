import random
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Callable, Generator, List, Optional, Tuple, TypeVar

from patchagent.agent.base import BaseAgent
from patchagent.builder import Builder, PoC
from patchagent.builder.utils import BuilderProcessError, BuilderTimeoutError
from patchagent.context import Context
from patchagent.parser import SanitizerReport


class ValidationResult(Enum):
    BugFree = "Bug free"
    BugDetected = "Bug detected"

    InvalidPatchFormat = "Invalid patch format"
    BuildFailed = "Build failed"
    BuildTimeout = "Build timeout"
    ReplayFailed = "Replay failed"
    ReplayTimeout = "Replay timeout"
    FunctionTestFailed = "Function test failed"
    FunctionTestTimeout = "Function test timeout"


PoC_T = TypeVar("PoC_T", bound=PoC)


class PatchTask:
    def __init__(
        self,
        pocs: List[PoC_T],
        builder: Builder,
        log_file: Optional[Path] = None,
    ):
        self.pocs = pocs
        random.shuffle(self.pocs)

        self.builder: Builder = builder
        self.project: str = self.builder.project
        self.contexts: List[Context] = []
        self.log_file: Optional[Path] = log_file

        self._report: Optional[SanitizerReport] = None

    def initialize(self) -> Tuple[ValidationResult, str]:
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self.log_file.write_text("[]")

        try:
            self.builder.build()
        except BuilderProcessError as e:
            return ValidationResult.BuildFailed, str(e)
        except BuilderTimeoutError as e:
            return ValidationResult.BuildTimeout, str(e)

        try:
            for poc in self.pocs:
                report = self.builder.replay(poc)
                if report is not None:
                    self._report = report
                    return ValidationResult.BugDetected, report.summary
        except BuilderProcessError as e:
            return ValidationResult.ReplayFailed, str(e)
        except BuilderTimeoutError as e:
            return ValidationResult.ReplayTimeout, str(e)

        return ValidationResult.BugFree, ""

    @cached_property
    def report(self) -> SanitizerReport:
        assert self._report is not None, "Please initialize the task first"
        return self._report

    @property
    def patch(self) -> Optional[str]:
        return self.contexts[-1].patch if self.contexts else None

    @property
    def current_context(self) -> Context:
        return self.contexts[-1]

    def new_context(self) -> Context:
        context = Context(log_file=self.log_file)
        self.contexts.append(context)
        return context

    def validate(self, patch: str) -> Tuple[ValidationResult, str]:
        try:
            self.builder.check_patch(patch)
        except BuilderProcessError as e:
            return ValidationResult.InvalidPatchFormat, str(e)

        try:
            self.builder.build(patch)
        except BuilderProcessError as e:
            return ValidationResult.BuildFailed, str(e)
        except BuilderTimeoutError as e:
            return ValidationResult.BuildTimeout, str(e)

        try:
            for poc in self.pocs:
                report = self.builder.replay(poc, patch)
                if report is not None:
                    return ValidationResult.BugDetected, report.summary
        except BuilderProcessError as e:
            return ValidationResult.ReplayFailed, str(e)
        except BuilderTimeoutError as e:
            return ValidationResult.ReplayTimeout, str(e)

        try:
            self.builder.function_test(patch)
        except BuilderProcessError as e:
            return ValidationResult.FunctionTestFailed, str(e)
        except BuilderTimeoutError as e:
            return ValidationResult.FunctionTestTimeout, str(e)

        return ValidationResult.BugFree, ""

    def repair(self, agent_generator: Callable[["PatchTask"], Generator[BaseAgent, None, None]]) -> Optional[str]:
        for agent in agent_generator(self):
            if (patch := agent()) is not None:
                return patch

        return None
