from patchagent.logger import logger
from patchagent.parser.cwe import CWE
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport


class UnknownSanitizerReport(SanitizerReport):
    def __init__(self, stdout: str, stderr: str):
        super().__init__(Sanitizer.UnknownSanitizer, "", CWE.UNKNOWN, [])
        self.stdout = stdout
        self.stderr = stderr

        logger.error(f"[❌] Unknown Sanitizer Report:\n\n===STDOUT===\n\n{self.stdout}\n\n===STDERR===\n\n{self.stderr}\n\n")

    @property
    def summary(self) -> str:
        return (
            "The sanitizer detected an unknown vulnerability. \n"
            "Here is the stdout: \n\n"
            f"{self.stdout}\n\n"
            "Here is the stderr: \n\n"
            f"{self.stderr}\n\n"
            "Please check the logs for more information."
        )
