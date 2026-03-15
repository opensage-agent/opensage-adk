import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from patchagent.logger import logger
from patchagent.parser.cwe import CWE, CWE_DESCRIPTIONS, CWE_REPAIR_ADVICE
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport
from patchagent.parser.utils import (
    classic_simplify_and_extract_stacktraces,
    remove_ansi_escape,
)

AddressSanitizerPattern = r"(==[0-9]+==ERROR: AddressSanitizer: .*)"
LeakAddressSanitizerPattern = r"(==[0-9]+==ERROR: LeakSanitizer: detected memory leaks.*)"

cwe_pattern_map = {
    CWE.ILL: r"==[0-9]+==ERROR: AddressSanitizer: (ILL|illegal-instruction) on unknown address (0x[0-9a-f]+)*",
    CWE.ABORT: r"==[0-9]+==ERROR: AddressSanitizer: ABRT on unknown address (0x[0-9a-f]+)*",
    CWE.FPE: r"==[0-9]+==ERROR: AddressSanitizer: FPE on unknown address (0x[0-9a-f]+)*",
    CWE.Out_of_memory: r"==[0-9]+==ERROR: AddressSanitizer: out of memory: .*",
    CWE.Unknown_crash: r"==[0-9]+==ERROR: AddressSanitizer: unknown-crash on address (0x[0-9a-f]+)*",
    CWE.Allocation_size_too_big: r"==[0-9]+==ERROR: AddressSanitizer: requested allocation size (0x[0-9a-f]+) exceeds maximum supported size of (0x[0-9a-f]+)*",
    CWE.Null_dereference: r"==[0-9]+==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000[0-9a-f]+",
    CWE.Segv_on_unknown_address: r"==[0-9]+==ERROR: AddressSanitizer: SEGV on unknown address (0x[0-9a-f]+)*",
    CWE.Heap_buffer_overflow: r"==[0-9]+==ERROR: AddressSanitizer: heap-buffer-overflow on address (0x[0-9a-f]+)*",
    CWE.Stack_buffer_overflow: r"==[0-9]+==ERROR: AddressSanitizer: stack-buffer-overflow on address (0x[0-9a-f]+)*",
    CWE.Stack_buffer_underflow: r"==[0-9]+==ERROR: AddressSanitizer: stack-buffer-underflow on address (0x[0-9a-f]+)*",
    CWE.Dynamic_stack_buffer_overflow: r"==[0-9]+==ERROR: AddressSanitizer: dynamic-stack-buffer-overflow on address (0x[0-9a-f]+)*",
    CWE.Global_buffer_overflow: r"==[0-9]+==ERROR: AddressSanitizer: global-buffer-overflow on address (0x[0-9a-f]+)*",
    CWE.Container_overflow: r"==[0-9]+==ERROR: AddressSanitizer: container-overflow on address (0x[0-9a-f]+)*",
    CWE.Negative_size_param: r"==[0-9]+==ERROR: AddressSanitizer: negative-size-param: \(size=[-0-9]+\)*",
    CWE.Function_param_overlap: r"==[0-9]+==ERROR: AddressSanitizer: .*-param-overlap: .+",
    CWE.Stack_overflow: r"==[0-9]+==ERROR: AddressSanitizer: stack-overflow on address (0x[0-9a-f]+)*",
    CWE.Stack_use_after_return: r"==[0-9]+==ERROR: AddressSanitizer: stack-use-after-return on address (0x[0-9a-f]+)*",
    CWE.Stack_use_after_scope: r"==[0-9]+==ERROR: AddressSanitizer: stack-use-after-scope on address (0x[0-9a-f]+)*",
    CWE.Heap_double_free: r"==[0-9]+==ERROR: AddressSanitizer: attempting double-free on (0x[0-9a-f]+)*",
    CWE.Heap_use_after_free: r"==[0-9]+==ERROR: AddressSanitizer: heap-use-after-free on address (0x[0-9a-f]+)*",
    CWE.Bad_free: r"==[0-9]+==ERROR: AddressSanitizer: attempting free on address which was not malloc\(\)-ed: (0x[0-9a-f]+)*",
}

LeakAddressSanitizerPattern = r"(==[0-9]+==ERROR: LeakSanitizer: detected memory leaks.*)"


class AddressSanitizerReport(SanitizerReport):
    def __init__(
        self,
        content: str,
        cwe: CWE,
        stacktraces: List[List[Tuple[str, Path, int, int]]],
        purified_content: str,
        detect_leak: bool = False,
    ):

        if detect_leak:
            super().__init__(Sanitizer.LeakAddressSanitizer, content, cwe, stacktraces)
        else:
            super().__init__(Sanitizer.AddressSanitizer, content, cwe, stacktraces)
        self.purified_content = purified_content

    @staticmethod
    def parse(
        raw_content: str,
        source_path: Optional[Path] = None,
        work_path: Optional[Path] = None,
        detect_leak: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> Optional["AddressSanitizerReport"]:
        raw_content = remove_ansi_escape(raw_content)
        match = re.search(AddressSanitizerPattern, raw_content, re.DOTALL) or (re.search(LeakAddressSanitizerPattern, raw_content, re.DOTALL) if detect_leak else None)
        if match is None:
            return None

        content = match.group(1)
        lines = content.splitlines()
        header = lines[0]

        body = []
        for line in lines[1:]:
            if line.startswith("SCARINESS") or line.startswith("DEDUP_TOKEN"):
                continue
            if line.strip() == "AddressSanitizer can not provide additional info.":
                continue
            if line.startswith("SUMMARY:"):
                break
            body.append(line)

        search_patterns = cwe_pattern_map.copy()
        if detect_leak:
            search_patterns[CWE.Memory_Leak] = LeakAddressSanitizerPattern

        for cwe, pattern in search_patterns.items():
            if re.search(pattern, header) is not None:
                simplified, stacktraces = classic_simplify_and_extract_stacktraces(body, source_path, work_path)
                return AddressSanitizerReport(content, cwe, stacktraces, simplified, detect_leak)

        logger.error(f"[❌] Unknown AddressSanitizer report: {content}")
        return AddressSanitizerReport(content, CWE.UNKNOWN, [], content, detect_leak)

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
