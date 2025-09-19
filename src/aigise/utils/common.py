import os
import random
import socket


def find_free_port(
    start_port: int = 20000, end_port: int = 30000, reserved_ports: set[int] = None
) -> int:
    """Find a free port in the given range."""
    if reserved_ports is None:
        reserved_ports = set()
    first_port = random.randint(start_port, end_port - 1)
    port = first_port

    def next_port(p):
        p += 1
        if p >= end_port:
            p = start_port
        return p

    while True:
        if port in reserved_ports:
            port = next_port(port)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port

        port = next_port(port)

        if port == first_port:
            raise RuntimeError("No free port found")


def find_free_ports(
    n: int, start_port: int = 20000, end_port: int = 30000
) -> list[int]:
    """Find n free ports in the given range."""
    if n > (end_port - start_port):
        raise ValueError("Not enough ports in the given range")
    ports = set()
    while len(ports) < n:
        port = find_free_port(start_port, end_port, ports)
        ports.add(port)
    return list(ports)


def _slice_body(file_content: str, start: int, end: int) -> str:
    """
    Return the source lines [start, end] (1-based, inclusive) from `file_content`.
    If indices are out of range, the slice is truncated accordingly.
    """
    lines = file_content.splitlines()
    start_idx = max(start - 1, 0)
    end_idx = min(end, len(lines))
    return "\n".join(lines[start_idx:end_idx])


def get_lang_from_filename(filename):
    """Get the language from the filename."""
    filename = str(filename)
    if filename.endswith(".c"):
        return "c"
    elif (
        filename.endswith(".cpp")
        or filename.endswith(".cc")
        or filename.endswith(".cxx")
        or filename.endswith(".hpp")
        or filename.endswith(".hxx")
        or filename.endswith(".h")
        or filename.endswith(".h++")
        or filename.endswith(".hh")
    ):
        return "cpp"
    elif filename.endswith(".java"):
        return "java"
    else:
        return None


def check_crash(output):
    """
    Check if the poc still crashes the program.
    """
    return "sanitizer" in output.lower()


def wrap_in_cd(command, basedir):
    if basedir:
        return f"bash -c 'cd {basedir} && {command}'"
    return command


def is_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))
