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
