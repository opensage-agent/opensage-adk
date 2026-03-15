import unittest

from patchagent.parser.cwe import CWE, CWE_DESCRIPTIONS, CWE_REPAIR_ADVICE


class TestCWE(unittest.TestCase):
    def test_cwe(self) -> None:
        for cwe in CWE:
            assert cwe in CWE_DESCRIPTIONS, f"Missing description for CWE: {cwe}"
            assert cwe in CWE_REPAIR_ADVICE, f"Missing repair advice for CWE: {cwe}"


if __name__ == "__main__":
    unittest.main()

# python -m patchagent.tests.test_cwe
