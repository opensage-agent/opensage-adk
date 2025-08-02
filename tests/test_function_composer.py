#!/usr/bin/env python3
"""
Test function_composer functionality
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from secagentx.extended_features.function_composer import combined_for, combined_one


def test_function_a(query: str) -> dict:
    """
    Test function A that returns a list of dictionaries.
    
    Args:
        query: Search query
        
    Returns:
        Dictionary with 'result' key containing list of elements
    """
    return {
        "result": [
            {"function_name": "aa", "filepath": "/path/to/aa.py"},
            {"function_name": "bb", "filepath": "/path/to/bb.py"},
            {"function_name": "cc", "filepath": "/path/to/cc.py"}
        ]
    }


def test_function_b(function_name: str) -> dict:
    """
    Test function B that processes function names.
    
    Args:
        function_name: Name of the function to process
        
    Returns:
        Processing result
    """
    return {
        "processed_function": function_name,
        "status": "success",
        "length": len(function_name)
    }


def test_function_c(filepath: str) -> dict:
    """
    Test function C that processes file paths.
    
    Args:
        filepath: Path to the file to process
        
    Returns:
        Processing result
    """
    return {
        "processed_file": filepath,
        "file_type": filepath.split('.')[-1] if '.' in filepath else "unknown",
        "status": "success"
    }


def test_combined_for():
    """Test combined_for functionality"""
    print("=== Test combined_for functionality ===\n")
    
    # Create combined function
    combined_ab = combined_for(test_function_a, test_function_b, "test_a_for_b")
    
    print("1. Test function_a + function_b")
    result = combined_ab("test query")
    print(f"Result: {result}")
    print()
    
    print("2. Tool information:")
    print(f"Name: {combined_ab.name}")
    print(f"Description: {combined_ab.description}")
    print()
    
    # Test with function_c
    combined_ac = combined_for(test_function_a, test_function_c, "test_a_for_c")
    result_2 = combined_ac("test query")
    print("3. Test function_a + function_c")
    print(f"Result: {result_2}")
    print()


def test_combined_one():
    """Test combined_one functionality"""
    print("=== Test combined_one functionality ===\n")
    
    # Create combined function
    combined_ab_one = combined_one(test_function_a, test_function_b, "test_a_one_b")
    
    print("1. Test function_a + function_b (first element only)")
    result = combined_ab_one("test query")
    print(f"Result: {result}")
    print()
    
    print("2. Tool information:")
    print(f"Name: {combined_ab_one.name}")
    print(f"Description: {combined_ab_one.description}")
    print()
    
    # Test with function_c
    combined_ac_one = combined_one(test_function_a, test_function_c, "test_a_one_c")
    result_2 = combined_ac_one("test query")
    print("3. Test function_a + function_c (first element only)")
    print(f"Result: {result_2}")
    print()


def test_chaining():
    """Test chaining capability"""
    print("=== Test chaining capability ===\n")
    
    # Create base combined function
    combined_ab = combined_one(test_function_a, test_function_b, "test_a_one_b")
    
    # Chain with another function
    def test_function_d(processed_function: str) -> dict:
        return {
            "final_result": f"Final processing of {processed_function}",
            "status": "completed"
        }
    
    # Create chained function
    chain_1 = combined_one(combined_ab, test_function_d, "chain_ab_d")
    
    print("1. Test chaining: (function_a + function_b) + function_d")
    result = chain_1("test query")
    print(f"Result: {result}")
    print()
    
    print("2. Tool information:")
    print(f"Name: {chain_1.name}")
    print(f"Description: {chain_1.description}")
    print()


if __name__ == "__main__":
    test_combined_for()
    test_combined_one()
    test_chaining()
    print("All tests completed successfully!") 