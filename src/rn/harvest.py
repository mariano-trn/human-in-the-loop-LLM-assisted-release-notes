# src/rn/harvest.py
"""
Git harvesting stage (deterministic).

Purpose:
- Clone (once) and update (fetch) a public GitHub repo into a local cache directory.
- Collect commit metadata between two refs (tags/branches/SHAs): (from_ref, to_ref]
- Optionally collect the list of files changed per commit (useful signal for filtering/LLM prompts)

Design goals:
- Deterministic & reproducible: relies on git, no LLM involvement.
- Windows-safe: no shell=True, explicit path handling.
- Actionable errors: GitError includes stdout/stderr and cwd for debugging.
- Caching: avoids repeated clones and enables fast iteration.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any

# Immutable record for a single commit change. We later convert it to dict for JSON serialization.
@dataclass(frozen=True)
class CommitChange:
    sha: str
    author_name: str
    author_email: str
    author_date: str  # ISO-like string from git
    subject: str
    body: str
    files: List[str]
    url: Optional[str] = None  # commit URL if repo is GitHub


class GitError(RuntimeError):
    pass


def _run_git(args: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run git command in cwd and return CompletedProcess. Raises GitError on failure if check=True.
    Windows-safe (no shell).
    Note: we keep `shell=False` to avoid quoting issues and security pitfalls on Windows.
    """
    cmd = ["git"] + args
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed or not available in PATH.") from e

    if check and proc.returncode != 0:
        raise GitError(
            f"Git command failed: {' '.join(cmd)}\n"
            f"cwd={cwd}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc

# Cache key strategy:
# Convert repo URL into a stable, filesystem-safe directory name so multiple repos can co-exist in .cache/.
def _safe_repo_dirname(repo_url: str) -> str:
    """
    Create a stable folder name from a repo URL.
    Example: https://github.com/getlago/lago -> github.com_getlago_lago
    """
    repo_url = repo_url.strip().rstrip("/")
    repo_url = repo_url.replace("https://", "").replace("http://", "")
    repo_url = repo_url.replace("git@", "").replace(":", "/")
    repo_url = re.sub(r"[^a-zA-Z0-9._/-]+", "_", repo_url)
    return repo_url.replace("/", "_")


def ensure_repo(repo_url: str, cache_dir: Path) -> Path:
    """
    Ensure a local clone exists and is up-to-date.

    Why caching:
    - repeated runs are fast (no reclone)
    - enables deterministic diffs between refs
    - aligns with common tooling patterns (.cache as ephemeral, reproducible storage)
    """

    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    repo_dir = (cache_dir / _safe_repo_dirname(repo_url)).resolve()

    # If path exists but is not a directory -> actionable error
    if repo_dir.exists() and not repo_dir.is_dir():
        raise GitError(f"Expected repo_dir to be a directory but found a file: {repo_dir}")

    if not repo_dir.exists():
        # clone (no --quiet, we want error output if it fails)
        proc = _run_git(["clone", repo_url, str(repo_dir)], cwd=cache_dir, check=False)
        if proc.returncode != 0:
            raise GitError(
                f"git clone failed (code={proc.returncode})\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )

        # Defensive check after clone
        if not repo_dir.exists() or not repo_dir.is_dir():
            raise GitError(
                f"Clone reported success but repo directory was not created: {repo_dir}\n"
                "This usually indicates a permissions/path issue on Windows."
            )

    # sanity check it's a git repo
    if not (repo_dir / ".git").exists():
        raise GitError(f"Cache path exists but is not a git repo: {repo_dir}")

    # fetch updates (tags included)
    proc = _run_git(["fetch", "--all", "--tags", "--prune"], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        raise GitError(
            f"git fetch failed (code={proc.returncode})\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )

    return repo_dir

# Convenience fallback: if a user passes a branch name, try origin/<branch>.
def resolve_ref(repo_dir: Path, ref: str) -> str:
    """
    Resolve a ref (tag/branch/SHA) to a full commit SHA.
    """
    ref = ref.strip()
    proc = _run_git(["rev-parse", "--verify", ref + "^{commit}"], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        # try common fallbacks: origin/<ref> if branch name passed
        proc2 = _run_git(["rev-parse", "--verify", f"origin/{ref}" + "^{commit}"], cwd=repo_dir, check=False)
        if proc2.returncode != 0:
            raise GitError(f"Cannot resolve ref '{ref}'. Make sure it exists (tag/branch/SHA).")
        return proc2.stdout.strip()
    return proc.stdout.strip()


def _github_commit_url(repo_url: str, sha: str) -> Optional[str]:
    """
    Build a GitHub commit URL if repo_url looks like GitHub.
    """
    repo_url = repo_url.strip().rstrip("/")
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)$", repo_url)
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://github.com/{owner}/{repo}/commit/{sha}"
    return None


def list_commits_between(
    repo_dir: Path,
    repo_url: str,
    from_ref: str,
    to_ref: str,
    include_files: bool = True,
    max_commits: Optional[int] = None,
) -> List[CommitChange]:
    """
    Harvest commit changes between two refs: (from_ref, to_ref] using git log from..to.
    - include_files: if True, includes file paths changed per commit (slower but useful)
    - max_commits: optional cap for safety during development
    Parsing strategy:
    - Use hard separators in --pretty format to avoid ambiguity and make parsing robust.
    """
    from_sha = resolve_ref(repo_dir, from_ref)
    to_sha = resolve_ref(repo_dir, to_ref)

    # Custom pretty format with hard separators for robust parsing
    # Fields: sha, author_name, author_email, author_date, subject, body
    sep_record = "\n---RN_RECORD---\n"
    sep_field = "\n---RN_FIELD---\n"

    # Robust parsing: we use explicit separators to safely split records and fields
    # even when commit bodies contain newlines.
    pretty = f"%H{sep_field}%an{sep_field}%ae{sep_field}%ad{sep_field}%s{sep_field}%b{sep_record}"

    args = ["log", f"{from_sha}..{to_sha}", f"--pretty=format:{pretty}", "--date=iso-strict"]
    if max_commits is not None:
        args.insert(1, f"-n")
        args.insert(2, str(max_commits))

    proc = _run_git(args, cwd=repo_dir)
    raw = proc.stdout.strip()
    if not raw:
        return []

    records = [r for r in raw.split(sep_record) if r.strip()]
    changes: List[CommitChange] = []

    for rec in records:
        parts = rec.split(sep_field)
        if len(parts) < 6:
            # defensive: skip malformed record
            continue
        sha = parts[0].strip()
        author_name = parts[1].strip()
        author_email = parts[2].strip()
        author_date = parts[3].strip()
        subject = parts[4].strip()
        body = parts[5].strip()

        files: List[str] = []
        if include_files:
            # File lists are a useful heuristic signal (docs/tests/CI), but optional because it adds git calls per commit.
            p = _run_git(["show", "--name-only", "--pretty=format:", sha], cwd=repo_dir)
            files = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]

        changes.append(
            CommitChange(
                sha=sha,
                author_name=author_name,
                author_email=author_email,
                author_date=author_date,
                subject=subject,
                body=body,
                files=files,
                url=_github_commit_url(repo_url, sha),
            )
        )

    return changes

# Public API for the harvesting stage: returns JSON-friendly dicts for the next pipeline stages.
def harvest_changes(
    repo_url: str,
    from_ref: str,
    to_ref: str,
    cache_dir: Path,
    include_files: bool = True,
    max_commits: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    High-level function: ensure local repo, then harvest commits between refs.
    Returns list of dicts (JSON-friendly).
    """
    repo_dir = ensure_repo(repo_url, cache_dir)
    commits = list_commits_between(
        repo_dir=repo_dir,
        repo_url=repo_url,
        from_ref=from_ref,
        to_ref=to_ref,
        include_files=include_files,
        max_commits=max_commits,
    )
    return [asdict(c) for c in commits]