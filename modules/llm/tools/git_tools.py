import os
from typing import Dict, Any, List, Optional
from git import Repo, exc
from langchain_core.tools import tool
from modules.llm.tools.tool_context import get_repo_path

def _get_repo() -> Repo:
    """Helper to initialize the GitPython Repo object."""
    try:
        return Repo(str(get_repo_path()), search_parent_directories=True)
    except exc.InvalidGitRepositoryError:
        raise RuntimeError("Current directory is not a git repository.")

@tool
def git_status(expand_untracked: bool = False) -> Dict[str, Any]:
    """Returns a structured summary of the current git status.

    Args:
        expand_untracked: If True, lists individual files within untracked directories.
    """
    try:
        repo = _get_repo()
        try:
            branch = repo.active_branch.name
        except (TypeError, exc.GitCommandError):
            try:
                branch = f"DETACHED_{repo.head.commit.hexsha[:7]}"
            except Exception:
                branch = "unknown"

        try:
            staged = [item.a_path for item in repo.index.diff("HEAD")]
        except (exc.BadName, exc.GitCommandError):
            staged = []

        modified = [item.a_path for item in repo.index.diff(None)]

        mode = "-uall" if expand_untracked else "-unormal"
        status_raw = repo.git.status("--porcelain", mode)
        untracked = [line[3:] for line in status_raw.splitlines() if line.startswith("?? ")]

        return {
            "branch": branch,
            "staged": sorted(list(set(staged))),
            "modified": sorted(list(set(modified))),
            "untracked": sorted(untracked)
        }
    except Exception as e:
        return {"error": f"Error getting git status: {str(e)}"}

def _parse_hunks(diff_text: str) -> List[Dict[str, Any]]:
    """Helper to parse unified diff hunks into structured format."""
    if not diff_text:
        return []

    hunks = []
    current_hunk = None
    ln_old = 0
    ln_new = 0

    for line in diff_text.splitlines():
        if line.startswith('@@ '):
            parts = line.split(' ')
            if len(parts) >= 4:
                old_part = parts[1][1:].split(',')[0]
                new_part = parts[2][1:].split(',')[0]
                try:
                    old_start = int(old_part)
                    new_start = int(new_part)
                    current_hunk = {
                        "header": line,
                        "old_start": old_start,
                        "new_start": new_start,
                        "lines": []
                    }
                    hunks.append(current_hunk)
                    ln_old = old_start
                    ln_new = new_start
                except ValueError:
                    current_hunk = None
        elif current_hunk is not None:
            if line.startswith(' '):
                current_hunk["lines"].append([" ", line[1:], ln_new])
                ln_old += 1
                ln_new += 1
            elif line.startswith('-'):
                current_hunk["lines"].append(["-", line[1:], ln_old])
                ln_old += 1
            elif line.startswith('+'):
                current_hunk["lines"].append(["+", line[1:], ln_new])
                ln_new += 1
    return hunks

def _get_diff_status(change_type: str) -> str:
    """Map GitPython change types to readable status strings."""
    mapping = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied"}
    return mapping.get(change_type, "modified")

def _process_diffs(diffs) -> List[Dict[str, Any]]:
    """Common helper to transform GitPython Diff objects into structured results."""
    results = []
    for d in diffs:
        results.append({
            "path": d.b_path if d.b_path else d.a_path,
            "status": _get_diff_status(d.change_type),
            "binary": d.diff is None,
            "hunks": _parse_hunks(d.diff.decode('utf-8')) if d.diff else []
        })
    return results

@tool
def git_diff(target: Optional[str] = None, files: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Returns a structured git diff.

    Args:
        target: Optional branch or commit to diff against.
               If provided, compares the common ancestor of target and HEAD to the working tree.
               If None (default), returns all uncommitted changes (staged + unstaged + untracked).
        files: Optional list of file paths to filter the diff.
    """
    try:
        repo = _get_repo()
        results = []

        # Determine the base tree for comparison
        if target:
            try:
                repo.git.rev_parse("--verify", target)
            except exc.GitCommandError:
                return [{"error": f"Target '{target}' not found."}]

            merge_bases = repo.merge_base(target, "HEAD")
            if not merge_bases:
                return [{"error": f"No common ancestor between HEAD and {target}."}]
            base_tree = merge_bases[0]
        else:
            try:
                base_tree = repo.head.commit
            except (exc.BadName, AttributeError, exc.GitCommandError):
                base_tree = None

        # Compare base_tree to working tree (None)
        # This captures staged + unstaged changes (if base_tree exists)
        if base_tree:
            try:
                diffs = base_tree.diff(None, paths=files, create_patch=True)
                results = _process_diffs(diffs)
            except Exception:
                results = []

        # Manually add untracked (new) files
        untracked = repo.untracked_files
        if files:
            untracked = [f for f in untracked if f in files]

        for f_path in untracked:
            try:
                full_path = os.path.join(repo.working_dir, f_path or "")
                if not os.path.isfile(full_path):
                    continue

                is_binary = False
                try:
                    with open(full_path, 'tr') as check_f:
                        check_f.read(1024)
                except UnicodeDecodeError:
                    is_binary = True

                hunks = []
                if not is_binary:
                    with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                        lines = f.read().splitlines()
                    hunks = [{
                        "header": f"@@ -0,0 +1,{len(lines)} @@",
                        "old_start": 0,
                        "new_start": 1,
                        "lines": [["+", line, i + 1] for i, line in enumerate(lines)]
                    }]

                results.append({
                    "path": f_path,
                    "status": "added",
                    "binary": is_binary,
                    "hunks": hunks
                })
            except Exception:
                continue

        return results
    except Exception as e:
        return [{"error": f"Error performing git diff: {str(e)}"}]

GIT_TOOLS = [git_status, git_diff]
