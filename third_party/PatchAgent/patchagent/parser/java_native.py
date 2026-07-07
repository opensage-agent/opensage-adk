import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from patchagent.parser.cwe import CWE, CWE_DESCRIPTIONS, CWE_REPAIR_ADVICE
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport
from patchagent.parser.utils import (
    jvm_simplify_and_extract_stacktraces,
    remove_ansi_escape,
)

JavaNativePattern = r"SUMMARY: (AddressSanitizer|MemorySanitizer|UndefinedBehaviorSanitizer): .*(Stack traces of all JVM threads.*)Garbage collector stats"


class JavaNativeReport(SanitizerReport):
    def __init__(
        self,
        content: str,
        cwe: CWE,
        stacktraces: List[List[Tuple[str, Path, int, int]]],
        purified_content: str,
    ):
        super().__init__(Sanitizer.JavaNativeSanitizer, content, cwe, stacktraces)
        self.purified_content = purified_content

    @staticmethod
    def parse(raw_content: str, source_path: Optional[Path] = None, *args: Any, **kwargs: Any) -> Optional["JavaNativeReport"]:
        raw_content = remove_ansi_escape(raw_content)
        match = re.search(JavaNativePattern, raw_content, re.DOTALL)
        if match is None:
            return None

        content = match.group(2)
        simplified, stacktraces = jvm_simplify_and_extract_stacktraces(content.splitlines(), source_path)

        return JavaNativeReport(content, CWE.Java_native_error, stacktraces, simplified)

    @property
    def summary(self) -> str:
        summary = (
            f"The sanitizer detected a {self.cwe.value} vulnerability. "
            f"The explanation of the vulnerability is: {CWE_DESCRIPTIONS[self.cwe]}. "
            f"Here is the detail: \n\n{self.purified_content}\n\n"
            f"To fix this issue, follow the advice below:\n\n{CWE_REPAIR_ADVICE[self.cwe]}"
        )

        return summary
