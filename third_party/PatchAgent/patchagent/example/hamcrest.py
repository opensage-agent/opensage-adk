import base64
import tempfile
from pathlib import Path

import git

from patchagent.agent.generator import agent_generator
from patchagent.builder import OSSFuzzBuilder, OSSFuzzPoC
from patchagent.parser.sanitizer import Sanitizer
from patchagent.task import PatchTask

oss_fuzz_url = "https://github.com/google/oss-fuzz.git"
oss_fuzz_commit = "26f36ff7ce9cd61856621ba197f8e8db24b15ad9"

hamcrest_url = "https://github.com/hamcrest/JavaHamcrest.git"
hamcrest_commit = "3d58e993a5d12e65ec1309497cf8fab4bf5f3645"

poc_base64 = """
XE1cXVxNXEVcXVxFXKpcqVyYXF1cTVyXXF1cTlxNXF1cDVxFXF1cRVyqXChcmFxdXE1cmFxdXE1c
TVxdXE1cTVyYXF1cTVxNXF1cTVxFXF1cRVyqXKpcmFwdXBFcalyOXE1cX1xNXF1cTVwpXC1tLVwt
bS1cLW0tfjIiLWdsb3NlU28pLS+FoPOBAt1cPOKArgkAQvOgXERbXKucXV20pV1FJ1VdXdwlRZ1d
XFUjuuFd86CBvKtdYFyZ//////9c//P/XP9sdyNd9lz/cVx4XHE=
"""

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        oss_fuzz_path = tmppath / "oss-fuzz"
        source_path = tmppath / "source"
        poc_path = tmppath / "poc.bin"

        print(f"[🔍] POC Path: {poc_path}")
        poc_path.write_bytes(base64.b64decode(poc_base64))

        print(f"[🔍] OSSFuzz Path: {oss_fuzz_path}")
        oss_fuzz_repo = git.Repo.clone_from(oss_fuzz_url, oss_fuzz_path)
        oss_fuzz_repo.git.checkout(oss_fuzz_commit)

        print(f"[🔍] Source Path: {source_path}")
        source_repo = git.Repo.clone_from(hamcrest_url, source_path)
        source_repo.git.checkout(hamcrest_commit)

        patchtask = PatchTask(
            [OSSFuzzPoC(poc_path, "HamcrestFuzzer")],
            OSSFuzzBuilder(
                "hamcrest",
                source_path,
                oss_fuzz_path,
                [Sanitizer.JazzerSanitizer],
            ),
        )

        patchtask.initialize()
        print(f"Patch: {patchtask.repair(agent_generator())}")

# set -a; source .env; set +a;
# python -m patchagent.example.hamcrest
