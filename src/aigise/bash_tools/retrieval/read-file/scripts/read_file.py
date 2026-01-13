import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser(description="Read lines from a file.")
    parser.add_argument("--file", required=True, help="Absolute path to the file")
    parser.add_argument("--linenum", type=int, required=True, help="Target line number")
    parser.add_argument("--context", type=int, required=True, help="Context lines")

    args = parser.parse_args()

    filepath = args.file
    linenum = args.linenum
    context = args.context

    if not os.path.isabs(filepath):
        print(json.dumps({"error": "The input file path is not an absolute path."}))
        return

    try:
        # Since this runs in the sandbox, we can read the file directly
        # assuming the sandbox has the file system mounted or we are in it.
        # Original code used sandbox.extract_file_from_container(filepath).
        # bash_tools run IN the sandbox, so we just read the file.

        with open(filepath, "r", errors="ignore") as f:
            lines = f.readlines()

        start = max(0, linenum - context - 1)  # Adjust for 0-based index
        end = min(len(lines), linenum + context)  # Adjust for 0-based index

        if end - start > 210:
            print(
                json.dumps(
                    {
                        "error": f"The number of lines to extract is too large, please set context to a value less than 100. The number of lines to extract is {end - start}."
                    }
                )
            )
            return

        result = f"# Extracted lines from {filepath} (lines {start + 1} to {end})\n"

        for i in range(start, end):
            line_number = i + 1
            line_content = lines[i].rstrip(
                "\n"
            )  # remove trailing newline for formatting
            result += f"{line_number:4d}|{line_content}\n"

        print(json.dumps({"result": result}))

    except Exception as e:
        print(json.dumps({"error": f"Failed to read file '{filepath}': {e}"}))


if __name__ == "__main__":
    main()
