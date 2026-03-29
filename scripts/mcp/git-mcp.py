from __future__ import annotations

import argparse
import locale
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
FIELD_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x1e"
STATUS_CODE_MAP = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "unmerged",
    "T": "type_changed",
}


@dataclass(frozen=True)
class RepoRecord:
    name: str
    root: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only MCP server for local Git repositories."
    )
    parser.add_argument(
        "--repo-root",
        action="append",
        default=[],
        help="Allowed Git repository root. Repeat to allow multiple repositories.",
    )
    return parser.parse_args()


class GitService:
    def __init__(self, repo_roots: list[str]) -> None:
        if not shutil.which("git"):
            raise RuntimeError("The 'git' command was not found. Install Git first.")

        roots = repo_roots or self._detect_repo_roots()
        if not roots:
            raise RuntimeError(
                "No Git repositories were detected. Pass --repo-root or register a repository explicitly."
            )

        records_by_root: dict[Path, RepoRecord] = {}
        for root in roots:
            record = self._fetch_repo_record(Path(root))
            records_by_root[record.root] = record

        self._records = dict(sorted(records_by_root.items(), key=lambda item: str(item[0]).lower()))

    @property
    def records(self) -> list[RepoRecord]:
        return list(self._records.values())

    def _run_git(self, repo: RepoRecord | None, args: list[str]) -> str:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        command = ["git"]
        if repo is not None:
            command.extend(["-C", str(repo.root)])
        command.extend(args)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Git command failed: {' '.join(command)}\n{details}")
        return result.stdout

    def _detect_repo_roots(self) -> list[str]:
        output = self._run_git(None, ["rev-parse", "--show-toplevel"]).strip()
        if not output:
            return []
        return [output]

    def _fetch_repo_record(self, root: Path) -> RepoRecord:
        resolved = root.resolve(strict=False)
        output = self._run_git(None, ["-C", str(resolved), "rev-parse", "--show-toplevel"]).strip()
        if not output:
            raise RuntimeError(f"Could not determine Git root for '{root}'.")
        repo_root = Path(output).resolve(strict=False)
        return RepoRecord(name=repo_root.name, root=repo_root)

    def _default_repo(self) -> RepoRecord:
        if len(self.records) == 1:
            return self.records[0]
        roots = ", ".join(str(record.root) for record in self.records)
        raise ValueError(
            "Multiple Git repositories are configured. Pass repo_path explicitly or use an absolute path inside one of: "
            f"{roots}"
        )

    def _find_repo_for_path(self, candidate: Path) -> RepoRecord | None:
        resolved = candidate.resolve(strict=False)
        matches = [
            record for record in self.records if resolved == record.root or resolved.is_relative_to(record.root)
        ]
        if not matches:
            return None
        return max(matches, key=lambda record: len(str(record.root)))

    def resolve_repo(self, repo_path: str | None = None) -> RepoRecord:
        if not repo_path:
            return self._default_repo()

        raw_candidate = Path(repo_path).expanduser()
        if not raw_candidate.is_absolute():
            name_match = next(
                (record for record in self.records if record.name.lower() == repo_path.strip().lower()),
                None,
            )
            if name_match:
                return name_match
            raw_candidate = self._default_repo().root / raw_candidate

        repo = self._find_repo_for_path(raw_candidate)
        if repo is None:
            allowed = ", ".join(str(record.root) for record in self.records)
            raise ValueError(f"Path '{raw_candidate}' is outside the allowed Git repositories. Allowed roots: {allowed}")
        return repo

    def resolve_repo_path(self, path: str | None = None, repo_path: str | None = None) -> tuple[RepoRecord, Path, str | None]:
        repo = self.resolve_repo(repo_path)
        if not path:
            return repo, repo.root, None

        raw_candidate = Path(path).expanduser()
        if not raw_candidate.is_absolute():
            raw_candidate = repo.root / raw_candidate

        resolved = raw_candidate.resolve(strict=False)
        if not (resolved == repo.root or resolved.is_relative_to(repo.root)):
            raise ValueError(f"Path '{resolved}' is outside the Git repository root '{repo.root}'.")

        relative = PurePosixPath(resolved.relative_to(repo.root)).as_posix()
        return repo, resolved, relative

    def _counter_dict(self, values: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items()))

    def _decode_status_code(self, code: str) -> str | None:
        if code in {".", " "}:
            return None
        return STATUS_CODE_MAP.get(code, code)

    def _parse_branch_header(self, lines: list[str]) -> dict[str, Any]:
        branch: dict[str, Any] = {
            "head": None,
            "oid": None,
            "upstream": None,
            "ahead": 0,
            "behind": 0,
            "is_detached": False,
        }
        for line in lines:
            if line.startswith("# branch.oid "):
                branch["oid"] = line.removeprefix("# branch.oid ").strip()
            elif line.startswith("# branch.head "):
                head = line.removeprefix("# branch.head ").strip()
                branch["head"] = head
                branch["is_detached"] = head == "(detached)"
            elif line.startswith("# branch.upstream "):
                branch["upstream"] = line.removeprefix("# branch.upstream ").strip()
            elif line.startswith("# branch.ab "):
                payload = line.removeprefix("# branch.ab ").strip().split()
                for item in payload:
                    if item.startswith("+"):
                        branch["ahead"] = int(item[1:])
                    elif item.startswith("-"):
                        branch["behind"] = int(item[1:])
        return branch

    def _parse_status_entries(self, repo: RepoRecord, output: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        branch_lines: list[str] = []
        entries: list[dict[str, Any]] = []

        for raw_line in output.splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            if line.startswith("# "):
                branch_lines.append(line)
                continue

            if line.startswith("? "):
                relative_path = line[2:]
                entries.append(
                    {
                        "entry_type": "untracked",
                        "path": str((repo.root / PurePosixPath(relative_path)).resolve(strict=False)),
                        "relative_path": relative_path,
                        "primary_status": "untracked",
                        "staged_status": None,
                        "worktree_status": "untracked",
                        "xy": "??",
                    }
                )
                continue

            if line.startswith("! "):
                relative_path = line[2:]
                entries.append(
                    {
                        "entry_type": "ignored",
                        "path": str((repo.root / PurePosixPath(relative_path)).resolve(strict=False)),
                        "relative_path": relative_path,
                        "primary_status": "ignored",
                        "staged_status": None,
                        "worktree_status": "ignored",
                        "xy": "!!",
                    }
                )
                continue

            if line.startswith("1 "):
                parts = line.split(" ", 8)
                if len(parts) != 9:
                    raise RuntimeError(f"Unexpected git status entry: {line}")
                _, xy, _sub, _mH, _mI, _mW, _hH, _hI, relative_path = parts
                staged_status = self._decode_status_code(xy[0])
                worktree_status = self._decode_status_code(xy[1])
                primary_status = staged_status or worktree_status or "clean"
                entries.append(
                    {
                        "entry_type": "ordinary",
                        "path": str((repo.root / PurePosixPath(relative_path)).resolve(strict=False)),
                        "relative_path": relative_path,
                        "primary_status": primary_status,
                        "staged_status": staged_status,
                        "worktree_status": worktree_status,
                        "xy": xy,
                    }
                )
                continue

            if line.startswith("2 "):
                parts = line.split(" ", 9)
                if len(parts) != 10:
                    raise RuntimeError(f"Unexpected git rename status entry: {line}")
                _, xy, _sub, _mH, _mI, _mW, _hH, _hI, _score, path_payload = parts
                relative_path, original_path = (path_payload.split("\t", 1) + [""])[:2]
                staged_status = self._decode_status_code(xy[0])
                worktree_status = self._decode_status_code(xy[1])
                primary_status = staged_status or worktree_status or "renamed"
                entries.append(
                    {
                        "entry_type": "renamed",
                        "path": str((repo.root / PurePosixPath(relative_path)).resolve(strict=False)),
                        "relative_path": relative_path,
                        "original_path": original_path or None,
                        "primary_status": primary_status,
                        "staged_status": staged_status,
                        "worktree_status": worktree_status,
                        "xy": xy,
                    }
                )
                continue

            if line.startswith("u "):
                parts = line.split(" ", 10)
                if len(parts) != 11:
                    raise RuntimeError(f"Unexpected git unmerged status entry: {line}")
                _, xy, _sub, _m1, _m2, _m3, _mW, _h1, _h2, _h3, relative_path = parts
                entries.append(
                    {
                        "entry_type": "unmerged",
                        "path": str((repo.root / PurePosixPath(relative_path)).resolve(strict=False)),
                        "relative_path": relative_path,
                        "primary_status": "unmerged",
                        "staged_status": "unmerged",
                        "worktree_status": "unmerged",
                        "xy": xy,
                    }
                )
                continue

        return self._parse_branch_header(branch_lines), entries

    def _truncate_text(self, text: str, max_chars: int) -> tuple[str, bool]:
        normalized = text.strip()
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized, False
        return normalized[:max_chars], True

    def _parse_log_records(self, output: str) -> list[dict[str, Any]]:
        commits: list[dict[str, Any]] = []
        for record in output.split(RECORD_SEPARATOR):
            stripped = record.strip()
            if not stripped:
                continue
            parts = stripped.split(FIELD_SEPARATOR)
            if len(parts) < 6:
                continue
            full_hash, short_hash, author_name, author_email, author_date, subject = parts[:6]
            commits.append(
                {
                    "commit": full_hash,
                    "short_commit": short_hash,
                    "author_name": author_name,
                    "author_email": author_email,
                    "author_date": author_date,
                    "subject": subject,
                }
            )
        return commits

    def list_repositories(self) -> dict[str, Any]:
        return {
            "repositories": [record.to_dict() for record in self.records],
        }

    def repository_status(
        self,
        repo_path: str | None = None,
        limit: int = 200,
        include_untracked: bool = True,
        include_ignored: bool = False,
    ) -> dict[str, Any]:
        repo = self.resolve_repo(repo_path)
        args = ["status", "--porcelain=v2", "--branch"]
        args.append("--untracked-files=all" if include_untracked else "--untracked-files=no")
        if include_ignored:
            args.append("--ignored=matching")

        output = self._run_git(repo, args)
        branch, entries = self._parse_status_entries(repo, output)
        normalized_limit = max(1, min(limit, 500))

        return {
            "repo_root": str(repo.root),
            "repo_name": repo.name,
            "branch": branch,
            "total_count": len(entries),
            "truncated": len(entries) > normalized_limit,
            "by_primary_status": self._counter_dict([entry["primary_status"] for entry in entries]),
            "by_staged_status": self._counter_dict([entry["staged_status"] for entry in entries if entry.get("staged_status")]),
            "by_worktree_status": self._counter_dict([entry["worktree_status"] for entry in entries if entry.get("worktree_status")]),
            "entries": entries[:normalized_limit],
        }

    def branches(self, repo_path: str | None = None, include_remote: bool = False) -> dict[str, Any]:
        repo = self.resolve_repo(repo_path)
        refs = ["refs/heads"]
        if include_remote:
            refs.append("refs/remotes")
        format_string = FIELD_SEPARATOR.join(
            ["%(refname)", "%(refname:short)", "%(upstream:short)", "%(objectname:short)", "%(HEAD)", "%(committerdate:iso-strict)", "%(subject)"]
        ) + RECORD_SEPARATOR
        output = self._run_git(repo, ["for-each-ref", f"--format={format_string}", *refs])
        branches: list[dict[str, Any]] = []
        for record in output.split(RECORD_SEPARATOR):
            stripped = record.strip()
            if not stripped:
                continue
            parts = stripped.split(FIELD_SEPARATOR)
            if len(parts) < 7:
                continue
            refname, short_name, upstream, short_commit, head_marker, committer_date, subject = parts[:7]
            branches.append(
                {
                    "name": short_name,
                    "refname": refname,
                    "upstream": upstream or None,
                    "short_commit": short_commit or None,
                    "is_current": head_marker == "*",
                    "is_remote": refname.startswith("refs/remotes/"),
                    "committer_date": committer_date or None,
                    "subject": subject or None,
                }
            )
        return {
            "repo_root": str(repo.root),
            "repo_name": repo.name,
            "branches": branches,
        }

    def remotes(self, repo_path: str | None = None) -> dict[str, Any]:
        repo = self.resolve_repo(repo_path)
        output = self._run_git(repo, ["remote", "-v"])
        grouped: dict[str, dict[str, Any]] = {}
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            name, url, direction = line.split(maxsplit=2)
            direction = direction.strip("()")
            remote = grouped.setdefault(name, {"name": name, "fetch_url": None, "push_url": None})
            if direction == "fetch":
                remote["fetch_url"] = url
            elif direction == "push":
                remote["push_url"] = url
        return {
            "repo_root": str(repo.root),
            "repo_name": repo.name,
            "remotes": list(grouped.values()),
        }

    def log(
        self,
        repo_path: str | None = None,
        limit: int = 20,
        ref: str = "HEAD",
        path: str | None = None,
    ) -> dict[str, Any]:
        repo, _, relative_path = self.resolve_repo_path(path, repo_path)
        normalized_limit = max(1, min(limit, 100))
        format_string = FIELD_SEPARATOR.join(["%H", "%h", "%an", "%ae", "%ad", "%s"]) + RECORD_SEPARATOR
        args = [
            "log",
            ref,
            f"-n{normalized_limit}",
            "--date=iso-strict",
            f"--format={format_string}",
        ]
        if relative_path:
            args.extend(["--follow", "--", relative_path])
        output = self._run_git(repo, args)
        return {
            "repo_root": str(repo.root),
            "repo_name": repo.name,
            "ref": ref,
            "path": relative_path,
            "commits": self._parse_log_records(output),
        }

    def file_history(
        self,
        path: str,
        repo_path: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        repo, absolute_path, relative_path = self.resolve_repo_path(path, repo_path)
        if not relative_path:
            raise ValueError("file_history requires a path inside the repository.")
        result = self.log(repo_path=str(repo.root), limit=limit, ref="HEAD", path=str(absolute_path))
        result["absolute_path"] = str(absolute_path)
        return result

    def show_commit(
        self,
        commit: str = "HEAD",
        repo_path: str | None = None,
        include_patch: bool = False,
        context_lines: int = 3,
        max_patch_chars: int = 40000,
    ) -> dict[str, Any]:
        repo = self.resolve_repo(repo_path)
        meta_format = FIELD_SEPARATOR.join(["%H", "%h", "%an", "%ae", "%ad", "%P", "%s"]) + RECORD_SEPARATOR
        output = self._run_git(
            repo,
            [
                "show",
                "--date=iso-strict",
                "--name-status",
                "--format=" + meta_format,
                "--find-renames",
                commit,
            ],
        )
        meta_text, _, name_status_text = output.partition(RECORD_SEPARATOR)
        meta_parts = meta_text.split(FIELD_SEPARATOR)
        if len(meta_parts) < 7:
            raise RuntimeError(f"Unexpected git show metadata for commit '{commit}'.")
        full_hash, short_hash, author_name, author_email, author_date, parents, subject = meta_parts[:7]
        files: list[dict[str, Any]] = []
        for raw_line in name_status_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status_code = parts[0]
            if status_code.startswith("R") or status_code.startswith("C"):
                old_path = parts[1] if len(parts) > 1 else None
                new_path = parts[2] if len(parts) > 2 else None
                files.append(
                    {
                        "status": "renamed" if status_code.startswith("R") else "copied",
                        "status_code": status_code,
                        "path": new_path,
                        "original_path": old_path,
                    }
                )
            else:
                path_item = parts[1] if len(parts) > 1 else None
                files.append(
                    {
                        "status": STATUS_CODE_MAP.get(status_code, status_code),
                        "status_code": status_code,
                        "path": path_item,
                        "original_path": None,
                    }
                )

        patch_text = None
        patch_truncated = False
        if include_patch:
            patch_output = self._run_git(
                repo,
                [
                    "show",
                    "--date=iso-strict",
                    f"--unified={max(0, min(context_lines, 20))}",
                    "--no-ext-diff",
                    "--format=fuller",
                    commit,
                ],
            )
            patch_text, patch_truncated = self._truncate_text(patch_output, max_patch_chars)

        return {
            "repo_root": str(repo.root),
            "repo_name": repo.name,
            "commit": full_hash,
            "short_commit": short_hash,
            "author_name": author_name,
            "author_email": author_email,
            "author_date": author_date,
            "parents": [item for item in parents.split() if item],
            "subject": subject,
            "files": files,
            "patch": patch_text,
            "patch_truncated": patch_truncated,
        }

    def diff(
        self,
        repo_path: str | None = None,
        path: str | None = None,
        staged: bool = False,
        ref: str | None = None,
        context_lines: int = 3,
        max_diff_chars: int = 40000,
    ) -> dict[str, Any]:
        repo, absolute_path, relative_path = self.resolve_repo_path(path, repo_path)
        base_args = ["diff", "--no-ext-diff", f"--unified={max(0, min(context_lines, 20))}"]
        stat_args = ["diff", "--no-ext-diff", "--stat"]
        if staged:
            base_args.append("--cached")
            stat_args.append("--cached")
        if ref:
            base_args.append(ref)
            stat_args.append(ref)
        if relative_path:
            base_args.extend(["--", relative_path])
            stat_args.extend(["--", relative_path])

        stat_text = self._run_git(repo, stat_args)
        diff_text = self._run_git(repo, base_args)
        truncated_diff, is_truncated = self._truncate_text(diff_text, max_diff_chars)

        return {
            "repo_root": str(repo.root),
            "repo_name": repo.name,
            "path": relative_path,
            "absolute_path": str(absolute_path),
            "staged": staged,
            "ref": ref,
            "stat": stat_text.strip() or None,
            "diff": truncated_diff or None,
            "truncated": is_truncated,
            "is_empty": not diff_text.strip(),
        }


def build_server(service: GitService) -> FastMCP:
    server = FastMCP("git")

    @server.tool(
        name="list_repositories",
        description="List the allowed local Git repositories.",
        annotations=READ_ONLY,
    )
    def list_repositories() -> dict[str, Any]:
        return service.list_repositories()

    @server.tool(
        name="repository_status",
        description="Inspect Git status for a local repository, including branch tracking and changed files.",
        annotations=READ_ONLY,
    )
    def repository_status(
        repo_path: str | None = None,
        limit: int = 200,
        include_untracked: bool = True,
        include_ignored: bool = False,
    ) -> dict[str, Any]:
        return service.repository_status(
            repo_path=repo_path,
            limit=limit,
            include_untracked=include_untracked,
            include_ignored=include_ignored,
        )

    @server.tool(
        name="branches",
        description="List local Git branches and optionally remote branches for an allowed repository.",
        annotations=READ_ONLY,
    )
    def branches(repo_path: str | None = None, include_remote: bool = False) -> dict[str, Any]:
        return service.branches(repo_path=repo_path, include_remote=include_remote)

    @server.tool(
        name="remotes",
        description="List Git remotes for an allowed repository.",
        annotations=READ_ONLY,
    )
    def remotes(repo_path: str | None = None) -> dict[str, Any]:
        return service.remotes(repo_path=repo_path)

    @server.tool(
        name="log",
        description="Show recent Git commit history for a repository or a specific file path.",
        annotations=READ_ONLY,
    )
    def log(
        repo_path: str | None = None,
        limit: int = 20,
        ref: str = "HEAD",
        path: str | None = None,
    ) -> dict[str, Any]:
        return service.log(repo_path=repo_path, limit=limit, ref=ref, path=path)

    @server.tool(
        name="file_history",
        description="Show Git commit history for a specific file inside an allowed repository.",
        annotations=READ_ONLY,
    )
    def file_history(path: str, repo_path: str | None = None, limit: int = 20) -> dict[str, Any]:
        return service.file_history(path=path, repo_path=repo_path, limit=limit)

    @server.tool(
        name="show_commit",
        description="Show metadata and changed files for a Git commit, with an optional truncated patch.",
        annotations=READ_ONLY,
    )
    def show_commit(
        commit: str = "HEAD",
        repo_path: str | None = None,
        include_patch: bool = False,
        context_lines: int = 3,
        max_patch_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.show_commit(
            commit=commit,
            repo_path=repo_path,
            include_patch=include_patch,
            context_lines=context_lines,
            max_patch_chars=max_patch_chars,
        )

    @server.tool(
        name="diff",
        description="Show a Git diff for unstaged changes, staged changes, or a ref, optionally limited to one file.",
        annotations=READ_ONLY,
    )
    def diff(
        repo_path: str | None = None,
        path: str | None = None,
        staged: bool = False,
        ref: str | None = None,
        context_lines: int = 3,
        max_diff_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.diff(
            repo_path=repo_path,
            path=path,
            staged=staged,
            ref=ref,
            context_lines=context_lines,
            max_diff_chars=max_diff_chars,
        )

    return server


def main() -> int:
    args = parse_args()
    service = GitService(args.repo_root)
    server = build_server(service)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
