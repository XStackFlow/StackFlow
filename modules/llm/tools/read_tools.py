import os
import subprocess
from langchain_core.tools import tool
from modules.llm.const import READ_FILE_MAX_LINES, SEARCH_CODEBASE_MAX_RESULTS
from modules.llm.tools.tool_context import resolve_path, get_repo_path

@tool
def read_file(file_path: str) -> str:
    """Reads the content of a file. If the file is too large, it returns an error.

    Args:
        file_path: Path to the file to read.
    """
    try:
        file_path = resolve_path(file_path)
        if not os.path.exists(file_path):
            return f"Error: File '{file_path}' not found."

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        if len(lines) > READ_FILE_MAX_LINES:
            return (f"Error: File too large to read ({len(lines)} lines). "
                    f"The maximum allowed is {READ_FILE_MAX_LINES} lines. "
                    f"Please use 'read_file_segment' to read this file in chunks.")

        return "".join(lines)
    except Exception as e:
        return f"Error reading file '{file_path}': {str(e)}"

@tool
def read_file_segment(file_path: str, start_line: int, end_line: int) -> str:
    """Reads a specific segment of a file (1-indexed, inclusive).

    Args:
        file_path: Path to the file.
        start_line: Starting line number (1-indexed).
        end_line: Ending line number (1-indexed, inclusive).
    """
    try:
        file_path = resolve_path(file_path)
        if not os.path.exists(file_path):
            return f"Error: File '{file_path}' not found."

        if start_line < 1:
            return "Error: start_line must be >= 1."

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        segment = lines[start_line-1 : end_line]
        if not segment:
            return f"Warning: No lines found in range [{start_line}, {end_line}]. File has {len(lines)} lines."

        return "".join(segment)
    except Exception as e:
        return f"Error reading file segment: {str(e)}"

@tool
def search_codebase(directory: str, query: str, file_pattern: str = "*") -> str:
    """
    Searches for a specific string or pattern in files within a directory.
    Equivalent to 'grep -r'. Useful for finding where functions or classes are defined.

    Args:
        directory: The relative path to search in (e.g., "." or "src").
        query: The string to search for.
        file_pattern: Optional glob pattern to filter files (e.g., "*.py", "*.ts").
    """
    try:
        resolved_dir = resolve_path(directory)
        if not os.path.exists(resolved_dir):
            return f"Error: Directory '{directory}' not found."

        # Build grep command with file pattern filtering
        cmd = ["grep", "-rn", "--color=never", query, resolved_dir]

        # Add file pattern filter if specified and not the default "*"
        if file_pattern and file_pattern != "*":
            cmd.extend(["--include", file_pattern])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(get_repo_path())
        )

        # grep returns 0 if matches found, 1 if no matches, >1 for errors
        if result.returncode == 1:
            return f"No matches found for '{query}' in {directory}"

        if result.returncode > 1:
            return f"Error searching: {result.stderr}"

        output = result.stdout.strip()

        # Limit output to prevent context overflow
        lines = output.split('\n')
        if len(lines) > SEARCH_CODEBASE_MAX_RESULTS:
            return (f"Found {len(lines)} matches (showing first {SEARCH_CODEBASE_MAX_RESULTS}):\n" +
                   '\n'.join(lines[:SEARCH_CODEBASE_MAX_RESULTS]) +
                   f"\n\n... {len(lines) - SEARCH_CODEBASE_MAX_RESULTS} more matches omitted")

        return f"Found {len(lines)} matches:\n{output}"

    except subprocess.TimeoutExpired:
        return f"Error: Search timed out after 30 seconds"
    except Exception as e:
        return f"Error searching codebase: {str(e)}"

@tool
def list_directory(dir_path: str = ".") -> str:
    """Lists files and directories in the given path.

    Args:
        dir_path: Relative or absolute directory path to list (default: ".").
    """
    try:
        resolved = resolve_path(dir_path)
        if not os.path.isdir(resolved):
            return f"Error: '{dir_path}' is not a directory or does not exist."
        entries = sorted(os.listdir(resolved))
        return "\n".join(entries) if entries else "(empty directory)"
    except Exception as e:
        return f"Error: {e}"


@tool
def file_search(pattern: str, dir_path: str = ".") -> str:
    """Searches for files matching a glob pattern recursively.

    Args:
        pattern: Glob pattern to match file names (e.g., '*.py', 'config*').
        dir_path: Directory to search in (default: ".").
    """
    import fnmatch
    try:
        resolved = resolve_path(dir_path)
        if not os.path.isdir(resolved):
            return f"No files found for pattern {pattern} in directory {dir_path}"
        matches = []
        for root, dirs, files in os.walk(resolved):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in files:
                if fnmatch.fnmatch(fname, pattern):
                    rel = os.path.relpath(os.path.join(root, fname), resolved)
                    matches.append(rel)
        if not matches:
            return f"No files found for pattern {pattern} in directory {dir_path}"
        return "\n".join(sorted(matches))
    except Exception as e:
        return f"Error searching: {e}"


READ_TOOLS = [read_file, read_file_segment, search_codebase, list_directory, file_search]
