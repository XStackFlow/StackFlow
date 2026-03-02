import os
import shutil
from langchain_core.tools import tool
from modules.llm.tools.tool_context import resolve_path

@tool
def create_file(file_path: str, content: str = "") -> str:
    """Creates a new file with the specified content.
    Returns an error if the file already exists.

    Args:
        file_path: Path where the file should be created.
        content: Initial text content for the file.
    """
    try:
        file_path = resolve_path(file_path)
        if os.path.exists(file_path):
            return f"Error: File '{file_path}' already exists. Use 'patch_file' for modifications."

        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return f"Successfully created new file: {file_path}"
    except Exception as e:
        return f"Error creating file '{file_path}': {str(e)}"

@tool
def patch_file(file_path: str, old_content: str, new_content: str) -> str:
    """Swaps a specific code block for the first occurrence in a file.
    This is the safest way to modify existing files.

    Args:
        file_path: Path to the file to modify.
        old_content: The exact block of text to be replaced.
        new_content: The new text to put in its place.
    """
    try:
        file_path = resolve_path(file_path)
        if not os.path.exists(file_path):
            return f"Error: File '{file_path}' not found."

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for occurrences
        occurrence_count = content.count(old_content)

        if occurrence_count == 0:
            return f"Error: The exact text to replace was not found in {file_path}. Please check indentation and whitespace."

        if occurrence_count > 1:
            return f"Error: Found {occurrence_count} occurrences of the text in {file_path}. Please provide more specific context to target a single occurrence."

        # replace(..., 1) ensures we only swap the FIRST occurrence
        new_file_content = content.replace(old_content, new_content, 1)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_file_content)

        return f"Successfully updated {file_path} (swapped first occurrence)"
    except Exception as e:
        return f"Error modifying file '{file_path}': {str(e)}"

@tool
def move_file(source: str, destination: str) -> str:
    """Moves or renames a file from source to destination.

    Args:
        source: Current file path.
        destination: New file path.
    """
    try:
        src = resolve_path(source)
        dst = resolve_path(destination)
        if not os.path.exists(src):
            return f"Error: Source '{source}' not found."
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.move(src, dst)
        return f"Successfully moved {source} to {destination}"
    except Exception as e:
        return f"Error moving file: {e}"


@tool
def copy_file(source: str, destination: str) -> str:
    """Copies a file from source to destination.

    Args:
        source: Source file path.
        destination: Destination file path.
    """
    try:
        src = resolve_path(source)
        dst = resolve_path(destination)
        if not os.path.exists(src):
            return f"Error: Source '{source}' not found."
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copy2(src, dst)
        return f"Successfully copied {source} to {destination}"
    except Exception as e:
        return f"Error copying file: {e}"


@tool
def file_delete(file_path: str) -> str:
    """Deletes a file at the specified path.

    Args:
        file_path: Path to the file to delete.
    """
    try:
        resolved = resolve_path(file_path)
        if not os.path.exists(resolved):
            return f"Error: File '{file_path}' not found."
        os.remove(resolved)
        return f"Successfully deleted {file_path}"
    except Exception as e:
        return f"Error deleting file: {e}"


WRITE_TOOLS = [create_file, patch_file, move_file, copy_file, file_delete]
