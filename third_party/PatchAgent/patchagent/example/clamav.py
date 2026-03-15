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

clamav_url = "https://github.com/Cisco-Talos/clamav.git"
clamav_commit = "1f214b268cf4c4b034cfd8d54ae47749fff6bfeb"

poc_base64 = """
aW5jbHVkZSBhYWEkMmJlIGZvciBuewogICBydWxlIHNpdWkKe2NvbmRpdGlvbjo4XDF9TUFJTFs0
PSBzaXVpCntjbzF7CiAgIHJ1bGUgc2l1aQp7Y29uZGl0aW9uOjF8c2l1aQp7Y28kbnsKICAgcnVs
ZSBzCm8kbnsKICAgcnVsZSBzaXVpCntjb25kaXRpb246MXxzaXVpCntjbyRuewogICBydWxlIHMK
e2NvbmRpdGlvbjoxfHNpdWkKe2NvJG57CiAgIHJ1bGUgcwp7Y28xewogICBydWxlIHNpdWkKe2Nv
bmRpdGlvbjoxfHNpdWkKe2NvJG57CiAgIHJ1bGUgcwp7Y29uZGl0aW9uOjF8c2l1aQp7Y28kbnsK
ICAgcnVsZSBzCntjb25kaXRpb246MXxzaXVpCntjZSBzCntjb25kaXRpb246MXxzaXVpCntjbyRu
ewogICBydWxlIHMKe2NvbmRpdGlvbjowfHNpdWkKe2NvJG57CiAgIHJ1bGU6NnxzaXVpCntjbyRu
ewogICBydWxlIHMKe2NvbmRpdGlvbjoxfHNpdWkKe2NvJG57CiAgIHJ1bGUgcwp7Y29uZGl0aW9u
OjF8c2l1aQoofjApZSE2ODU4bnQzMiAgKH4xKWVkZTIgfHNpdWkKKH4wKWUhNjg1OG50MzIgICh+
MSllZGUyICBuKDcsMSE2ODU4bnQzMiAgKH4xKWVkaW50MTZlIHMKe2Nvc2l1aQp7Y28kbnsKICAg
cnVsZSBzCntjb25kaXRpb246MXx5aXVpCntjbyRuewogICBydWxlIHMKe2NvbmRpdGlvbjo2fHNp
dWkK
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
        source_repo = git.Repo.clone_from(clamav_url, source_path)
        source_repo.git.checkout(clamav_commit)

        patchtask = PatchTask(
            [OSSFuzzPoC(poc_path, "clamav_dbload_YARA_fuzzer")],
            OSSFuzzBuilder(
                "clamav",
                source_path,
                oss_fuzz_path,
                [Sanitizer.LeakAddressSanitizer],
            ),
        )

        patchtask.initialize()
        print(f"Patch: {patchtask.repair(agent_generator())}")

# set -a; source .env; set +a;
# python -m patchagent.example.clamav
