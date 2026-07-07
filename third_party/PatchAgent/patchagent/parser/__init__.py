from typing import Any, Dict, Optional

from patchagent.parser.address import AddressSanitizerReport
from patchagent.parser.java_native import JavaNativeReport
from patchagent.parser.jazzer import JazzerReport
from patchagent.parser.leak import LeakAddressSanitizerReport
from patchagent.parser.libfuzzer import LibFuzzerReport
from patchagent.parser.memory import MemorySanitizerReport
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport
from patchagent.parser.thread import ThreadSanitizerReport
from patchagent.parser.undefined import UndefinedBehaviorSanitizerReport


def parse_sanitizer_report(content: str, sanitizer: Sanitizer, *args: Any, **kwargs: Any) -> Optional[SanitizerReport]:
    __sanitizer_report_classes_map__: Dict[Sanitizer, type[SanitizerReport]] = {
        Sanitizer.AddressSanitizer: AddressSanitizerReport,
        Sanitizer.LeakAddressSanitizer: LeakAddressSanitizerReport,
        Sanitizer.UndefinedBehaviorSanitizer: UndefinedBehaviorSanitizerReport,
        Sanitizer.MemorySanitizer: MemorySanitizerReport,
        Sanitizer.JazzerSanitizer: JazzerReport,
        Sanitizer.JavaNativeSanitizer: JavaNativeReport,
        Sanitizer.LibFuzzer: LibFuzzerReport,
        Sanitizer.ThreadSanitizer: ThreadSanitizerReport,
    }
    if sanitizer not in __sanitizer_report_classes_map__:
        return None

    return __sanitizer_report_classes_map__[sanitizer].parse(content, *args, **kwargs)
