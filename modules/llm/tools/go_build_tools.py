import subprocess
from langchain_core.tools import tool
from modules.llm.tools.tool_context import get_repo_path

@tool
def go_build(path: str) -> str:
    """Runs 'go build' on the specified path.

    Args:
        path: The package path or file to build.
    """
    try:
        result = subprocess.run(
            ["go", "build", "-o", "/dev/null", path], capture_output=True, text=True, timeout=300,
            cwd=str(get_repo_path())
        )
        if result.returncode == 0:
            return "Build successful (no binary generated)"
        return f"Build failed:\n{result.stderr}\n{result.stdout}"
    except Exception as e:
        return f"Error running go build: {str(e)}"

@tool
def go_test(run: str, path: str) -> str:
    """Runs 'go test' on the specified path, filtered by a test name regex.

    Args:
        run: Required regex to filter tests to run (equivalent to -run flag).
        path: The package path to test.
    """
    try:
        cmd = ["go", "test", "-v", path, "-run", run]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            cwd=str(get_repo_path())
        )
        output = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0:
            return f"Tests passed:\n{output}"
        return f"Tests failed:\n{output}"
    except Exception as e:
        return f"Error running go test: {str(e)}"

@tool
def golangci_lint(file_path: str) -> str:
    """Runs 'golangci-lint run' on a single Go file.

    Args:
        file_path: The specific Go file to lint.
    """
    if not file_path.endswith(".go"):
        return f"Error: golangci_lint can only be run on .go files. Got: {file_path}"

    try:
        result = subprocess.run(
            ["golangci-lint", "run", file_path], capture_output=True, text=True, timeout=600,
            cwd=str(get_repo_path())
        )
        if result.returncode == 0:
            return "Linting successful"
        return f"Linting failed:\n{result.stderr}\n{result.stdout}"
    except Exception as e:
        return f"Error running golangci-lint: {str(e)}"

# Export all Go build tools as a list
GO_BUILD_TOOLS = [go_build, go_test, golangci_lint]
