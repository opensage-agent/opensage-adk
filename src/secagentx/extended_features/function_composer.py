from typing import Any, Callable, Dict, List, Optional, Union
import inspect
import asyncio
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.base_tool import BaseTool


def combined_for(function_a: Callable, function_b: Callable, name: Optional[str] = None) -> FunctionTool:
    """
    Combine two functions, call function_b for each element returned by function_a
    
    Args:
        function_a: First function, returns dict with 'result' key containing list
        function_b: Second function, receives parameters matching keys from function_a's result elements
        name: Name for the combined function
        
    Returns:
        FunctionTool: Combined function that can be used directly as ADK tool
    """
    
    # Get function names
    func_a_name = function_a.__name__ if hasattr(function_a, '__name__') else 'function_a'
    func_b_name = function_b.__name__ if hasattr(function_b, '__name__') else 'function_b'
    
    # Create combined function
    def composed_function(*args, **kwargs):
        # Execute first function
        # Handle FunctionTool objects
        if hasattr(function_a, 'func'):
            # function_a is a FunctionTool
            actual_function_a = function_a.func
        else:
            actual_function_a = function_a
        
        if asyncio.iscoroutinefunction(actual_function_a):
            result_a = asyncio.run(actual_function_a(*args, **kwargs))
        else:
            result_a = actual_function_a(*args, **kwargs)
        
        # Check result format
        if not isinstance(result_a, dict) or "result" not in result_a:
            raise ValueError(f"Function {func_a_name} must return dict with 'result' key")
        
        result_list = result_a["result"]
        if not isinstance(result_list, list):
            raise ValueError(f"Function {func_a_name} 'result' value must be a list")
        
        # Check each element in the list
        if not result_list:
            return result_a  # Return directly if empty list
        
        # Check format of first element
        first_element = result_list[0]
        if not isinstance(first_element, dict):
            raise ValueError(f"Each element in {func_a_name} result list must be a dict")
        
        # Get function_b parameters
        # Handle FunctionTool objects
        if hasattr(function_b, 'func'):
            # function_b is a FunctionTool
            actual_function_b = function_b.func
        else:
            actual_function_b = function_b
        
        func_b_sig = inspect.signature(actual_function_b)
        func_b_params = list(func_b_sig.parameters.keys())
        
        # Check which parameters exist in first element
        available_keys = list(first_element.keys())
        matching_keys = [key for key in func_b_params if key in available_keys]
        
        if not matching_keys:
            # If no matching keys, return first function result directly
            return result_a
        
        # Execute second function for each element
        final_results = []
        for element in result_list:
            if not isinstance(element, dict):
                continue  # Skip non-dict elements
            
            # Prepare arguments
            call_args = {}
            for key in matching_keys:
                if key in element:
                    call_args[key] = element[key]
            
            # Execute second function
            if asyncio.iscoroutinefunction(actual_function_b):
                result_b = asyncio.run(actual_function_b(**call_args))
            else:
                result_b = actual_function_b(**call_args)
            
            final_results.append({
                "source_element": element,
                "result": result_b
            })
        
        return {
            "result": final_results,
            "composed_from": {
                "function_a": func_a_name,
                "function_b": func_b_name,
                "matching_keys": matching_keys
            }
        }
    
    # Create docstring
    doc_a = function_a.__doc__ or ""
    doc_b = function_b.__doc__ or ""
    
    combined_doc = f"""
Call function {func_a_name}, then for each result, call function {func_b_name}.

{doc_a.strip()}

{doc_b.strip()}

Data flow:
1. Execute {func_a_name} with input parameters
2. For each element in the result list, extract matching keys for {func_b_name}
3. Call {func_b_name} with extracted parameters
4. Aggregate all results

Returns:
- List of results from {func_b_name} for each element
- Source element and processed result for each item
- Composition information showing which functions were combined
"""
    
    # Create FunctionTool
    tool_name = name or f"{func_a_name}_combined_with_{func_b_name}"
    
    # Create function with docstring
    composed_function.__name__ = tool_name
    composed_function.__doc__ = combined_doc
    
    return FunctionTool(composed_function)


def combined_one(function_a: Callable, function_b: Callable, name: Optional[str] = None) -> FunctionTool:
    """
    Combine two functions, call function_b only for the first element returned by function_a
    
    Args:
        function_a: First function, returns dict with 'result' key containing list
        function_b: Second function, receives parameters matching keys from function_a's first result element
        name: Name for the combined function
        
    Returns:
        FunctionTool: Combined function that can be used directly as ADK tool
    """
    
    # Get function names
    func_a_name = function_a.__name__ if hasattr(function_a, '__name__') else 'function_a'
    func_b_name = function_b.__name__ if hasattr(function_b, '__name__') else 'function_b'
    
    # Create combined function
    def composed_function(*args, **kwargs):
        # Execute first function
        # Handle FunctionTool objects
        if hasattr(function_a, 'func'):
            # function_a is a FunctionTool
            actual_function_a = function_a.func
        else:
            actual_function_a = function_a
        
        if asyncio.iscoroutinefunction(actual_function_a):
            result_a = asyncio.run(actual_function_a(*args, **kwargs))
        else:
            result_a = actual_function_a(*args, **kwargs)
        
        # Check result format
        if not isinstance(result_a, dict) or "result" not in result_a:
            raise ValueError(f"Function {func_a_name} must return dict with 'result' key")
        
        result_list = result_a["result"]
        if not isinstance(result_list, list):
            raise ValueError(f"Function {func_a_name} 'result' value must be a list")
        
        # Check if list is empty
        if not result_list:
            return result_a  # Return directly if empty list
        
        # Get first element
        first_element = result_list[0]
        if not isinstance(first_element, dict):
            raise ValueError(f"First element in {func_a_name} result list must be a dict")
        
        # Get function_b parameters
        # Handle FunctionTool objects
        if hasattr(function_b, 'func'):
            # function_b is a FunctionTool
            actual_function_b = function_b.func
        else:
            actual_function_b = function_b
        
        func_b_sig = inspect.signature(actual_function_b)
        func_b_params = list(func_b_sig.parameters.keys())
        
        # Check which parameters exist in first element
        available_keys = list(first_element.keys())
        matching_keys = [key for key in func_b_params if key in available_keys]
        
        if not matching_keys:
            # If no matching keys, return first function result directly
            return result_a
        
        # Prepare arguments for first element
        call_args = {}
        for key in matching_keys:
            if key in first_element:
                call_args[key] = first_element[key]
        
        # Execute second function with first element
        if asyncio.iscoroutinefunction(actual_function_b):
            result_b = asyncio.run(actual_function_b(**call_args))
        else:
            result_b = actual_function_b(**call_args)
        
        return {
            "result": result_b,
            "composed_from": {
                "function_a": func_a_name,
                "function_b": func_b_name,
                "matching_keys": matching_keys,
                "processed_element": first_element
            }
        }
    
    # Create docstring
    doc_a = function_a.__doc__ or ""
    doc_b = function_b.__doc__ or ""
    
    combined_doc = f"""
Call function {func_a_name}, then call function {func_b_name} for the first result only.

{doc_a.strip()}

{doc_b.strip()}

Data flow:
1. Execute {func_a_name} with input parameters
2. Extract matching keys from the first element in the result list for {func_b_name}
3. Call {func_b_name} with extracted parameters
4. Return the result from {func_b_name}

Returns:
- Result from {func_b_name} for the first element
- Composition information showing which functions were combined
"""
    
    # Create FunctionTool
    tool_name = name or f"{func_a_name}_combined_one_{func_b_name}"
    
    # Create function with docstring
    composed_function.__name__ = tool_name
    composed_function.__doc__ = combined_doc
    
    return FunctionTool(composed_function) 