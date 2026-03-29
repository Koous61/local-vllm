from __future__ import annotations

import argparse
import locale
import os
import shutil
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
IGNORE_DIRS = {
    ".git",
    ".idea",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "env",
    "node_modules",
    "out",
    "site-packages",
    "venv",
}
PROJECT_MARKERS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "setup.py",
    "setup.cfg",
    "tox.ini",
)
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py", "*tests.py")
ENTRYPOINT_CANDIDATES = ("app.py", "main.py", "manage.py", "server.py", "run.py", "wsgi.py")


@dataclass(frozen=True)
class PythonProjectRecord:
    name: str
    root: Path
    interpreter: Path
    interpreter_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
            "interpreter": str(self.interpreter),
            "interpreter_source": self.interpreter_source,
        }


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    output: str
    truncated: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MCP server for local Python projects, script execution, and tests."
    )
    parser.add_argument(
        "--project-root",
        action="append",
        default=[],
        help="Allowed Python project root. Repeat to allow multiple projects.",
    )
    return parser.parse_args()


class PythonService:
    def __init__(self, project_roots: list[str]) -> None:
        roots = project_roots or self._detect_project_roots()
        if not roots:
            raise RuntimeError(
                "No Python projects were detected. Pass --project-root or register a project explicitly."
            )

        records_by_root: dict[Path, PythonProjectRecord] = {}
        for root in roots:
            record = self._fetch_project_record(Path(root))
            records_by_root[record.root] = record

        self._records = dict(sorted(records_by_root.items(), key=lambda item: str(item[0]).lower()))

    @property
    def records(self) -> list[PythonProjectRecord]:
        return list(self._records.values())

    def _should_skip_dir(self, path: Path) -> bool:
        return path.name in IGNORE_DIRS or path.name.startswith(".venv")

    def _iter_python_files(self, root: Path, *, limit: int = 500) -> list[Path]:
        files: list[Path] = []
        queue: list[Path] = [root]
        seen: set[Path] = set()

        while queue and len(files) < limit:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)

            try:
                children = sorted(current.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                continue

            for child in children:
                if child.is_dir():
                    if self._should_skip_dir(child):
                        continue
                    queue.append(child)
                    continue
                if child.suffix.lower() == ".py":
                    files.append(child.resolve(strict=False))
                    if len(files) >= limit:
                        break

        return files

    def _looks_like_python_project(self, root: Path) -> bool:
        for marker in PROJECT_MARKERS:
            if (root / marker).exists():
                return True
        return bool(self._iter_python_files(root, limit=1))

    def _detect_project_roots(self) -> list[str]:
        cwd = Path.cwd().resolve(strict=False)
        matches: list[Path] = []
        if self._looks_like_python_project(cwd):
            matches.append(cwd)

        queue: list[tuple[Path, int]] = [(cwd, 0)]
        seen: set[Path] = set()
        while queue:
            current, depth = queue.pop(0)
            if current in seen or depth >= 2:
                continue
            seen.add(current)

            try:
                children = sorted(current.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                continue

            for child in children:
                if not child.is_dir() or self._should_skip_dir(child):
                    continue
                if self._looks_like_python_project(child):
                    matches.append(child.resolve(strict=False))
                    continue
                queue.append((child, depth + 1))

        unique_matches = sorted({item.resolve(strict=False) for item in matches}, key=lambda item: str(item).lower())
        return [str(item) for item in unique_matches]

    def _choose_interpreter(self, root: Path) -> tuple[Path, str]:
        candidates = (
            (root / ".venv" / "Scripts" / "python.exe", "project-.venv"),
            (root / "venv" / "Scripts" / "python.exe", "project-venv"),
            (root / "env" / "Scripts" / "python.exe", "project-env"),
        )
        for candidate, source in candidates:
            if candidate.exists():
                return candidate.resolve(strict=False), source

        system_python = shutil.which("python")
        if system_python:
            return Path(system_python).resolve(strict=False), "system-python"

        return Path(sys.executable).resolve(strict=False), "server-interpreter"

    def _fetch_project_record(self, root: Path) -> PythonProjectRecord:
        resolved = root.resolve(strict=False)
        if not self._looks_like_python_project(resolved):
            raise RuntimeError(f"'{resolved}' does not look like a Python project root.")

        interpreter, interpreter_source = self._choose_interpreter(resolved)
        return PythonProjectRecord(
            name=resolved.name,
            root=resolved,
            interpreter=interpreter,
            interpreter_source=interpreter_source,
        )

    def _default_project(self) -> PythonProjectRecord:
        if len(self.records) == 1:
            return self.records[0]
        roots = ", ".join(str(record.root) for record in self.records)
        raise ValueError(
            "Multiple Python projects are configured. Pass project_path explicitly or use an absolute path inside one of: "
            f"{roots}"
        )

    def _find_project_for_path(self, candidate: Path) -> PythonProjectRecord | None:
        resolved = candidate.resolve(strict=False)
        matches = [
            record for record in self.records if resolved == record.root or resolved.is_relative_to(record.root)
        ]
        if not matches:
            return None
        return max(matches, key=lambda record: len(str(record.root)))

    def resolve_project(self, project_path: str | None = None) -> PythonProjectRecord:
        if not project_path:
            return self._default_project()

        raw_candidate = Path(project_path).expanduser()
        if not raw_candidate.is_absolute():
            name_match = next(
                (record for record in self.records if record.name.lower() == project_path.strip().lower()),
                None,
            )
            if name_match:
                return name_match
            raw_candidate = self._default_project().root / raw_candidate

        project = self._find_project_for_path(raw_candidate)
        if project is None:
            allowed = ", ".join(str(record.root) for record in self.records)
            raise ValueError(f"Path '{raw_candidate}' is outside the allowed Python projects. Allowed roots: {allowed}")
        return project

    def resolve_project_path(
        self,
        path: str | None = None,
        project_path: str | None = None,
        *,
        require_exists: bool = True,
    ) -> tuple[PythonProjectRecord, Path]:
        project = self.resolve_project(project_path)
        if not path:
            return project, project.root

        raw_candidate = Path(path).expanduser()
        if not raw_candidate.is_absolute():
            raw_candidate = project.root / raw_candidate

        resolved = raw_candidate.resolve(strict=False)
        if not (resolved == project.root or resolved.is_relative_to(project.root)):
            raise ValueError(f"Path '{resolved}' is outside the Python project root '{project.root}'.")
        if require_exists and not resolved.exists():
            raise FileNotFoundError(f"Path '{resolved}' was not found.")
        return project, resolved

    def _requirements_files(self, project: PythonProjectRecord) -> list[str]:
        files = []
        for candidate in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
            path = project.root / candidate
            if path.exists():
                files.append(str(path))
        return files

    def _config_files(self, project: PythonProjectRecord) -> list[str]:
        files = []
        for candidate in ("pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini"):
            path = project.root / candidate
            if path.exists():
                files.append(str(path))
        return files

    def _find_test_files(self, project: PythonProjectRecord, *, limit: int = 100) -> list[Path]:
        matches: list[Path] = []
        for pattern in TEST_FILE_PATTERNS:
            for path in project.root.rglob(pattern):
                if any(part in IGNORE_DIRS for part in path.parts):
                    continue
                if path.is_file():
                    matches.append(path.resolve(strict=False))
                if len(matches) >= limit:
                    return sorted({item for item in matches}, key=lambda item: str(item).lower())

        tests_dir = project.root / "tests"
        if tests_dir.exists() and tests_dir.is_dir():
            for path in tests_dir.rglob("*.py"):
                if any(part in IGNORE_DIRS for part in path.parts):
                    continue
                matches.append(path.resolve(strict=False))
                if len(matches) >= limit:
                    break

        return sorted({item for item in matches}, key=lambda item: str(item).lower())[:limit]

    def _entrypoint_candidates(self, project: PythonProjectRecord) -> list[str]:
        candidates: list[str] = []
        for filename in ENTRYPOINT_CANDIDATES:
            path = project.root / filename
            if path.exists():
                candidates.append(str(path))
        return candidates

    def _truncate_text(self, text: str, max_chars: int) -> tuple[str, bool]:
        normalized = text.strip()
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized, False
        return normalized[:max_chars], True

    def _run_python(
        self,
        *,
        project: PythonProjectRecord,
        arguments: list[str],
        timeout_seconds: int,
        max_chars: int,
    ) -> CommandResult:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        command = [str(project.interpreter), *arguments]
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

        try:
            result = subprocess.run(
                command,
                cwd=str(project.root),
                capture_output=True,
                text=True,
                encoding=encoding,
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=max(1, timeout_seconds),
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
            truncated, _ = self._truncate_text(combined, max_chars)
            raise RuntimeError(
                f"Python command timed out after {timeout_seconds} seconds: {' '.join(command)}\n{truncated}"
            ) from exc

        combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        truncated_output, truncated = self._truncate_text(combined, max_chars)
        if result.returncode != 0:
            details = truncated_output or f"exit code {result.returncode}"
            raise RuntimeError(f"Python command failed: {' '.join(command)}\n{details}")

        return CommandResult(
            command=command,
            output=truncated_output,
            truncated=truncated,
        )

    def _normalize_relative_path(self, project: PythonProjectRecord, path: Path) -> str:
        return PurePosixPath(path.resolve(strict=False).relative_to(project.root)).as_posix()

    def _normalize_target_arguments(
        self,
        project: PythonProjectRecord,
        targets: list[str] | None,
        *,
        for_unittest: bool = False,
    ) -> list[str]:
        if not targets:
            return []

        normalized: list[str] = []
        for item in targets:
            raw_candidate = Path(item).expanduser()
            if raw_candidate.is_absolute() or (project.root / raw_candidate).exists():
                _, resolved = self.resolve_project_path(item, str(project.root))
                if for_unittest and resolved.suffix.lower() == ".py":
                    relative = self._normalize_relative_path(project, resolved)
                    normalized.append(relative[:-3].replace("/", "."))
                else:
                    normalized.append(str(resolved))
            else:
                normalized.append(item)
        return normalized

    def list_projects(self) -> dict[str, Any]:
        projects: list[dict[str, Any]] = []
        for record in self.records:
            python_files = self._iter_python_files(record.root, limit=500)
            tests = self._find_test_files(record, limit=50)
            projects.append(
                {
                    **record.to_dict(),
                    "python_file_count": len(python_files),
                    "test_file_count": len(tests),
                    "requirements_files": self._requirements_files(record),
                    "config_files": self._config_files(record),
                    "entrypoint_candidates": self._entrypoint_candidates(record),
                }
            )
        return {"projects": projects}

    def project_summary(self, project_path: str | None = None) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        python_files = self._iter_python_files(project.root, limit=500)
        tests = self._find_test_files(project, limit=50)
        return {
            "project": project.to_dict(),
            "python_file_count": len(python_files),
            "python_file_sample": [str(path) for path in python_files[:12]],
            "test_file_count": len(tests),
            "has_tests": bool(tests),
            "test_file_sample": [str(path) for path in tests[:12]],
            "requirements_files": self._requirements_files(project),
            "config_files": self._config_files(project),
            "entrypoint_candidates": self._entrypoint_candidates(project),
        }

    def list_test_targets(self, project_path: str | None = None, limit: int = 100) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        normalized_limit = max(1, min(limit, 200))
        tests = self._find_test_files(project, limit=normalized_limit)
        return {
            "project": project.to_dict(),
            "test_file_count": len(tests),
            "tests": [str(path) for path in tests[:normalized_limit]],
            "truncated": len(tests) > normalized_limit,
        }

    def syntax_check(
        self,
        project_path: str | None = None,
        paths: list[str] | None = None,
        file_limit: int = 200,
        max_errors: int = 20,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        candidates: list[Path] = []

        if paths:
            for item in paths:
                _, resolved = self.resolve_project_path(item, str(project.root))
                if resolved.is_dir():
                    candidates.extend(self._iter_python_files(resolved, limit=file_limit))
                elif resolved.suffix.lower() == ".py":
                    candidates.append(resolved)
        else:
            candidates = self._iter_python_files(project.root, limit=file_limit)

        unique_candidates = []
        seen_paths: set[Path] = set()
        for path in candidates:
            resolved = path.resolve(strict=False)
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            unique_candidates.append(resolved)
            if len(unique_candidates) >= file_limit:
                break

        errors: list[dict[str, Any]] = []
        for path in unique_candidates:
            try:
                with tokenize.open(path) as handle:
                    source = handle.read()
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                errors.append(
                    {
                        "path": str(path),
                        "line": exc.lineno,
                        "offset": exc.offset,
                        "message": exc.msg,
                        "text": exc.text.strip() if exc.text else None,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "path": str(path),
                        "line": None,
                        "offset": None,
                        "message": str(exc),
                        "text": None,
                    }
                )
            if len(errors) >= max_errors:
                break

        return {
            "project": project.to_dict(),
            "checked_file_count": len(unique_candidates),
            "is_clean": not errors,
            "errors": errors,
            "truncated": len(errors) >= max_errors,
        }

    def run_script(
        self,
        script_path: str,
        project_path: str | None = None,
        script_args: list[str] | None = None,
        python_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project, script = self.resolve_project_path(script_path, project_path)
        if script.suffix.lower() != ".py":
            raise RuntimeError(f"'{script}' is not a Python script.")

        arguments = [*(python_args or []), str(script), *(script_args or [])]
        result = self._run_python(
            project=project,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "script_path": str(script),
            "command": " ".join(result.command),
            "timeout_seconds": timeout_seconds,
            "output": result.output or None,
            "truncated": result.truncated,
        }

    def run_module(
        self,
        module: str,
        project_path: str | None = None,
        module_args: list[str] | None = None,
        python_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        arguments = [*(python_args or []), "-m", module, *(module_args or [])]
        result = self._run_python(
            project=project,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "module": module,
            "command": " ".join(result.command),
            "timeout_seconds": timeout_seconds,
            "output": result.output or None,
            "truncated": result.truncated,
        }

    def run_pytest(
        self,
        project_path: str | None = None,
        targets: list[str] | None = None,
        keyword: str | None = None,
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        normalized_targets = self._normalize_target_arguments(project, targets, for_unittest=False)
        arguments = ["-m", "pytest", *normalized_targets]
        if keyword:
            arguments.extend(["-k", keyword])
        arguments.extend(extra_args or [])
        result = self._run_python(
            project=project,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "targets": normalized_targets,
            "keyword": keyword,
            "command": " ".join(result.command),
            "timeout_seconds": timeout_seconds,
            "output": result.output or None,
            "truncated": result.truncated,
        }

    def run_unittest(
        self,
        project_path: str | None = None,
        targets: list[str] | None = None,
        discover: bool = True,
        start_directory: str = ".",
        pattern: str = "test*.py",
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        arguments = ["-m", "unittest"]
        normalized_targets = self._normalize_target_arguments(project, targets, for_unittest=True)
        if normalized_targets:
            arguments.extend(normalized_targets)
        elif discover:
            arguments.extend(["discover", "-s", start_directory, "-p", pattern])
        arguments.extend(extra_args or [])

        result = self._run_python(
            project=project,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "targets": normalized_targets,
            "discover": discover and not normalized_targets,
            "start_directory": start_directory if discover and not normalized_targets else None,
            "pattern": pattern if discover and not normalized_targets else None,
            "command": " ".join(result.command),
            "timeout_seconds": timeout_seconds,
            "output": result.output or None,
            "truncated": result.truncated,
        }


def build_server(service: PythonService) -> FastMCP:
    server = FastMCP("python")

    @server.tool(
        name="list_projects",
        description="List the configured local Python projects that this MCP server can inspect or run.",
        annotations=READ_ONLY,
    )
    def list_projects() -> dict[str, Any]:
        return service.list_projects()

    @server.tool(
        name="project_summary",
        description="Summarize one local Python project, including interpreter, config files, test hints, and entrypoint candidates.",
        annotations=READ_ONLY,
    )
    def project_summary(project_path: str | None = None) -> dict[str, Any]:
        return service.project_summary(project_path=project_path)

    @server.tool(
        name="list_test_targets",
        description="List discovered Python test files inside one configured project.",
        annotations=READ_ONLY,
    )
    def list_test_targets(project_path: str | None = None, limit: int = 100) -> dict[str, Any]:
        return service.list_test_targets(project_path=project_path, limit=limit)

    @server.tool(
        name="syntax_check",
        description="Read-only Python syntax check for project files or selected paths. This compiles source in memory without writing bytecode files.",
        annotations=READ_ONLY,
    )
    def syntax_check(
        project_path: str | None = None,
        paths: list[str] | None = None,
        file_limit: int = 200,
        max_errors: int = 20,
    ) -> dict[str, Any]:
        return service.syntax_check(
            project_path=project_path,
            paths=paths,
            file_limit=file_limit,
            max_errors=max_errors,
        )

    @server.tool(
        name="run_script",
        description="Run a Python script inside the configured project. This is execution-capable and should only be used in write-enabled runs.",
    )
    def run_script(
        script_path: str,
        project_path: str | None = None,
        script_args: list[str] | None = None,
        python_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.run_script(
            script_path=script_path,
            project_path=project_path,
            script_args=script_args,
            python_args=python_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    @server.tool(
        name="run_module",
        description="Run a Python module with `python -m ...` inside the configured project. This is execution-capable and should only be used in write-enabled runs.",
    )
    def run_module(
        module: str,
        project_path: str | None = None,
        module_args: list[str] | None = None,
        python_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.run_module(
            module=module,
            project_path=project_path,
            module_args=module_args,
            python_args=python_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    @server.tool(
        name="run_pytest",
        description="Run pytest for the configured project or selected test targets. This is execution-capable and should only be used in write-enabled runs.",
    )
    def run_pytest(
        project_path: str | None = None,
        targets: list[str] | None = None,
        keyword: str | None = None,
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.run_pytest(
            project_path=project_path,
            targets=targets,
            keyword=keyword,
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    @server.tool(
        name="run_unittest",
        description="Run unittest for the configured project, either for explicit targets or via discovery. This is execution-capable and should only be used in write-enabled runs.",
    )
    def run_unittest(
        project_path: str | None = None,
        targets: list[str] | None = None,
        discover: bool = True,
        start_directory: str = ".",
        pattern: str = "test*.py",
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.run_unittest(
            project_path=project_path,
            targets=targets,
            discover=discover,
            start_directory=start_directory,
            pattern=pattern,
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    return server


def main() -> int:
    args = parse_args()
    service = PythonService(args.project_root)
    server = build_server(service)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
