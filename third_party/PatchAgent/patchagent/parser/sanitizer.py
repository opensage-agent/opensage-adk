from enum import StrEnum
from pathlib import Path
from typing import Any, List, Tuple, Union

from patchagent.parser.cwe import CWE


class Sanitizer(StrEnum):
    UnknownSanitizer = "UnknownSanitizer"
    AddressSanitizer = "AddressSanitizer"
    LeakAddressSanitizer = "LeakAddressSanitizer"
    UndefinedBehaviorSanitizer = "UndefinedBehaviorSanitizer"
    ThreadSanitizer = "ThreadSanitizer"
    MemorySanitizer = "MemorySanitizer"
    JavaNativeSanitizer = "JavaNativeSanitizer"
    JazzerSanitizer = "JazzerSanitizer"
    LibFuzzer = "LibFuzzer"


class SanitizerReport:
    def __init__(
        self,
        sanitizer: Sanitizer,
        content: str,
        cwe: CWE,
        stacktraces: List[List[Tuple[str, Path, int, int]]],
    ):

        self.sanitizer: Sanitizer = sanitizer
        self.content: str = content
        self.cwe: CWE = cwe
        self.stacktraces: List[List[Tuple[str, Path, int, int]]] = stacktraces

    @property
    def summary(self) -> str:
        return self.content

    @staticmethod
    def parse(raw_content: str, *args: Any, **kwargs: Any) -> Union[None, "SanitizerReport"]:
        raise NotImplementedError("parse method must be implemented in child class")
