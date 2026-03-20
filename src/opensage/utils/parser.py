from collections import defaultdict

from tree_sitter_languages import get_parser


# ---------- C ----------
def find_c_function(node, cur_class=None):
    """
    Return (qualified_name, start_line, end_line) for a C function_definition.
    """
    if node.type != "function_definition" or b"enum" in node.text:
        return None

    declarator = node.child_by_field_name("declarator")
    if not declarator:
        return None

    ident = declarator.child_by_field_name("declarator")
    if not ident:
        return None

    name_node = next((c for c in ident.children if c.type == "identifier"), ident)
    fname = name_node.text.decode()

    if cur_class:
        fname = f"{cur_class}::{fname}"

    return fname, node.start_point[0] + 1, node.end_point[0] + 1


# ---------- C++ ----------
def find_cpp_function(node, cur_class=None):
    """
    Handle ordinary functions, constructors, destructors and operators.
    """
    if node.type not in (
        "function_definition",
        "constructor_definition",
        "destructor_definition",
    ):
        return None

    declarator = node.child_by_field_name("declarator")
    if not declarator:
        return None
    inner = declarator.child_by_field_name("declarator")
    if not inner:
        inner = next(
            (c for c in declarator.children if c.type.endswith("declarator")), None
        )
        if not inner:
            return None

    # Choose the first child that looks like an identifier / destructor / operator token
    name_node = next(
        (
            c
            for c in inner.children
            if c.type in ("identifier", "destructor_name", "operator")
        ),
        inner,
    )

    fname = name_node.text.decode()
    if cur_class and not fname.startswith(f"{cur_class}::"):
        fname = f"{cur_class}::{fname}"

    return fname, node.start_point[0] + 1, node.end_point[0] + 1


# ---------- Java ----------
def find_java_function(node, cur_class=None):
    """
    Handle method_declaration and constructor_declaration.
    """
    if node.type not in ("method_declaration", "constructor_declaration"):
        return None

    name_node = node.child_by_field_name("name")
    if not name_node:
        return None

    fname = name_node.text.decode()
    if cur_class:
        fname = f"{cur_class}.{fname}"

    return fname, node.start_point[0] + 1, node.end_point[0] + 1


# ---------- Collector ----------
def get_function_info(code: str, lang: str = "cpp"):
    """
    Parse source code and return a dictionary:

        { qualified_name : [(start_line, end_line), ...] }

    * For overloaded functions or multiple constructors,
      all occurrences are preserved in the value list.
    """
    parser = get_parser(lang)
    tree = parser.parse(code.encode())
    root = tree.root_node

    dispatch = {
        "c": find_c_function,
        "cpp": find_cpp_function,
        "java": find_java_function,
    }
    find_func = dispatch[lang]

    functions = defaultdict(list)

    stack = [(root, None)]  # (node, current_class)
    while stack:
        node, cur_class = stack.pop()

        # Update current class name when entering a class / struct / interface
        if node.type in (
            "class_specifier",
            "struct_specifier",
            "class_declaration",
            "interface_declaration",
        ):
            name_node = node.child_by_field_name("name")
            if name_node:
                cur_class = name_node.text.decode()

        # Push children to stack
        for child in reversed(node.children):
            stack.append((child, cur_class))

        # Collect functions / methods / constructors / destructors
        if node.type in {
            "function_definition",
            "constructor_definition",
            "destructor_definition",
            "method_declaration",
            "constructor_declaration",
        }:
            res = find_func(node, cur_class)
            if res:
                fname, l0, l1 = res
                fname = fname.replace("\n", "")
                functions[fname].append((l0, l1))

    return functions
