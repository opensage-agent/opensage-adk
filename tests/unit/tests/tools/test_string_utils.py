"""Tests for str_replace_edit multi-strategy replacer chain.

These tests verify that the replacer chain correctly handles common
LLM-generated code edit failures:
- Tab vs space mismatches
- Escape sequence issues (\\n → \n)
- Indentation variations
- Fuzzy block matching

Matching OpenCode's 9-strategy replacer chain architecture.
"""

import pytest

from opensage.toolbox.general.string_utils import (
    block_anchor_replacer,
    context_aware_replacer,
    escape_normalized_replacer,
    # Legacy direct replacer
    exact_replacer,
    get_multiple_match_info,
    indentation_flexible_replacer,
    levenshtein_distance,
    levenshtein_similarity,
    line_trimmed_replacer,
    multi_occurrence_replacer,
    replace,
    replace_with_fallback,  # Legacy API alias
    replace_with_info,
    # Generator-based replacers
    simple_replacer,
    trimmed_boundary_replacer,
    unescape_llm_output,
    whitespace_normalized_replacer,
)


class TestUnescapeLlmOutput:
    """Tests for unescape_llm_output function."""

    def test_unescape_newline(self):
        """Test unescaping \\n to actual newline."""
        assert unescape_llm_output("line1\\nline2") == "line1\nline2"

    def test_unescape_tab(self):
        """Test unescaping \\t to actual tab."""
        assert unescape_llm_output("col1\\tcol2") == "col1\tcol2"

    def test_unescape_carriage_return(self):
        """Test unescaping \\r to actual carriage return."""
        assert unescape_llm_output("text\\rmore") == "text\rmore"

    def test_unescape_quotes(self):
        """Test unescaping quote characters."""
        assert unescape_llm_output("\\'single\\'") == "'single'"
        assert unescape_llm_output('\\"double\\"') == '"double"'
        assert unescape_llm_output("\\`backtick\\`") == "`backtick`"

    def test_unescape_backslash(self):
        """Test unescaping double backslash."""
        assert unescape_llm_output("path\\\\to\\\\file") == "path\\to\\file"

    def test_no_change_for_normal_text(self):
        """Test that normal text is not modified."""
        text = "normal text without escapes"
        assert unescape_llm_output(text) == text

    def test_mixed_escapes(self):
        """Test mixed escape sequences."""
        assert unescape_llm_output("line1\\nline2\\tcolumn") == "line1\nline2\tcolumn"


class TestLevenshteinDistance:
    """Tests for Levenshtein distance calculation."""

    def test_identical_strings(self):
        """Test distance between identical strings is 0."""
        assert levenshtein_distance("hello", "hello") == 0

    def test_empty_strings(self):
        """Test distance with empty strings."""
        assert levenshtein_distance("", "") == 0
        assert levenshtein_distance("hello", "") == 5
        assert levenshtein_distance("", "world") == 5

    def test_single_insertion(self):
        """Test single character insertion."""
        assert levenshtein_distance("cat", "cats") == 1

    def test_single_deletion(self):
        """Test single character deletion."""
        assert levenshtein_distance("cats", "cat") == 1

    def test_single_substitution(self):
        """Test single character substitution."""
        assert levenshtein_distance("cat", "car") == 1

    def test_multiple_operations(self):
        """Test multiple operations."""
        assert levenshtein_distance("kitten", "sitting") == 3


class TestLevenshteinSimilarity:
    """Tests for Levenshtein similarity calculation."""

    def test_identical_strings(self):
        """Test similarity of identical strings is 1.0."""
        assert levenshtein_similarity("hello", "hello") == 1.0

    def test_completely_different(self):
        """Test similarity of completely different strings."""
        assert levenshtein_similarity("abc", "xyz") < 0.5

    def test_empty_strings(self):
        """Test similarity with empty strings."""
        assert levenshtein_similarity("", "") == 1.0
        assert levenshtein_similarity("hello", "") == 0.0
        assert levenshtein_similarity("", "world") == 0.0

    def test_similar_strings(self):
        """Test similarity of similar strings."""
        sim = levenshtein_similarity("hello", "hallo")
        assert 0.7 < sim < 0.9


class TestExactReplacer:
    """Tests for exact_replacer function."""

    def test_exact_match(self):
        """Test exact string replacement."""
        content = "Hello World"
        result = exact_replacer(content, "World", "Universe")
        assert result == "Hello Universe"

    def test_no_match(self):
        """Test when string is not found."""
        content = "Hello World"
        result = exact_replacer(content, "Foo", "Bar")
        assert result is None

    def test_multiple_matches(self):
        """Test when string appears multiple times."""
        content = "foo bar foo baz foo"
        result = exact_replacer(content, "foo", "qux")
        assert result is None  # Should fail for multiple matches


class TestLineTrimmedReplacer:
    """Tests for line_trimmed_replacer generator strategy."""

    def test_tab_vs_space_mismatch(self):
        """Test matching tab-indented content from space-indented find."""
        # File uses tabs
        content = "\tdef foo():\n\t\treturn 42"
        # LLM provides spaces
        old_string = "    def foo():\n        return 42"
        new_string = "    def foo():\n        return 100"

        matches = list(line_trimmed_replacer(content, old_string))
        assert matches == ["\tdef foo():\n\t\treturn 42"]

        result, strategy = replace_with_info(content, old_string, new_string)
        assert strategy == "line_trimmed"
        assert "return 100" in result

    def test_space_count_mismatch(self):
        """Test handling different space counts."""
        content = "  def foo():\n    return 42"  # 2-space indent
        old_string = "    def foo():\n        return 42"  # 4-space indent
        new_string = "    def bar():\n        return 42"

        matches = list(line_trimmed_replacer(content, old_string))
        assert matches == ["  def foo():\n    return 42"]

        result, strategy = replace_with_info(content, old_string, new_string)
        assert strategy == "line_trimmed"
        # new_string is inserted as-is (this layer does not adapt indentation)
        assert result.startswith("    def bar():")

    def test_no_match_different_content(self):
        """Test when trimmed content doesn't match."""
        content = "def foo():\n    return 42"
        old_string = "def bar():\n    return 42"  # Different function name
        new_string = "def bar():\n    return 100"

        matches = list(line_trimmed_replacer(content, old_string))
        assert matches == []

    def test_multiple_matches(self):
        """Test that strategy yields multiple candidates when ambiguous."""
        content = "foo()\nbar()\nfoo()"
        old_string = "foo()"
        new_string = "baz()"

        matches = list(line_trimmed_replacer(content, old_string))
        assert len(matches) == 2
        assert matches[0] == "foo()"
        assert matches[1] == "foo()"


class TestEscapeNormalizedReplacer:
    """Tests for escape_normalized_replacer generator strategy."""

    def test_escaped_newline(self):
        """Test handling escaped newline in search string."""
        content = "line1\nline2"
        old_string = "line1\\nline2"  # LLM escaped the newline
        new_string = "line1\\nline3"

        candidates = list(escape_normalized_replacer(content, old_string))
        assert "line1\nline2" in candidates

        result, strategy = replace_with_info(content, old_string, new_string)
        assert strategy == "escape_normalized"
        # replace_with_info inserts new_string as-is (it does not unescape new_string)
        assert result == "line1\\nline3"

    def test_escaped_tab(self):
        """Test handling escaped tab."""
        content = "col1\tcol2"
        old_string = "col1\\tcol2"
        new_string = "col1\\tcol3"

        candidates = list(escape_normalized_replacer(content, old_string))
        assert "col1\tcol2" in candidates

        result, strategy = replace_with_info(content, old_string, new_string)
        assert strategy == "escape_normalized"
        assert result == "col1\\tcol3"

    def test_no_escapes(self):
        """Test when there are no escapes to normalize."""
        content = "hello world"
        old_string = "hello world"  # No escapes
        new_string = "hello universe"

        candidates = list(escape_normalized_replacer(content, old_string))
        # This strategy may yield duplicates (direct match + block match).
        assert candidates
        assert all(c == "hello world" for c in candidates)

        result, strategy = replace_with_info(content, old_string, new_string)
        assert result == "hello universe"
        assert strategy == "simple"


class TestIndentationFlexibleReplacer:
    """Tests for indentation_flexible_replacer generator strategy."""

    def test_dedented_search(self):
        """Test matching when search string has no indentation."""
        content = "    def foo():\n        return 42"
        old_string = "def foo():\n    return 42"  # Dedented
        new_string = "def bar():\n    return 42"

        matches = list(indentation_flexible_replacer(content, old_string))
        assert matches == ["    def foo():\n        return 42"]

        # Note: in the full chain, this case is typically handled earlier by
        # line_trimmed. Here we only verify this strategy can find the block.
        result, _ = replace_with_info(content, old_string, new_string)
        assert "def bar():" in result

    def test_extra_indentation(self):
        """Test matching when search has extra indentation."""
        content = "def foo():\n    return 42"
        old_string = "    def foo():\n        return 42"  # Extra indent
        new_string = "    def bar():\n        return 42"

        matches = list(indentation_flexible_replacer(content, old_string))
        assert matches == ["def foo():\n    return 42"]


class TestWhitespaceNormalizedReplacer:
    """Tests for whitespace_normalized_replacer generator strategy."""

    def test_collapsed_whitespace(self):
        """Test matching with collapsed whitespace."""
        content = "def   foo(  x,   y  ):"  # Multiple spaces
        old_string = "def foo( x, y ):"  # Different spacing
        new_string = "def bar( x, y ):"

        matches = list(whitespace_normalized_replacer(content, old_string))
        assert len(matches) >= 1

    def test_mixed_whitespace(self):
        """Test matching with mixed tabs and spaces."""
        content = "def\tfoo():"  # Tab between def and foo
        old_string = "def foo():"  # Space
        new_string = "def bar():"

        matches = list(whitespace_normalized_replacer(content, old_string))
        assert "def\tfoo():" in matches


class TestBlockAnchorReplacer:
    """Tests for block_anchor_replacer generator strategy."""

    def test_fuzzy_middle_match(self):
        """Test matching with different middle content."""
        content = """def foo():
    # Original comment
    x = 1
    return x"""

        old_string = """def foo():
    # Different comment
    x = 1
    return x"""

        new_string = """def foo():
    # New comment
    x = 2
    return x"""

        matches = list(block_anchor_replacer(content, old_string))
        assert len(matches) >= 1

    def test_short_block_skipped(self):
        """Test that blocks shorter than 3 lines are skipped."""
        content = "line1\nline2"
        old_string = "line1\nline2"
        new_string = "new1\nnew2"

        matches = list(block_anchor_replacer(content, old_string))
        assert matches == []  # Too short for block anchor

    def test_no_anchor_match(self):
        """Test when anchors don't match."""
        content = """def foo():
    return 42"""

        old_string = """def bar():
    return 42"""

        new_string = """def bar():
    return 100"""

        matches = list(block_anchor_replacer(content, old_string))
        assert matches == []  # First line doesn't match


class TestReplaceWithFallback:
    """Tests for replace_with_fallback function."""

    def test_exact_match_first(self):
        """Test that the first strategy handles exact matches.

        Note: the current default chain starts with the "simple" strategy.
        """
        content = "Hello World"
        result, strategy = replace_with_fallback(content, "World", "Universe")
        assert result == "Hello Universe"
        assert strategy == "simple"

    def test_fallback_to_line_trimmed(self):
        """Test fallback to line_trimmed when exact fails."""
        content = "\tdef foo():\n\t\treturn 42"
        old_string = "    def foo():\n        return 42"
        new_string = "    def foo():\n        return 100"

        result, strategy = replace_with_fallback(content, old_string, new_string)
        assert result is not None
        assert strategy == "line_trimmed"

    def test_fallback_to_escape_normalized(self):
        """Test fallback to escape_normalized."""
        content = "line1\nline2"
        old_string = "line1\\nline2"
        new_string = "line1\\nline3"

        result, strategy = replace_with_fallback(content, old_string, new_string)
        assert result is not None
        assert strategy == "escape_normalized"

    def test_all_strategies_fail(self):
        """Test error when all strategies fail."""
        content = "Hello World"

        with pytest.raises(ValueError, match="oldString not found"):
            replace_with_fallback(content, "NotInContent", "Replacement")


class TestGetMultipleMatchInfo:
    """Tests for get_multiple_match_info function."""

    def test_single_match(self):
        """Test finding a single match."""
        content = "line1\nfoo bar\nline3"
        matches = get_multiple_match_info(content, "foo")
        assert len(matches) == 1
        assert matches[0][0] == 2  # Line 2

    def test_multiple_matches(self):
        """Test finding multiple matches."""
        content = "foo\nbar\nfoo\nbaz\nfoo"
        matches = get_multiple_match_info(content, "foo")
        assert len(matches) == 3
        assert matches[0][0] == 1
        assert matches[1][0] == 3
        assert matches[2][0] == 5

    def test_no_matches(self):
        """Test when string is not found."""
        content = "hello world"
        matches = get_multiple_match_info(content, "notfound")
        assert len(matches) == 0


class TestReplaceFunction:
    """Tests for the main replace() function (OpenCode architecture)."""

    def test_simple_replacement(self):
        """Test basic replacement."""
        content = "Hello World"
        result = replace(content, "World", "Universe")
        assert result == "Hello Universe"

    def test_replace_all(self):
        """Test replacing all occurrences."""
        content = "foo bar foo baz foo"
        result = replace(content, "foo", "qux", replace_all=True)
        assert result == "qux bar qux baz qux"

    def test_same_string_error(self):
        """Test error when old and new are same."""
        with pytest.raises(ValueError, match="must be different"):
            replace("content", "same", "same")

    def test_not_found_error(self):
        """Test error when string not found."""
        with pytest.raises(ValueError, match="not found"):
            replace("Hello World", "NotHere", "Replacement")

    def test_multiple_without_replace_all_error(self):
        """Test error when multiple matches and replace_all=False."""
        with pytest.raises(ValueError, match="multiple matches"):
            replace("foo bar foo", "foo", "baz")


class TestTrimmedBoundaryReplacer:
    """Tests for trimmed_boundary_replacer."""

    def test_leading_whitespace(self):
        """Test matching when find has leading whitespace."""
        content = "def foo():\n    return 42"
        # Extra leading/trailing whitespace, but line indentation matches the file.
        find = "  def foo():\n    return 42  "

        matches = list(trimmed_boundary_replacer(content, find))
        assert len(matches) >= 1

    def test_no_change_when_already_trimmed(self):
        """Test that already trimmed strings don't yield."""
        content = "hello world"
        find = "hello world"  # Already trimmed

        matches = list(trimmed_boundary_replacer(content, find))
        assert len(matches) == 0


class TestContextAwareReplacer:
    """Tests for context_aware_replacer."""

    def test_fifty_percent_similarity(self):
        """Test matching with 50% middle-line similarity."""
        content = """def foo():
    line1 = 1
    line2 = 2
    return x"""

        find = """def foo():
    different1 = 1
    line2 = 2
    return x"""

        matches = list(context_aware_replacer(content, find))
        # First and last match, 50% of middle matches
        assert len(matches) >= 1

    def test_short_block_skipped(self):
        """Test that blocks < 3 lines are skipped."""
        content = "line1\nline2"
        find = "line1\nline2"

        matches = list(context_aware_replacer(content, find))
        assert len(matches) == 0


class TestMultiOccurrenceReplacer:
    """Tests for multi_occurrence_replacer."""

    def test_finds_all_occurrences(self):
        """Test finding all exact matches."""
        content = "foo bar foo baz foo"
        find = "foo"

        matches = list(multi_occurrence_replacer(content, find))
        assert len(matches) == 3

    def test_no_matches(self):
        """Test no matches found."""
        content = "hello world"
        find = "foo"

        matches = list(multi_occurrence_replacer(content, find))
        assert len(matches) == 0


class TestIntegrationScenarios:
    """Integration tests for common real-world scenarios."""

    def test_go_file_tab_indent(self):
        """Test finding tab-indented block from space-indented find.

        Note: `string_utils.replace_with_info` inserts new_string as-is (no
        indentation adaptation). Indentation adaptation is handled in
        `str_replace_edit` integration tests.
        """
        # Go files use tabs
        content = """package main

func foo() int {
\treturn 42
}"""

        # LLM provides spaces (common mistake)
        old_string = """func foo() int {
    return 42
}"""

        new_string = """func foo() int {
    return 100
}"""

        result, strategy = replace_with_info(content, old_string, new_string)
        assert result is not None
        assert strategy == "line_trimmed"
        assert "    return 100" in result

    def test_python_file_space_indent(self):
        """Test handling Python files with space indentation."""
        content = """def foo():
    if True:
        return 42
    return 0"""

        # Different indentation level in search
        old_string = """    if True:
            return 42"""

        new_string = """    if True:
            return 100"""

        result, strategy = replace_with_info(content, old_string, new_string)
        assert result is not None

    def test_typescript_escaped_template_literal(self):
        """Test handling TypeScript template literals with escapes."""
        content = "const msg = `Hello\nWorld`"
        old_string = "const msg = `Hello\\nWorld`"
        new_string = "const msg = `Hello\\nUniverse`"

        result, strategy = replace_with_info(content, old_string, new_string)
        assert result is not None
        assert strategy == "escape_normalized"

    def test_replace_all_with_tabs(self):
        """Test replace_all preserves original formatting."""
        content = "\tfoo\n\tbar\n\tfoo"
        old_string = "foo"
        new_string = "baz"

        result = replace(content, old_string, new_string, replace_all=True)
        assert result == "\tbaz\n\tbar\n\tbaz"
