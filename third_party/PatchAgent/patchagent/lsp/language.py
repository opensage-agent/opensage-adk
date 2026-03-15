from pathlib import Path
from typing import List, Optional


class LanguageServer:
    def __init__(self, source_path: Path):
        self.source_path = source_path

    def viewcode(self, path: Path, start_line: int, end_line: int) -> Optional[List[str]]:
        assert not path.is_absolute()
        real_path = self.source_path / path
        if real_path.is_file():
            with real_path.open("r", errors="ignore") as f:
                return f.readlines()[start_line - 1 : end_line]

        return None

    def locate_symbol(self, symbol: str) -> List[str]:
        raise NotImplementedError

    def find_definition(self, path: Path, line: int, column: int) -> List[str]:
        raise NotImplementedError

    def hover(self, path: Path, line: int, column: int) -> Optional[str]:
        raise NotImplementedError
