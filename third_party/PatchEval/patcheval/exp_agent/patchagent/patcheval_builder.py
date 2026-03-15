import logging
import secrets
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from patchagent.builder.builder import Builder, PoC
from patchagent.builder.utils import BuilderProcessError
from patchagent.lang import Lang
from patchagent.lsp.language import LanguageServer
from patchagent.parser.cwe import CWE
from patchagent.parser.sanitizer import Sanitizer, SanitizerReport


class PatchEvalPoC(PoC):
    """Dummy PoC placeholder to satisfy PatchTask API."""

    pass


class PatchEvalReport(SanitizerReport):
    def __init__(self, content: str):
        super().__init__(Sanitizer.UnknownSanitizer, content, CWE.UNKNOWN, [])

    @property
    def summary(self) -> str:
        return self.content


class GrepLanguageServer(LanguageServer):
    """A lightweight language server for multi-language PatchEval repositories."""

    def locate_symbol(self, symbol: str):
        if not symbol.strip():
            return []
        try:
            proc = subprocess.run(
                ["rg", "-n", "--no-heading", "-m", "20", symbol, self.source_path.as_posix()],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode not in (0, 1):
                return []
            rows = []
            prefix = self.source_path.as_posix().rstrip("/") + "/"
            for line in proc.stdout.splitlines():
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                file_path, line_no, content = parts
                rel = file_path.replace(prefix, "", 1)
                col = max(1, content.find(symbol) + 1)
                rows.append(f"{rel}:{line_no}:{col}")
            return rows[:20]
        except Exception:
            return []

    def find_definition(self, path: Path, line: int, column: int):
        return []

    def hover(self, path: Path, line: int, column: int):
        return ""


class PatchEvalBuilder(Builder):
    """PatchAgent Builder adapter for PatchEval Docker benchmark."""

    def __init__(
        self,
        cve_id: str,
        image_name: str,
        work_dir: str,
        language: str,
        problem_statement: str,
        source_path: Path,
        workspace: Optional[Path] = None,
        clean_up: bool = False,
    ):
        super().__init__(
            project=cve_id.lower(),
            source_path=source_path,
            workspace=workspace,
            clean_up=clean_up,
        )
        self.cve_id = cve_id
        self.image_name = image_name
        self.work_dir = work_dir
        self.lang_name = language
        self.problem_statement = problem_statement
        self.logger = logging.getLogger(__name__)
        self._last_unittest_result: Optional[bool] = None
        self._last_unittest_msg: str = ""

    @property
    def language(self):
        # PatchEval is mostly Python/JS/Go; use CLIKE agent path with generic tools.
        return Lang.CLIKE

    @property
    def language_server(self):
        return GrepLanguageServer(self.source_path)

    def _docker_exec(self, cmd):
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _start_container(self, patch: str = "") -> Tuple[Optional[str], str, Optional[Path]]:
        container_name = f"patcheval_{self.cve_id.lower()}_{secrets.token_hex(4)}"
        volume_args = []
        patch_file: Optional[Path] = None
        if patch:
            patch_file = self.workspace / f"{self.cve_id.lower()}_tmp.patch"
            patch_file.write_text(patch, encoding="utf-8")
            volume_args = ["-v", f"{patch_file.as_posix()}:/workspace/fix.patch:rw"]

        proc = self._docker_exec(
            ["docker", "run", "-d", "--name", container_name, "-it", *volume_args, self.image_name, "/bin/bash"]
        )
        if proc.returncode != 0:
            if patch_file and patch_file.exists():
                patch_file.unlink(missing_ok=True)
            return None, proc.stderr, None
        return container_name, "", patch_file

    def _stop_container(self, container_name: str) -> None:
        self._docker_exec(["docker", "rm", "-f", container_name])

    def _exec_in(self, container_name: str, shell_cmd: str) -> Tuple[int, str]:
        proc = self._docker_exec(["docker", "exec", container_name, "/bin/bash", "-lc", shell_cmd])
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, output

    def _run_script(self, container_name: str, script_name: str) -> Tuple[bool, str]:
        code, msg = self._exec_in(container_name, "bash prepare.sh")
        if code != 0:
            return False, f"prepare.sh failed\n{msg}"
        code, msg = self._exec_in(container_name, f"bash {script_name}")
        return code == 0, msg

    def build(self, patch: str = "") -> None:
        # PatchEval benchmark validates patches in docker runtime; no compile stage here.
        _ = patch
        return

    def _raise_replay_error(self, report: PatchEvalReport) -> BuilderProcessError:
        return BuilderProcessError(
            message="Replay failed",
            command=["bash", "fix-run.sh"],
            cwd=self.source_path,
            stdout=report.summary,
            stderr="",
        )

    def replay(self, poc: PoC, patch: str = "") -> Optional[SanitizerReport]:
        _ = poc
        container_name, err, patch_file = self._start_container(patch=patch)
        if not container_name:
            return PatchEvalReport(f"docker start failed: {err}")

        try:
            if patch:
                # Validate patched case by PoC fix script.
                ok, msg = self._run_script(container_name, "fix-run.sh")
                code, _ = self._exec_in(container_name, "ls unit_test.sh >/dev/null 2>&1")
                has_unittest = code == 0
                if has_unittest:
                    ut_ok, ut_msg = self._run_script(container_name, "unit_test.sh")
                    self._last_unittest_result = ut_ok
                    self._last_unittest_msg = ut_msg
                else:
                    self._last_unittest_result = True
                    self._last_unittest_msg = "No unit-test"

                if ok:
                    return None
                return PatchEvalReport(msg)

            # Initialize stage:
            # PatchEval's vulnerable baseline is usually encoded as a failing vul-run.sh.
            # So non-zero means vulnerability reproduced -> return report (BugDetected).
            ok, msg = self._run_script(container_name, "vul-run.sh")
            if not ok:
                return PatchEvalReport(
                    "Vulnerability is reproducible in PatchEval runtime.\n"
                    f"CVE: {self.cve_id}\n"
                    f"Language: {self.lang_name}\n"
                    f"WorkDir: {self.work_dir}\n"
                    "Task statement:\n"
                    f"{self.problem_statement}\n\n"
                    f"Runtime log:\n{msg}"
                )
            return None
        finally:
            self._stop_container(container_name)
            if patch_file and patch_file.exists():
                patch_file.unlink(missing_ok=True)

    def function_test(self, patch: str = "") -> None:
        _ = patch
        if self._last_unittest_result is False:
            raise BuilderProcessError(
                message="Function test failed",
                command=["bash", "unit_test.sh"],
                cwd=self.source_path,
                stdout=self._last_unittest_msg,
                stderr="",
            )
