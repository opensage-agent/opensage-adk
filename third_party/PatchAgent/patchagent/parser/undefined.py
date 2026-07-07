import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from patchagent.parser.address import AddressSanitizerReport
from patchagent.parser.cwe import CWE, CWE_DESCRIPTIONS, CWE_REPAIR_ADVICE
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport
from patchagent.parser.utils import (
    classic_simplify_and_extract_stacktraces,
    remove_ansi_escape,
)

UndefinedBehaviorPattern = r"(runtime error: .+)"


class UndefinedBehaviorSanitizerReport(SanitizerReport):
    def __init__(
        self,
        content: str,
        cwe: CWE,
        stacktraces: List[List[Tuple[str, Path, int, int]]],
        purified_content: str,
    ):

        super().__init__(Sanitizer.UndefinedBehaviorSanitizer, content, cwe, stacktraces)
        self.purified_content = purified_content

    @staticmethod
    def parse(raw_content: str, source_path: Optional[Path] = None, work_path: Optional[Path] = None, *args: Any, **kwargs: Any) -> Optional["UndefinedBehaviorSanitizerReport"]:
        fake_asan_content = raw_content.replace("UndefinedBehaviorSanitizer", "AddressSanitizer")
        asan_report = AddressSanitizerReport.parse(fake_asan_content, source_path, work_path)
        if asan_report is not None:
            ubsan_content = asan_report.content.replace("AddressSanitizer", "UndefinedBehaviorSanitizer")
            return UndefinedBehaviorSanitizerReport(
                ubsan_content,
                asan_report.cwe,
                asan_report.stacktraces,
                asan_report.purified_content,
            )

        raw_content = remove_ansi_escape(raw_content)
        match = re.search(UndefinedBehaviorPattern, raw_content, re.DOTALL)
        if match is None:
            return None

        content = match.group(1)
        body = []
        for line in content.splitlines():
            if line.startswith("SCARINESS") or line.startswith("DEDUP_TOKEN"):
                continue
            body.append(line)

        simplified, stacktraces = classic_simplify_and_extract_stacktraces(body, source_path, work_path)
        return UndefinedBehaviorSanitizerReport(content, CWE.Undefined_behavior, stacktraces, simplified)

    @property
    def summary(self) -> str:
        if self.cwe == CWE.UNKNOWN:
            return self.content

        summary = (
            f"The sanitizer detected a {self.cwe.value} vulnerability. "
            f"The explanation of the vulnerability is: {CWE_DESCRIPTIONS[self.cwe]}. "
            f"Here is the detail: \n\n{self.purified_content}\n\n"
            f"To fix this issue, follow the advice below:\n\n{CWE_REPAIR_ADVICE[self.cwe]}"
        )

        return summary
