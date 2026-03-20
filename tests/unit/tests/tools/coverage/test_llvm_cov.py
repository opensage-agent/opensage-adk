import tarfile
from pathlib import Path

import pytest

from opensage.toolbox.coverage.llvm_cov import parse_llvm_coverage_json
from opensage.utils.project_info import PROJECT_PATH


@pytest.fixture
def cov_data_dir(tmp_path_factory):
    data_archive = PROJECT_PATH / "tests/unit/data/ossfuzz/cov.tar.gz"
    tmp_path = tmp_path_factory.mktemp("cov_data")
    with tarfile.open(data_archive, "r:gz") as tar:
        tar.extractall(path=tmp_path, filter="data")
    return tmp_path


def test_parse_llvm_coverage_json(cov_data_dir):
    for json_file in (cov_data_dir / "cov-jsons").glob("*.json"):
        with open(json_file, "rb") as f:
            data = f.read()
        coverage = parse_llvm_coverage_json(data)
        assert coverage is not None
