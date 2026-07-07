import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from patchagent.parser.cwe import CWE
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport
from patchagent.parser.utils import (
    classic_simplify_and_extract_stacktraces,
    jvm_simplify_and_extract_stacktraces,
    remove_ansi_escape,
)

LibFuzzerPattern = r"(==\d+== ERROR: libFuzzer: .+)"


class LibFuzzerReport(SanitizerReport):
    def __init__(
        self,
        content: str,
        cwe: CWE,
        stacktraces: List[List[Tuple[str, Path, int, int]]],
        purified_content: str,
    ):

        super().__init__(Sanitizer.LibFuzzer, content, cwe, stacktraces)
        self.purified_content = purified_content

    @staticmethod
    def parse(raw_content: str, source_path: Optional[Path] = None, work_path: Optional[Path] = None, *args: Any, **kwargs: Any) -> Optional["LibFuzzerReport"]:
        raw_content = remove_ansi_escape(raw_content)
        match = re.search(LibFuzzerPattern, raw_content, re.DOTALL)
        if match is None:
            return None

        content = match.group(1)
        body = []
        for line in content.splitlines():
            if line.startswith("SCARINESS") or line.startswith("DEDUP_TOKEN"):
                continue
            if line.startswith("SUMMARY:"):
                break
            body.append(line)

        simplified, stacktraces = classic_simplify_and_extract_stacktraces(body, source_path, work_path)

        if len(stacktraces) == 0:
            simplified, stacktraces = jvm_simplify_and_extract_stacktraces(body, source_path, work_path)

        return LibFuzzerReport(content, CWE.Libfuzzer, stacktraces, simplified)

    @property
    def summary(self) -> str:
        return f"The libFuzzer report is:\n\n{self.purified_content}"
