#!/usr/bin/env python3
"""
Test function_composer functionality
"""

import pytest

from aigise.extended_features.function_composer import combined_for, combined_one


def helper_function_a(query: str) -> dict:
    """
    Helper function A that returns a list of dictionaries.

    Args:
        query: Search query

    Returns:
        Dictionary with 'result' key containing list of elements
    """
    return {
        "result": [
            {"function_name": "aa", "filepath": "/path/to/aa.py"},
            {"function_name": "bb", "filepath": "/path/to/bb.py"},
            {"function_name": "cc", "filepath": "/path/to/cc.py"},
        ]
    }


def helper_function_b(function_name: str) -> dict:
    """
    Helper function B that processes function names.

    Args:
        function_name: Name of the function to process

    Returns:
        Processing result
    """
    return {
        "processed_function": function_name,
        "status": "success",
        "length": len(function_name),
    }


def helper_function_c(filepath: str) -> dict:
    """
    Helper function C that processes file paths.

    Args:
        filepath: Path to the file to process

    Returns:
        Processing result
    """
    return {
        "processed_file": filepath,
        "file_type": filepath.split('.')[-1] if '.' in filepath else "unknown",
        "status": "success",
    }


def test_combined_for_functionality():
    """Test combined_for functionality"""
    # Create combined function
    combined_ab = combined_for(helper_function_a, helper_function_b, "test_a_for_b")

    # Test the combined function - call the underlying function
    result = combined_ab.func("test query")

    # Assertions
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "result" in result, "Result should contain 'result' key"
    assert "composed_from" in result, "Result should contain composition info"

    # Check that all elements were processed
    assert len(result["result"]) == 3, "Should process all 3 elements"

    # Check structure of first result
    first_result = result["result"][0]
    assert "source_element" in first_result, "Should contain source element"
    assert "result" in first_result, "Should contain processing result"

    # Check the processed result
    processed = first_result["result"]
    assert (
        processed["processed_function"] == "aa"
    ), "Should process function name correctly"
    assert processed["status"] == "success", "Should have success status"

    # Test with function_c
    combined_ac = combined_for(helper_function_a, helper_function_c, "test_a_for_c")
    result_2 = combined_ac.func("test query")

    assert len(result_2["result"]) == 3, "Should process all 3 elements with function_c"
    first_result_c = result_2["result"][0]
    processed_c = first_result_c["result"]
    assert (
        processed_c["processed_file"] == "/path/to/aa.py"
    ), "Should process filepath correctly"


def test_combined_one_functionality():
    """Test combined_one functionality"""
    # Create combined function
    combined_ab_one = combined_one(helper_function_a, helper_function_b, "test_a_one_b")

    # Test the combined function - call the underlying function
    result = combined_ab_one.func("test query")

    # Assertions
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "result" in result, "Result should contain 'result' key"
    assert "composed_from" in result, "Result should contain composition info"

    # Check that only first element was processed
    processed = result["result"]
    assert (
        processed["processed_function"] == "aa"
    ), "Should process first function name only"
    assert processed["status"] == "success", "Should have success status"
    assert processed["length"] == 2, "Should correctly calculate length of 'aa'"

    # Test with function_c
    combined_ac_one = combined_one(helper_function_a, helper_function_c, "test_a_one_c")
    result_2 = combined_ac_one.func("test query")

    processed_c = result_2["result"]
    assert (
        processed_c["processed_file"] == "/path/to/aa.py"
    ), "Should process first filepath only"
    assert processed_c["file_type"] == "py", "Should correctly extract file type"


def test_chaining_capability():
    """Test chaining capability"""
    # Create base combined function
    combined_ab = combined_one(helper_function_a, helper_function_b, "test_a_one_b")

    # Define function to chain with - this function expects a single result, not a list
    def helper_function_d(processed_function: str, status: str, length: int) -> dict:
        return {
            "final_result": f"Final processing of {processed_function}",
            "status": "completed",
            "original_length": length,
        }

    # For chaining, we need to create a wrapper that returns the expected format
    def wrapper_for_chaining(query: str) -> dict:
        """Wrapper that converts combined_one result to expected list format"""
        result = combined_ab.func(query)
        # Extract the result and wrap it in a list format expected by combined_for
        inner_result = result["result"]
        return {"result": [inner_result]}  # Wrap single result in a list

    # Create chained function using combined_for with the wrapper
    chain_1 = combined_for(wrapper_for_chaining, helper_function_d, "chain_ab_d")

    # Test the chained function - call the underlying function
    result = chain_1.func("test query")

    # Assertions
    assert isinstance(result, dict), "Chained result should be a dictionary"
    assert "result" in result, "Chained result should contain 'result' key"
    assert len(result["result"]) == 1, "Should have one processed result"

    # Get the first (and only) result
    first_result = result["result"][0]
    assert "result" in first_result, "Should contain processing result"

    final_result = first_result["result"]
    assert (
        final_result["final_result"] == "Final processing of aa"
    ), "Should chain processing correctly"
    assert final_result["status"] == "completed", "Should have completed status"
    assert final_result["original_length"] == 2, "Should preserve original length"


def test_tool_properties():
    """Test that created tools have proper properties"""
    combined_tool = combined_for(helper_function_a, helper_function_b, "test_tool")

    # Check that it's a FunctionTool
    from google.adk.tools.function_tool import FunctionTool

    assert isinstance(
        combined_tool, FunctionTool
    ), "Should create FunctionTool instance"

    # Check tool properties
    assert hasattr(combined_tool, 'name'), "Tool should have name property"
    assert hasattr(
        combined_tool, 'description'
    ), "Tool should have description property"


def test_empty_result_handling():
    """Test handling of empty results"""

    def empty_function_a(query: str) -> dict:
        return {"result": []}

    combined_empty = combined_for(empty_function_a, helper_function_b, "empty_test")
    result = combined_empty.func("test")

    # Should return the original empty result
    assert result == {"result": []}, "Should handle empty results gracefully"


def test_invalid_result_format():
    """Test handling of invalid result formats"""

    def invalid_function_a(query: str) -> dict:
        return {"wrong_key": []}

    combined_invalid = combined_for(
        invalid_function_a, helper_function_b, "invalid_test"
    )

    with pytest.raises(ValueError, match="must return dict with 'result' key"):
        combined_invalid.func("test")


if __name__ == "__main__":
    # Allow running the test directly for debugging
    pytest.main([__file__, "-v"])
