from enum import StrEnum


class CWE(StrEnum):
    UNKNOWN = "unknown"

    # Libfuzzer
    Libfuzzer = "libfuzzer"

    # UndefinedBehaviorSanitizer
    Undefined_behavior = "undefined behavior"

    # MemorySanitizer
    Use_of_uninitialized_memory = "use of uninitialized memory"

    # AddressSanitizer
    ILL = "ill"
    FPE = "floating point exception"
    ABORT = "abort"
    Unknown_crash = "unknown crash"
    Allocation_size_too_big = "allocation size too big"
    Null_dereference = "null dereference"
    Segv_on_unknown_address = "segv on unknown address"
    Heap_buffer_overflow = "heap buffer overflow"
    Stack_buffer_overflow = "stack buffer overflow"
    Stack_buffer_underflow = "stack buffer underflow"
    Dynamic_stack_buffer_overflow = "dynamic stack buffer overflow"
    Global_buffer_overflow = "global buffer overflow"
    Container_overflow = "container overflow"
    Negative_size_param = "negative size param"
    Function_param_overlap = "function param overlap"
    Stack_overflow = "stack overflow"
    Stack_use_after_return = "stack use after return"
    Stack_use_after_scope = "stack use after scope"
    Heap_double_free = "heap double free"
    Heap_use_after_free = "heap use after free"
    Bad_free = "bad free"
    Memory_Leak = "memory leak"
    Out_of_memory = "out of memory"

    # Jazzer
    File_path_traversal = "file path traversal"
    LDAP_injection = "ldap injection"
    Naming_context_lookup = "naming context lookup"
    OS_command_injection = "os command injection"
    Reflective_call = "reflective call"
    Remote_code_execution = "remote code execution"
    Regular_expression_injection = "regular expression injection"
    Script_engine_injection = "script engine injection"
    Server_side_request_forgery = "server side request forgery"
    SQL_injection = "sql injection"
    XPath_injection = "xpath injection"

    # Java exception
    Java_generic_exception = "java generic exception"
    Java_native_error = "java native error"

    # ThreadSanitizer
    Data_race = "data race"


CWE_DESCRIPTIONS = {
    CWE.UNKNOWN: "The nature of the vulnerability is unknown or unspecified",
    # Libfuzzer
    CWE.Libfuzzer: "The program encountered an error while running with LibFuzzer, a coverage-guided fuzz testing tool",
    # UndefinedBehaviorSanitizer
    CWE.Undefined_behavior: "The program exhibits undefined behavior, leading to unpredictable results or crashes",
    # MemorySanitizer
    CWE.Use_of_uninitialized_memory: "The program reads uninitialized memory, leading to undefined behavior or crashes",
    # AddressSanitizer
    CWE.ILL: "The program encountered an illegal instruction, indicating a hardware or software issue",
    CWE.FPE: "The program encountered a floating-point exception, such as division by zero or invalid arithmetic operation",
    CWE.ABORT: "The program was terminated due to an abort signal, indicating a critical error",
    CWE.Allocation_size_too_big: "The program attempted to allocate a memory block that exceeds the maximum allowed size",
    CWE.Unknown_crash: "The program crashed, but the cause is unknown or not specified",
    CWE.Null_dereference: "The program attempts to dereference a null pointer, causing a segmentation fault or crash",
    CWE.Segv_on_unknown_address: "The program attempted to access an invalid or unallocated memory address, leading to a segmentation fault",
    CWE.Heap_buffer_overflow: "The program tried to access a heap object outside of its allocated memory, causing potential memory corruption or crashes",
    CWE.Stack_buffer_overflow: "A function writes more data to a local stack variable than it can hold, overwriting adjacent memory and potentially leading to execution hijacking",
    CWE.Stack_buffer_underflow: "A program attempts to read data before the beginning of a stack buffer, leading to unintended behavior",
    CWE.Dynamic_stack_buffer_overflow: "A dynamically allocated stack buffer is overflowed, leading to potential memory corruption or execution hijacking",
    CWE.Global_buffer_overflow: "A buffer overflow occurs in globally allocated memory, leading to memory corruption or crashes",
    CWE.Container_overflow: "A data structure, such as a vector or list, is accessed beyond its allocated memory, causing memory corruption or unexpected behavior",
    CWE.Negative_size_param: "A function receives a negative size parameter, leading to unexpected behavior or memory allocation errors",
    CWE.Function_param_overlap: "The source and destination buffers of a memory copy operation overlap, causing data corruption",
    CWE.Stack_overflow: "Excessive function calls lead to stack exhaustion, causing the program to crash or behave unpredictably",
    CWE.Stack_use_after_return: "A function accesses stack memory after returning, leading to use-after-free vulnerabilities",
    CWE.Stack_use_after_scope: "A program accesses stack memory that has gone out of scope, leading to undefined behavior or crashes",
    CWE.Heap_double_free: "A heap-allocated memory block is freed twice, potentially causing memory corruption or security vulnerabilities",
    CWE.Heap_use_after_free: "Memory is accessed after it has been freed, leading to undefined behavior and security risks",
    CWE.Bad_free: "A program attempts to free a memory block that was not dynamically allocated, causing undefined behavior or crashes",
    CWE.Memory_Leak: "A program fails to free allocated memory, leading to resource exhaustion and degraded system performance over time",
    CWE.Out_of_memory: "The program runs out of memory, causing allocation failures and potential crashes",
    # Jazzer
    CWE.File_path_traversal: "An attacker can manipulate file paths to access unauthorized files or directories",
    CWE.LDAP_injection: "Untrusted input is used in LDAP queries, leading to potential injection attacks",
    CWE.Naming_context_lookup: "An attacker can perform unauthorized JNDI lookups to access sensitive resources",
    CWE.OS_command_injection: "Untrusted input is used in system commands, leading to potential command injection attacks",
    CWE.Reflective_call: "An attacker can load and execute arbitrary libraries, leading to code execution vulnerabilities",
    CWE.Remote_code_execution: "An attacker can execute arbitrary code on a remote system, leading to full compromise",
    CWE.Regular_expression_injection: "Untrusted input is used in regular expressions, leading to potential injection attacks",
    CWE.Script_engine_injection: "An attacker can execute arbitrary scripts in the application context, leading to code execution vulnerabilities",
    CWE.Server_side_request_forgery: "An attacker can make the server perform unauthorized requests, leading to potential data exfiltration or unauthorized access",
    CWE.SQL_injection: "Untrusted input is used in SQL queries, leading to potential injection attacks",
    CWE.XPath_injection: "An XPath query is constructed using untrusted input, leading to potential injection attacks",
    CWE.Java_generic_exception: "A Java exception occurred, indicating a potential issue in the code or environment",
    CWE.Java_native_error: "A Java native error occurred, indicating a potential issue in the native code or environment",
    # ThreadSanitizer
    CWE.Data_race: "A data race occurred, indicating a potential issue in the code or environment",
}

CWE_REPAIR_ADVICE = {
    **dict.fromkeys(
        [CWE.UNKNOWN],
        (
            "1. Review the code logic and memory operations to identify potential corruption.\n"
            "2. Check for uninitialized variables or unintended pointer accesses.\n"
            "3. Implement defensive programming techniques to validate all memory accesses."
        ),
    ),
    **dict.fromkeys(
        [CWE.Libfuzzer],
        (
            "1. Review the code for potential assumptions that may lead to undefined behavior.\n"
            "2. Check the stack traces for any specific functions or lines of code that may be causing the issue.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.Undefined_behavior],
        (
            "1. Review the code for undefined behavior patterns and correct them.\n"
            "2. Check for type mismatches, signed integer overflows, and other undefined behavior triggers.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.Use_of_uninitialized_memory],
        (
            "1. Initialize all variables before using them to prevent uninitialized memory access.\n"
            "2. Check for uninitialized pointers or memory buffers that may lead to undefined behavior.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.ABORT],
        (
            "1. Investigate the cause of the abort signal to identify the root cause of the error.\n"
            "2. Review the program's error handling mechanisms to ensure critical errors are handled appropriately.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.ILL],
        (
            "1. Investigate the cause of the illegal instruction to identify the source of the error.\n"
            "2. Check for hardware or software issues that may be causing the illegal instruction.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.FPE],
        (
            "1. Implement input validation to prevent division by zero and other arithmetic exceptions.\n"
            "2. Use exception handling mechanisms to gracefully handle floating-point exceptions.\n"
            "3. Check for invalid arithmetic operations that may lead to floating-point exceptions."
        ),
    ),
    **dict.fromkeys(
        [CWE.Unknown_crash],
        (
            "1. Investigate the crash logs and stack traces to identify the cause of the crash.\n"
            "2. Review the code for potential memory corruption or undefined behavior.\n"
            "3. Check for uninitialized variables or unintended pointer accesses.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.Allocation_size_too_big],
        (
            "1. Check the size of memory allocations to ensure they are within reasonable limits.\n"
            "2. Implement input validation to prevent excessively large allocation requests.\n"
            "3. Use appropriate data types for size calculations to avoid overflow."
        ),
    ),
    **dict.fromkeys(
        [CWE.Heap_buffer_overflow, CWE.Stack_buffer_overflow, CWE.Dynamic_stack_buffer_overflow, CWE.Global_buffer_overflow, CWE.Container_overflow],
        (
            "1. If overflow is unavoidable, allocate a sufficiently large buffer during initialization.\n"
            "2. Add explicit bounds checking before accessing arrays or buffers to prevent overflows.\n"
            "3. Replace unsafe functions like memcpy, strcpy, strcat, and sprintf with safer alternatives such as strncpy, strncat, and snprintf.\n"
            "4. Check for integer overflows in size calculations that could cause improper memory allocations.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.Stack_buffer_underflow],
        (
            "1. Ensure that stack buffers are properly initialized before reading data from them.\n"
            "2. Implement bounds checking to prevent underflow conditions.\n"
            "3. Use safer string manipulation functions like strncpy and strncat to avoid buffer underflows."
        ),
    ),
    **dict.fromkeys(
        [CWE.Null_dereference, CWE.Segv_on_unknown_address],
        (
            "1. Ensure all pointers are initialized before use to prevent null dereferences.\n"
            "2. Validate pointer values before dereferencing them.\n"
            "3. Implement default values for pointers to reduce the risk of unintended NULL dereferences."
        ),
    ),
    **dict.fromkeys(
        [CWE.Stack_use_after_return, CWE.Stack_use_after_scope],
        (
            "1. Avoid returning addresses of local variables from functions, as stack memory is deallocated after return.\n"
            "2. If persistent storage is needed, use heap-allocated memory instead of local stack memory.\n"
            "3. Check the lifetime of variables to ensure they are not accessed after they go out of scope."
        ),
    ),
    **dict.fromkeys(
        [CWE.Heap_use_after_free, CWE.Heap_double_free],
        (
            "1. Set pointers to NULL immediately after freeing them to prevent accidental reuse.\n"
            "2. Ensure that each allocated memory block is freed only once.\n"
            "3. Track memory allocations and deallocations systematically to prevent use-after-free conditions.\n"
            "4. Consider swap the order of freeing memory and accessing it."
        ),
    ),
    **dict.fromkeys(
        [CWE.Bad_free],
        (
            "1. Ensure that only dynamically allocated memory is freed.\n"
            "2. Avoid freeing memory that was not allocated using malloc or similar functions.\n"
            "3. Verify pointer values before attempting to free them."
        ),
    ),
    **dict.fromkeys(
        [CWE.Memory_Leak],
        (
            "1. Ensure that every memory allocation has a corresponding deallocation.\n"
            "2. Track allocated memory systematically to avoid leaks.\n"
            "3. Avoid unnecessary memory allocations by reusing buffers where possible."
        ),
    ),
    **dict.fromkeys(
        [CWE.Negative_size_param],
        (
            "1. Validate all size parameters before passing them to memory allocation functions.\n"
            "2. Ensure that buffer sizes and loop limits are always positive values.\n"
            "3. Use unsigned integer types to store sizes to prevent negative values."
        ),
    ),
    **dict.fromkeys(
        [CWE.Function_param_overlap],
        (
            "1. Ensure that the source and destination buffers do not overlap in memcpy operations.\n"
            "2. If overlapping memory regions must be copied, use memmove instead of memcpy.\n"
            "3. Validate memory regions before performing copy operations."
        ),
    ),
    **dict.fromkeys(
        [CWE.Stack_overflow],
        (
            "1. Avoid deep recursion by implementing iterative solutions where possible.\n"
            "2. Increase the stack size if necessary to accommodate recursion.\n"
            "3. Reduce function call depth by refactoring complex recursive functions."
        ),
    ),
    # TODO: LLM-generated CWEs suggestions
    **dict.fromkeys(
        [CWE.Out_of_memory],
        (
            "1. Check for memory leaks that may be consuming system resources.\n"
            "2. Optimize memory usage by releasing unused resources.\n"
            "3. Increase system memory or swap space to accommodate memory requirements."
        ),
    ),
    **dict.fromkeys(
        [CWE.File_path_traversal],
        (
            "1. Validate user input to prevent directory traversal attacks.\n"
            "2. Use whitelists or allowlists to restrict file access to authorized directories.\n"
            "3. Sanitize file paths before using them in file operations."
        ),
    ),
    **dict.fromkeys(
        [CWE.LDAP_injection],
        (
            "1. Avoid constructing LDAP queries using untrusted input.\n"
            "2. Use parameterized queries or prepared statements to prevent injection attacks.\n"
            "3. Sanitize user input before using it in LDAP queries."
        ),
    ),
    **dict.fromkeys(
        [CWE.Naming_context_lookup],
        (
            "1. Restrict access to naming contexts to prevent unauthorized lookups.\n"
            "2. Use secure authentication mechanisms to control access to sensitive resources.\n"
            "3. Monitor naming context lookups for suspicious activity."
        ),
    ),
    **dict.fromkeys(
        [CWE.OS_command_injection],
        (
            "1. Avoid constructing system commands using untrusted input.\n"
            "2. Use parameterized commands or secure APIs to prevent injection attacks.\n"
            "3. Sanitize user input before executing system commands."
        ),
    ),
    **dict.fromkeys(
        [CWE.Reflective_call],
        (
            "1. Avoid loading and executing arbitrary libraries in the application context.\n"
            "2. Use secure APIs or sandboxed environments to execute external code.\n"
            "3. Validate library paths and signatures before loading them into memory."
        ),
    ),
    **dict.fromkeys(
        [CWE.Remote_code_execution],
        (
            "1. Implement strong input validation to prevent code injection attacks.\n"
            "2. Use secure communication protocols to protect against remote code execution.\n"
            "3. Monitor network traffic for suspicious activity and unauthorized access."
        ),
    ),
    **dict.fromkeys(
        [CWE.Regular_expression_injection],
        (
            "1. Avoid constructing regular expressions using untrusted input.\n"
            "2. Use predefined regex patterns or libraries to prevent injection attacks.\n"
            "3. Sanitize user input before using it in regular expressions."
        ),
    ),
    **dict.fromkeys(
        [CWE.Script_engine_injection],
        (
            "1. Avoid executing untrusted scripts or code in the application context.\n"
            "2. Use secure sandboxing techniques to isolate script execution.\n"
            "3. Validate script inputs and restrict access to sensitive resources."
        ),
    ),
    **dict.fromkeys(
        [CWE.Server_side_request_forgery],
        (
            "1. Validate and sanitize all user-provided URLs to prevent SSRF attacks.\n"
            "2. Use allowlists or secure APIs to restrict server-side requests to authorized domains.\n"
            "3. Monitor server logs for unusual or unauthorized requests."
        ),
    ),
    **dict.fromkeys(
        [CWE.SQL_injection],
        (
            "1. Avoid constructing SQL queries using untrusted input.\n"
            "2. Use parameterized queries or prepared statements to prevent injection attacks.\n"
            "3. Sanitize user input before using it in SQL queries."
        ),
    ),
    **dict.fromkeys(
        [CWE.XPath_injection],
        (
            "1. Avoid constructing XPath queries using untrusted input.\n"
            "2. Use parameterized queries or prepared statements to prevent injection attacks.\n"
            "3. Sanitize user input before using it in XPath queries."
        ),
    ),
    **dict.fromkeys(
        [CWE.Java_generic_exception],
        (
            "1. Investigate the cause of the Java exception to identify the root cause of the error.\n"
            "2. Review the code for potential memory corruption or undefined behavior.\n"
            "3. Check for uninitialized variables or unintended pointer accesses.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.Java_native_error],
        (
            "1. Investigate the cause of the Java native error to identify the root cause of the error.\n"
            "2. Review the code for potential memory corruption or undefined behavior.\n"
            "3. Check for uninitialized variables or unintended pointer accesses.\n"
        ),
    ),
    **dict.fromkeys(
        [CWE.Data_race],
        (
            "1. Review the code for potential data races and correct them.\n"
            "2. Check for concurrent access to shared resources.\n"
            "3. Implement synchronization mechanisms to prevent data races."
        ),
    ),
}
