import os
import shutil
import subprocess
from functools import cached_property
from hashlib import md5
from pathlib import Path
from typing import List, Optional

import pexpect
import yaml

from patchagent.builder import Builder, PoC
from patchagent.builder.utils import (
    BuilderProcessError,
    DockerUnavailableError,
    safe_subprocess_run,
)
from patchagent.lang import Lang
from patchagent.logger import logger
from patchagent.lsp.hybridc import HybridCServer
from patchagent.lsp.java import JavaLanguageServer
from patchagent.lsp.language import LanguageServer
from patchagent.parser import Sanitizer, SanitizerReport, parse_sanitizer_report
from patchagent.parser.unknown import UnknownSanitizerReport
from patchagent.utils import bear_path


class OSSFuzzPoC(PoC):
    def __init__(self, path: Path, harness_name: str):
        super().__init__()
        self.path = path
        self.harness_name = harness_name


class OSSFuzzBuilder(Builder):
    SANITIZER_MAP = {
        Sanitizer.AddressSanitizer: "address",
        Sanitizer.UndefinedBehaviorSanitizer: "undefined",
        Sanitizer.LeakAddressSanitizer: "address",
        Sanitizer.MemorySanitizer: "memory",
        # OSS-Fuzz maps Jazzer to AddressSanitizer for JVM projects
        # Reference:
        #   - https://github.com/google/oss-fuzz/blob/master/projects/hamcrest/project.yaml
        #   - https://github.com/google/oss-fuzz/blob/master/projects/apache-commons-bcel/project.yaml
        #   - https://github.com/google/oss-fuzz/blob/master/projects/threetenbp/project.yaml
        Sanitizer.JazzerSanitizer: "address",
    }

    def __init__(
        self,
        project: str,
        source_path: Path,
        fuzz_tooling_path: Path,
        sanitizers: List[Sanitizer],
        workspace: Optional[Path] = None,
        clean_up: bool = True,
        replay_poc_timeout: int = 360,
    ):
        super().__init__(project, source_path, workspace, clean_up)
        self.project = project
        self.org_fuzz_tooling_path = fuzz_tooling_path

        self.sanitizers = sanitizers
        self.replay_poc_timeout = replay_poc_timeout

    @cached_property
    def fuzz_tooling_path(self) -> Path:
        target_path = self.workspace / "immutable" / self.org_fuzz_tooling_path.name
        if not target_path.is_dir():
            shutil.copytree(self.org_fuzz_tooling_path, target_path, symlinks=True)

        return target_path

    def hash_patch(self, sanitizer: Sanitizer, patch: str) -> str:
        return f"{md5(patch.encode()).hexdigest()}-{self.SANITIZER_MAP[sanitizer]}"

    def build_finish_indicator(self, sanitizer: Sanitizer, patch: str) -> Path:
        return self.workspace / self.hash_patch(sanitizer, patch) / ".build"

    def _build_image(self, fuzz_tooling_path: Path, tries: int = 3) -> None:
        for _ in range(tries):
            process = subprocess.Popen(
                ["infra/helper.py", "build_image", "--pull", self.project],
                cwd=fuzz_tooling_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            _, stderr = process.communicate()
            if process.returncode == 0:
                return

        raise DockerUnavailableError(stderr.decode(errors="ignore"))

    def _build(self, sanitizer: Sanitizer, patch: str = "") -> None:
        if self.build_finish_indicator(sanitizer, patch).is_file():
            return

        logger.info(f"[🧱] Building {self.project} with patch {self.hash_patch(sanitizer, patch)}")
        workspace = self.workspace / self.hash_patch(sanitizer, patch)
        source_path = workspace / self.org_source_path.name
        fuzz_tooling_path = workspace / self.org_fuzz_tooling_path.name

        shutil.rmtree(workspace, ignore_errors=True)
        shutil.copytree(self.source_path, source_path, symlinks=True)
        shutil.copytree(self.fuzz_tooling_path, fuzz_tooling_path, symlinks=True)

        safe_subprocess_run(["patch", "-p1"], source_path, input=patch.encode())

        self._build_image(fuzz_tooling_path)

        safe_subprocess_run(
            [
                "infra/helper.py",
                "build_fuzzers",
                "--sanitizer",
                self.SANITIZER_MAP[sanitizer],
                "--clean",
                self.project,
                source_path,
            ],
            fuzz_tooling_path,
        )

        safe_subprocess_run(
            [
                "infra/helper.py",
                "check_build",
                "--sanitizer",
                self.SANITIZER_MAP[sanitizer],
                self.project,
            ],
            fuzz_tooling_path,
        )

        self.build_finish_indicator(sanitizer, patch).write_text(patch)

    def build(self, patch: str = "") -> None:
        for sanitizer in self.sanitizers:
            self._build(sanitizer, patch)

    def _replay(self, poc: PoC, sanitizer: Sanitizer, patch: str = "") -> Optional[SanitizerReport]:
        self._build(sanitizer, patch)

        assert isinstance(poc, OSSFuzzPoC), f"Invalid PoC type: {type(poc)}"
        assert poc.path.is_file(), "PoC file does not exist"
        assert self.build_finish_indicator(sanitizer, patch).is_file(), "Build failed"

        logger.info(f"[🔄] Replaying {self.project}/{poc.harness_name} with PoC {poc.path} and patch {self.hash_patch(sanitizer, patch)}")

        try:
            safe_subprocess_run(
                [
                    "infra/helper.py",
                    "reproduce",
                    self.project,
                    poc.harness_name,
                    poc.path,
                ],
                self.workspace / self.hash_patch(sanitizer, patch) / self.fuzz_tooling_path.name,
                timeout=self.replay_poc_timeout,
            )

            return None
        except BuilderProcessError as e:
            sanitizers: List[Sanitizer]
            match self.language:
                case Lang.CLIKE:
                    sanitizers = [sanitizer, Sanitizer.LibFuzzer]
                case Lang.JVM:
                    sanitizers = [sanitizer, Sanitizer.JavaNativeSanitizer, Sanitizer.LibFuzzer]

            for report in [e.stdout, e.stderr]:
                for sanitizer in sanitizers:
                    if (
                        san_report := parse_sanitizer_report(
                            report,
                            sanitizer,
                            source_path=self.source_path,
                        )
                    ) is not None:
                        return san_report

            # HACK: Check for Docker-related errors in the output
            for output_stream in [e.stdout, e.stderr]:
                if "docker: Error response from daemon:" in output_stream:
                    raise DockerUnavailableError(output_stream)

            return UnknownSanitizerReport(e.stdout, e.stderr)

    def replay(self, poc: PoC, patch: str = "") -> Optional[SanitizerReport]:
        for sanitizer in self.sanitizers:
            report = self._replay(poc, sanitizer, patch)
            if report is not None:
                return report

        return None

    @cached_property
    def language(self) -> Lang:
        project_yaml = self.fuzz_tooling_path / "projects" / self.project / "project.yaml"
        assert project_yaml.is_file(), "project.yaml not found"
        yaml_data = yaml.safe_load(project_yaml.read_text())
        return Lang.from_str(yaml_data.get("language", "c"))

    @cached_property
    def language_server(self) -> LanguageServer:
        match self.language:
            case Lang.CLIKE:
                return self.construct_c_language_server()
            case Lang.JVM:
                return self.construct_java_language_server()

    def _build_clangd_compile_commands(self) -> Path:
        clangd_workdir = self.workspace / "clangd"
        clangd_source = clangd_workdir / self.source_path.name
        clangd_fuzz_tooling = clangd_workdir / self.fuzz_tooling_path.name
        compile_commands = clangd_fuzz_tooling / "build" / "out" / self.project / "compile_commands.json"

        if not compile_commands.is_file():
            shutil.rmtree(clangd_workdir, ignore_errors=True)

            os.makedirs(clangd_workdir, exist_ok=True)
            shutil.copytree(self.source_path, clangd_source, symlinks=True)
            shutil.copytree(self.fuzz_tooling_path, clangd_fuzz_tooling, symlinks=True)

            logger.info("[🔋] Generating compile_commands.json")
            self._build_image(clangd_fuzz_tooling)

            shutil.copytree(bear_path(), clangd_source / ".bear", symlinks=True)

            shell = pexpect.spawn(
                "python",
                [
                    "infra/helper.py",
                    "shell",
                    self.project,
                    clangd_source.as_posix(),
                ],
                cwd=clangd_fuzz_tooling,
                timeout=None,
                codec_errors="ignore",
            )
            shell.sendline("$(find /src -name .bear | head -n 1)/bear.sh")
            shell.sendline("exit")
            shell.expect(pexpect.EOF)

            dotpwd = clangd_fuzz_tooling / "build" / "out" / self.project / ".pwd"
            if dotpwd.is_file() and compile_commands.is_file():
                workdir = dotpwd.read_text().strip()
                compile_commands.write_text(
                    compile_commands.read_text().replace(
                        workdir,
                        clangd_source.as_posix(),
                    ),
                )
            else:
                compile_commands.write_text("[]")

        assert compile_commands.is_file(), "compile_commands.json not found"
        if compile_commands.read_text(errors="ignore").strip() == "[]":
            logger.error("[❌] compile_commands.json is empty")

        target_compile_commands = clangd_source / "compile_commands.json"
        shutil.copy(compile_commands, target_compile_commands)

        return clangd_source

    def construct_c_language_server(self) -> HybridCServer:
        ctags_source = self.workspace / "ctags"
        if not ctags_source.is_dir():
            shutil.copytree(self.source_path, ctags_source, symlinks=True)

        clangd_source = self._build_clangd_compile_commands()
        return HybridCServer(ctags_source, clangd_source)

    def construct_java_language_server(self) -> JavaLanguageServer:
        return JavaLanguageServer(self.source_path)
