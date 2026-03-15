from pathlib import Path
from typing import List

from patchagent.lsp.clangd import ClangdServer
from patchagent.lsp.ctags import CtagsServer
from patchagent.lsp.language import LanguageServer


class HybridCServer(LanguageServer):
    def __init__(self, ctags_source_path: Path, clangd_source_path: Path):
        super().__init__(ctags_source_path)

        self.ctags = CtagsServer(ctags_source_path)
        self.clangd = ClangdServer(clangd_source_path)

    def locate_symbol(self, symbol: str) -> List[str]:
        return self.ctags.locate_symbol(symbol)

    def find_definition(self, path: Path, line: int, column: int) -> List[str]:
        return self.clangd.find_definition(path, line, column)

    def hover(self, path: Path, line: int, column: int) -> str:
        return self.clangd.hover(path, line, column)
