import docker
import time
import io
import tarfile
import os
import shutil
import subprocess
from collections import deque
from src.utils.parser import get_function_info
from docker.errors import NotFound, APIError
import re
import tempfile
client = docker.from_env(timeout=300)

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
    elif filename.endswith(".cpp") or filename.endswith(".cc") or filename.endswith(".cxx") or filename.endswith(".hpp") or filename.endswith(".hxx") or filename.endswith(".h") or filename.endswith(".h++") or filename.endswith(".hh"): 
        return "cpp"
    elif filename.endswith(".java"):
        return "java"
    else:
        return None

def copy_directory_from_container(container_id, src_path, dst_path):
    container = client.containers.get(container_id)
    exec_result = container.exec_run(f"ls -la {src_path}")
    if exec_result.exit_code != 0:
        raise ValueError(f"Path {src_path} does not exist in the container.")

    if os.path.exists(dst_path):
        shutil.rmtree(dst_path)
    os.makedirs(dst_path, exist_ok=True)

    stream, stats = container.get_archive(src_path)
    temp_tar = os.path.join(dst_path, "temp_archive.tar")

    with open(temp_tar, "wb") as f:
        for chunk in stream:
            f.write(chunk)

    with tarfile.open(temp_tar) as tar:
        tar.extractall(path=dst_path, numeric_owner=True)

    os.remove(temp_tar)

def copy_file_from_container(container_id, src_path, dst_path):
    container = client.containers.get(container_id)

    # Check if the file exists inside the container
    exec_result = container.exec_run(f"test -f {src_path}")
    if exec_result.exit_code != 0:
        raise FileNotFoundError(f"File {src_path} does not exist in the container.")

    # Retrieve the file as a tar stream
    stream, _ = container.get_archive(src_path)
    temp_tar = dst_path + ".tar"

    # Write the tar stream to a temporary file
    with open(temp_tar, "wb") as f:
        for chunk in stream:
            f.write(chunk)

    # Extract the file content and write it directly to dst_path
    with tarfile.open(temp_tar) as tar:
        members = tar.getmembers()
        file_member = members[0]
        fileobj = tar.extractfile(file_member)
        if fileobj is None:
            raise RuntimeError("Failed to extract file from the tar archive.")

        with open(dst_path, "wb") as out_file:
            out_file.write(fileobj.read())

    os.remove(temp_tar)


def copy_file_to_container(container_id, local_path, container_path):
    """
    Copy a single file to the container.
    container_path should be the full path to the target file in the container.
    """
    container = client.containers.get(container_id)

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        tar.add(local_path, arcname=os.path.basename(container_path))
    data.seek(0)

    container_dir = os.path.dirname(container_path)
    container.exec_run(f"mkdir -p {container_dir}")
    container.put_archive(container_dir, data.getvalue())


def copy_directory_to_container(container_id, src_path, dst_path):
    """
    Copies a directory from the host (src_path) into the container (dst_path).
    If dst_path does not exist, it is created. Contents are overwritten.
    """
    container = client.containers.get(container_id)

    mkdir_cmd = f"mkdir -p {dst_path}"
    exit_code, output = container.exec_run(mkdir_cmd)
    if exit_code != 0:
        raise RuntimeError(f"Failed to create directory {dst_path} in container: {output.decode()}")

    mem_tar = io.BytesIO()
    with tarfile.open(fileobj=mem_tar, mode='w') as tar:
        tar.add(src_path, arcname="") 
    mem_tar.seek(0)

    container.put_archive(dst_path, mem_tar.getvalue())


def get_container(image_name):
    """Create and start a new container from the specified image."""
    container = client.containers.run(image_name, command="bash", stdin_open=True, tty=True, detach=True)
    print(f"Container {container.id} started from image {image_name}")
    return container.id

def run_poc(container_id, poc_command):
    container = client.containers.get(container_id)
    exec_result = container.exec_run(poc_command, stdout=True, stderr=True)
    output = exec_result.output.decode("utf-8-sig", errors="ignore")
    return output

def compile_target(container_id, compile_command):
    container = client.containers.get(container_id)
    workdir = get_work_dir(container_id)
    compile_command = f"bash -c 'cd {workdir} && arvo compile'"
    exec_result = container.exec_run(compile_command, stdout=True, stderr=True)
    output = exec_result.output.decode()
    return output

def delete_container(container_id, max_wait=10):
    try:
        container = client.containers.get(container_id)
        container.remove(force=True, v=True)
    except NotFound:
        print(f"[info] container {container_id} already gone")
        return
    except APIError as e:
        print(f"[warn] docker API error on {container_id}: {e.explanation}")
        return
    for _ in range(max_wait):
        try:
            client.containers.get(container_id)
            time.sleep(1)
        except NotFound:
            print(f"Container {container_id} removed")
            return
    print(f"[warn] container {container_id} still listed after {max_wait}s")


def extract_file_from_container(container_id, filepath):
    """
    Extracts the content of the specified file from the container.
    Returns the file content as a decoded string.
    """
    container = client.containers.get(container_id)
    stream, _ = container.get_archive(filepath)
    file_data = b""
    for chunk in stream:
        file_data += chunk
    tar_stream = io.BytesIO(file_data)
    with tarfile.open(fileobj=tar_stream) as tar:
        member = tar.getmembers()[0]
        f = tar.extractfile(member)
        content = f.read().decode()
    return content

def extract_file_from_container_bytes(container_id, filepath):
    """
    Extracts the content of the specified file from the container.
    Returns the file content as bytes.
    """
    container = client.containers.get(container_id)
    stream, _ = container.get_archive(filepath)
    file_data = b""
    for chunk in stream:
        file_data += chunk
    tar_stream = io.BytesIO(file_data)
    with tarfile.open(fileobj=tar_stream) as tar:
        member = tar.getmembers()[0]
        f = tar.extractfile(member)
        content = f.read()
    return content

def create_tar_bytes(file_content, arcname):
    """
    Packs the given file content into a tar archive.
    
    :param file_content: The file content as a string.
    :param arcname: The name of the file inside the archive.
    :return: The tar archive as a byte string.
    """
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode='w') as tar:
        file_bytes = file_content.encode()
        tarinfo = tarfile.TarInfo(name=arcname)
        tarinfo.size = len(file_bytes)
        tar.addfile(tarinfo, io.BytesIO(file_bytes))
    tar_stream.seek(0)
    return tar_stream.read()

def patch_search_replace(container_id, file, search, replace):
    """
    Replaces all occurrences of 'search' with 'replace' in the specified file inside the container.
    """
    container = client.containers.get(container_id)
    
    # Extract the file content from the container
    file_content = extract_file_from_container(container, file)
    
    # Replace the search string with the replace string
    modified_content = file_content.replace(search, replace)
    
    # Create a tar archive of the modified content
    archive_data = create_tar_bytes(modified_content, arcname=file.split("/")[-1])
    
    # Copy the modified content back to the container
    destination_dir = "/".join(file.split("/")[:-1])
    if not destination_dir:
        destination_dir = "/"
    container.put_archive(destination_dir, archive_data)

def patch_file_func(container_id, files_func_to_content, lang="c"):
    """
    Replaces a function in a file inside the container with new content.
    Input:
    - container_id: The ID of the container.
    - files_func_to_content: A dictionary containing the file content to replace.
             The key should be in the format 'filepath__xx__functionname'.
             The value should be the new function content.
    - lang: The language of the file. Default is 'c'.
    """
    container = client.containers.get(container_id)
    
    for key, new_function_content in files_func_to_content.items():
        parts = key.split("__xx__")
        if len(parts) != 2:
            print(f"Key {key} is not in the correct format. Expected format: 'filepath__xx__functionname'")
            continue
        filepath, function_name = parts
        
        # Extract the file content from the container.
        file_content = extract_file_from_container(container, filepath)
        
        # Use Tree-sitter to obtain function information from the file.
        functions = get_function_info(file_content, lang)
        if function_name not in functions:
            print(f"Initial try, Function {function_name} not found in file {filepath}")
            print("Trying to do partial matching, the result may be inaccurate")
            func_name = function_name.split("::")[-1]
            if func_name in functions:
                function_name = func_name
            else:
                print("Trying to do partial matching with looser rules")
                potential_funcs = [func for func in functions if func_name in func or func in func_name]
                # get the distance between the function name and the potential function name
                if potential_funcs:
                    potential_funcs.sort(key=lambda f: abs(len(f) - len(func_name)))
                    function_name = potential_funcs[0]
                else:
                    print(f"Function {function_name} finally not found in file {filepath}")
                    continue
            
        # TODO: FIXME: if there are multiple functions with the same name, we need to find the one that matches the line number
        start_line, end_line = functions[function_name][0]
        start_index = start_line - 1
        end_index = end_line  
        
        # Replace
        file_lines = file_content.splitlines()
        new_function_lines = new_function_content.splitlines()
        modified_lines = file_lines[:start_index] + new_function_lines + file_lines[end_index:]
        modified_file_content = "\n".join(modified_lines)
        
        # copy back
        archive_data = create_tar_bytes(modified_file_content, arcname=filepath.split("/")[-1])
        destination_dir = "/".join(filepath.split("/")[:-1])
        if not destination_dir:
            destination_dir = "/"
        container.put_archive(destination_dir, archive_data)
        print(f"Updated function {function_name} in file {filepath} in container {container_id}")

def check_crash(output):
    """
    Check if the poc still crashes the program.
    """
    return "sanitizer" in output.lower()

def get_function_content(container_id, key, lang="c", line_in_func = -1):
    """
    Retrieves the content of a specific function from a file inside the container.
    
    Input:
      - container_id: The ID of the Docker container.
      - key: A string in the format 'filepath__xx__functionname'.
      - lang: The programming language of the file. Default is 'c'.
    
    Returns:
      The content of the function as a string, or None if the function is not found.
    """
    container = client.containers.get(container_id)
    
    parts = key.split("__xx__")
    if len(parts) != 2:
        print(f"Key {key} is not in the correct format. Expected format: 'filepath__xx__functionname'")
        return "", -1, -1
    filepath, function_name = parts
    
    # Extract the file content from the container
    file_content = extract_file_from_container(container, filepath)
    # Use Tree-sitter to obtain function information from the file
    functions = get_function_info(file_content, lang)
    if function_name not in functions:
        print(f"Initial try, Function {function_name} not found in file {filepath}")
        print("Trying to do partial matching, the result may be inaccurate")
        func_name = function_name.split("::")[-1]
        if func_name in functions:
            function_name = func_name
        else:
            return "", -1, -1
    # line_in_func helps to decide which function to extract when there are multiple functions with the same name
    if line_in_func != -1:
        for scope in functions[function_name]:
            start_line, end_line = scope
            if start_line <= line_in_func <= end_line:
                break
    else:
        start_line, end_line = functions[function_name][-1]
    
    # Split the file content into lines and extract the function content
    file_lines = file_content.splitlines()
    function_lines = file_lines[start_line - 1:end_line]  # convert 1-indexed to 0-indexed
    function_content = "\n".join(function_lines)
    
    return function_content, start_line, end_line

def get_file_content(container_id, filepath):
    """
    Retrieves the content of a file inside the container.
    
    Input:
      - container_id: The ID of the Docker container.
      - filepath: The path to the file inside the container.
    
    Returns:
      The content of the file as a string, or None if the file is not found.
    """
    container = client.containers.get(container_id)
    
    # Extract the file content from the container
    file_content = extract_file_from_container(container, filepath)
    
    return file_content

def run_command_in_container(container_id, command):
    """
    Run a command inside the container.
    """
    container = client.containers.get(container_id)
    full_command = f"/bin/bash -c \"{command}\""
    exec_result = container.exec_run(full_command, stdout=True, stderr=True)
    output = exec_result.output.decode('latin-1')
    exit_code = exec_result.exit_code
    
    return output, exit_code

def get_work_dir(container_id):
    """
    Get the working directory of the container.
    """
    container = client.containers.get(container_id)
    work_dir, exit_code = run_command_in_container(container_id, "pwd").strip()
    return work_dir

def wrap_in_cd(command, basedir):
    if basedir:
        return f"bash -c 'cd {basedir} && {command}'"
    return command

def is_git_repo(path):
    return os.path.isdir(os.path.join(path, '.git'))