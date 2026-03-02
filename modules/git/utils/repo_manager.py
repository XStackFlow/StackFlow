"""Repository manager for cloning and managing ephemeral GitHub workspaces."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from src.utils.exceptions import RepositoryError
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


def get_default_branch(repo_path: Path) -> str:
    """Get the default branch name for a git repository.
    
    Raises:
        RepositoryError: If default branch cannot be determined.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
         raise RepositoryError(f"Failed to determine default branch: {result.stderr.strip()}")
    
    return result.stdout.strip().replace("origin/", "")


def get_or_clone_repository(repo_url: str, repo_name: str, temp_prefix: str = "stackflow_") -> Path:
    """
    Ensures a repository is keyed to WORKING_DIR / repo_name.
    
    This function manages the mapping between a logical repository identifier (repo_name)
    and an ephemeral physical workspace (temporary directory).
    
    Args:
        repo_url: The git URL to clone from.
        repo_name: The logical identifier (key) for the repository in the working directory.
                  Usually contains the sanitized repo name and thread/session ID.
        temp_prefix: Custom prefix for the temporary directory if a fresh clone is needed.
        
    Returns:
        Path: The absolute path to the repository inside the WORKING_DIR.
    """
    working_dir_env = os.getenv("WORKING_DIR")
    if not working_dir_env:
        raise ValueError("WORKING_DIR environment variable is required.")
    working_dir = Path(working_dir_env)
    working_dir.mkdir(parents=True, exist_ok=True)

    if not repo_url.endswith(".git"):
        repo_url = repo_url + ".git"
    
    # The 'Key' is the path in the working directory (symlink or real dir)
    keyed_path = working_dir / repo_name
    
    # 1. Check if the "key" already points to a healthy workspace
    if keyed_path.is_symlink():
        target = Path(os.readlink(keyed_path))
        if target.exists():
            logger.info("Using existing workspace for '%s' (linked to: %s)", repo_name, target)
            return keyed_path
    elif keyed_path.is_dir() and (keyed_path / ".git").exists():
        logger.info("Using existing directory workspace for '%s' at %s", repo_name, keyed_path)
        return keyed_path

    # 2. If no healthy workspace exists, create a fresh one in a temporary directory
    temp_dir = Path(tempfile.mkdtemp(prefix=temp_prefix))
    logger.info("Cloning '%s' into fresh ephemeral workspace: %s", repo_name, temp_dir)
    
    # Clone the repository
    clone_res = subprocess.run(
        ["git", "clone", "--single-branch", repo_url, "."], 
        cwd=temp_dir, 
        check=False, 
        capture_output=True, 
        timeout=300
    )
    
    if clone_res.returncode != 0:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise RepositoryError(f"Failed to clone repository '{repo_name}': {clone_res.stderr.strip()}")
        
    # 3. Key the repository to the working directory (via symlink)
    if keyed_path.exists() or keyed_path.is_symlink():
        if keyed_path.is_symlink():
            keyed_path.unlink()
        else:
            shutil.rmtree(keyed_path)
    
    keyed_path.symlink_to(temp_dir)
    
    logger.info("Repository '%s' is now keyed to %s", repo_name, keyed_path)
    return keyed_path


def checkout_pr_branch(repo_path: Path, branch_name: str) -> None:
    """Ensure the repository is on the specified branch and synced with origin.
    Also fetches the default branch to ensure accurate diffs.
    """
    # 1. Fetch branches separately
    default_branch = get_default_branch(repo_path)
    logger.info("Syncing default branch '%s'...", default_branch)
    subprocess.run(["git", "fetch", "origin", f"+refs/heads/{default_branch}:refs/remotes/origin/{default_branch}"], cwd=str(repo_path), capture_output=True, check=False)
    
    logger.info("Fetching target branch '%s'...", branch_name)
    fetch_res = subprocess.run(
        ["git", "fetch", "origin", f"+refs/heads/{branch_name}:refs/remotes/origin/{branch_name}"],
        cwd=str(repo_path),
        capture_output=True,
        timeout=60,
        check=False
    )

    # 2. Decider: Use Remote Fetch result as primary signal
    if fetch_res.returncode == 0:
        logger.info("Branch '%s' exists on remote, ensuring latest...", branch_name)
        
        # Use -f to force checkout and discard any uncommitted changes in tracked files.
        # -B will create the branch or reset it if it exists to match the fetched remote HEAD.
        checkout_result = subprocess.run(
            ["git", "checkout", "-f", "-B", branch_name, "FETCH_HEAD"], 
            cwd=str(repo_path), 
            capture_output=True,
            text=True,
            check=False
        )
        
        if checkout_result.returncode != 0:
            raise RepositoryError(
                f"Git checkout failed for branch '{branch_name}':\n"
                f"STDOUT: {checkout_result.stdout}\n"
                f"STDERR: {checkout_result.stderr}"
            )
        
        # Clean untracked files
        subprocess.run(["git", "clean", "-fd"], cwd=str(repo_path), capture_output=True, check=False)
        
        # Set upstream tracking manually to bypass the "is not a branch" check
        # which occurs in single-branch clones.
        subprocess.run(["git", "config", f"branch.{branch_name}.remote", "origin"], cwd=str(repo_path), check=True)
        subprocess.run(["git", "config", f"branch.{branch_name}.merge", f"refs/heads/{branch_name}"], cwd=str(repo_path), check=True)
        logger.info("Successfully set manual upstream tracking for '%s' to origin/%s", branch_name, branch_name)
    else:
        logger.info("Branch '%s' not found on remote, ensuring local starts from origin/%s...", branch_name, default_branch)
        
        # Use -f to force checkout and discard any uncommitted changes in tracked files.
        # -B here creates or resets the local branch to the latest fetched default branch.
        checkout_result = subprocess.run(
            ["git", "checkout", "-f", "-B", branch_name, f"origin/{default_branch}"], 
            cwd=str(repo_path), 
            capture_output=True,
            text=True,
            check=False
        )
        
        if checkout_result.returncode != 0:
            raise RepositoryError(
                f"Git checkout failed for branch '{branch_name}' from origin/{default_branch}:\n"
                f"STDOUT: {checkout_result.stdout}\n"
                f"STDERR: {checkout_result.stderr}"
            )
        
        # Clean untracked files
        subprocess.run(["git", "clean", "-fd"], cwd=str(repo_path), capture_output=True, check=False)


def commit_and_push_changes(
    repo_path: str,
    commit_message: str,
    new_branch: Optional[str] = None,
    allow_empty: bool = False
) -> None:
    """Commit and optionally push changes to a branch.
    
    Args:
        repo_path: Path to the repository
        commit_message: Commit message (will be prefixed with [AI Generated])
        new_branch: If None, push to current branch. If string, push to that branch with -u flag (for new branches).
        allow_empty: Whether to allow empty commits (default: False)
        
    Raises:
        ValueError: If there are no changes to commit and allow_empty is False
        RepositoryError: If git operations fail
    """
    repo = Path(repo_path)
    
    # Stage all changes
    logger.info("Staging changes...")
    result = subprocess.run(
        ["git", "add", "-A"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False
    )
    if result.returncode != 0:
        raise RepositoryError(f"Failed to stage changes: {result.stderr.strip()}")
    
    # Check if there are changes to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False
    )
    
    # If returncode is 0, there are no changes
    if result.returncode == 0 and not allow_empty:
        raise ValueError("No changes to commit")
    
    # Detect if we're completing a merge (use git's auto-generated merge message)
    merge_head = repo / ".git" / "MERGE_HEAD"
    is_merge = merge_head.exists()

    if is_merge:
        logger.info("Completing merge commit (using git's default merge message)")
        commit_cmd = ["git", "commit", "--no-edit"]
    else:
        prefixed_message = f"[AI Generated] {commit_message}"
        logger.info("Committing changes with message: %s", prefixed_message)
        commit_cmd = ["git", "commit", "-m", prefixed_message]

    if allow_empty:
        commit_cmd.append("--allow-empty")
        
    result = subprocess.run(
        commit_cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False
    )
    if result.returncode != 0:
        # Check if it's just "nothing to commit" (which is fine if allow_empty is True)
        error_output = (result.stderr + " " + result.stdout).lower()
        if allow_empty and ("nothing to commit" in error_output or "no changes added to commit" in error_output):
            logger.info("No changes to commit")
            return
        raise RepositoryError(f"Failed to commit changes: {result.stderr.strip()}")
    
    logger.info("Successfully committed changes")
    
    # Push changes
    if new_branch is not None:
        # Push to new branch with -u flag
        logger.info("Pushing new branch %s to remote...", new_branch)
        result = subprocess.run(
            ["git", "push", "-u", "origin", new_branch],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
            check=False
        )
        if result.returncode != 0:
            raise RepositoryError(f"Failed to push branch {new_branch}: {result.stderr.strip()}")
        logger.info("Successfully pushed branch %s", new_branch)
    else:
        # Push to current branch
        logger.info("Pushing changes to remote...")
        result = subprocess.run(
            ["git", "push"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
            check=False
        )
        if result.returncode != 0:
            raise RepositoryError(f"Failed to push changes: {result.stderr.strip()}")
        logger.info("Successfully pushed changes")
