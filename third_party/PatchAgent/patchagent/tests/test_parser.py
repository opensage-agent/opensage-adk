import unittest
from pathlib import Path

from patchagent.parser import Sanitizer, parse_sanitizer_report
from patchagent.parser.cwe import CWE


class TestSanitizer(unittest.TestCase):
    def test_parse_sanitizer_report(self) -> None:
        cover_error_type = set()
        sanitizer_report_dir = Path(__file__).parent / "sanitizer_reports"
        for report_txt in sanitizer_report_dir.glob("**/report.txt"):
            case_dir = report_txt.parent
            raw_report = report_txt.read_text(encoding="utf-8", errors="ignore")
            summary_txt = case_dir / "summary.txt"

            if "Stack traces of all JVM threads" in raw_report and any(pattern in raw_report for pattern in ["AddressSanitizer", "MemorySanitizer", "UndefinedBehaviorSanitizer"]):
                report = parse_sanitizer_report(raw_report, Sanitizer.JavaNativeSanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.JavaNativeSanitizer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "ERROR: LeakSanitizer" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.LeakAddressSanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.LeakAddressSanitizer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "ERROR: AddressSanitizer" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.LeakAddressSanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.LeakAddressSanitizer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "UndefinedBehaviorSanitizer" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.UndefinedBehaviorSanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.UndefinedBehaviorSanitizer
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "MemorySanitizer" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.MemorySanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.MemorySanitizer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "Java Exception" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.JazzerSanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.JazzerSanitizer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "ERROR: libFuzzer" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.LibFuzzer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.LibFuzzer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            elif "ThreadSanitizer" in raw_report:
                report = parse_sanitizer_report(raw_report, Sanitizer.ThreadSanitizer)
                assert report is not None
                assert report.cwe is not CWE.UNKNOWN
                assert report.sanitizer == Sanitizer.ThreadSanitizer
                assert len(report.stacktraces) >= 1 and len(report.stacktraces[0]) >= 1
                cover_error_type.add(report.cwe)
                assert report.summary == summary_txt.read_text()
                summary_txt.write_text(report.summary)

            else:
                assert False, f"Unknown sanitizer report: {report_txt}"

        for error_type in CWE:
            assert not (error_type not in cover_error_type and error_type is not CWE.UNKNOWN), f"Missing test case for {error_type.value} in {sanitizer_report_dir}"


if __name__ == "__main__":
    unittest.main()

# python -m patchagent.tests.test_parser
