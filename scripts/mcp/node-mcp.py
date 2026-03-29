from __future__ import annotations

import argparse
import json
import locale
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
LOCKFILE_TO_MANAGER = {
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
}
IGNORE_SCAN_DIRS = {
    ".git",
    ".next",
    ".turbo",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
}
COMMON_OUTPUT_DIRS = ("build", "dist", ".next", "out")


@dataclass(frozen=True)
class NodeProjectRecord:
    name: str
    root: Path
    manifest_path: Path
    package_name: str | None
    version: str | None
    package_manager: str
    package_manager_source: str
    lockfile: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
            "manifest_path": str(self.manifest_path),
            "package_name": self.package_name,
            "version": self.version,
            "package_manager": self.package_manager,
            "package_manager_source": self.package_manager_source,
            "lockfile": self.lockfile,
        }


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    output: str
    truncated: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MCP server for local Node.js projects and build workflows."
    )
    parser.add_argument(
        "--project-root",
        action="append",
        default=[],
        help="Allowed Node project root that contains a package.json file. Repeat to allow multiple projects.",
    )
    return parser.parse_args()


class NodeService:
    def __init__(self, project_roots: list[str]) -> None:
        if not shutil.which("node"):
            raise RuntimeError("The 'node' command was not found. Install Node.js first.")

        self._manager_paths = {
            name: shutil.which(name)
            for name in ("npm", "pnpm", "yarn")
        }

        roots = project_roots or self._detect_project_roots()
        if not roots:
            raise RuntimeError(
                "No Node.js projects were detected. Pass --project-root or register a project explicitly."
            )

        records_by_root: dict[Path, NodeProjectRecord] = {}
        for root in roots:
            record = self._fetch_project_record(Path(root))
            records_by_root[record.root] = record

        self._records = dict(sorted(records_by_root.items(), key=lambda item: str(item[0]).lower()))

    @property
    def records(self) -> list[NodeProjectRecord]:
        return list(self._records.values())

    def _read_manifest(self, manifest_path: Path) -> dict[str, Any]:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid package.json in '{manifest_path}': {exc}") from exc
        if not isinstance(manifest, dict):
            raise RuntimeError(f"Unexpected package.json payload in '{manifest_path}'.")
        return manifest

    def _detect_project_roots(self) -> list[str]:
        cwd = Path.cwd().resolve(strict=False)
        matches: list[Path] = []
        if (cwd / "package.json").exists():
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
                if not child.is_dir():
                    continue
                if child.name in IGNORE_SCAN_DIRS or child.name.startswith(".venv"):
                    continue
                if (child / "package.json").exists():
                    matches.append(child.resolve(strict=False))
                    continue
                queue.append((child, depth + 1))

        unique_matches = sorted({item.resolve(strict=False) for item in matches}, key=lambda item: str(item).lower())
        return [str(item) for item in unique_matches]

    def _find_lockfile(self, root: Path) -> str | None:
        for filename in LOCKFILE_TO_MANAGER.keys():
            if (root / filename).exists():
                return filename
        return None

    def _detect_package_manager(self, manifest: dict[str, Any], root: Path) -> tuple[str, str, str | None]:
        declared = manifest.get("packageManager")
        if isinstance(declared, str) and declared.strip():
            manager_name = declared.strip().split("@", 1)[0].strip().lower()
            if manager_name in self._manager_paths:
                return manager_name, "packageManager", self._find_lockfile(root)

        lockfile = self._find_lockfile(root)
        if lockfile:
            return LOCKFILE_TO_MANAGER[lockfile], "lockfile", lockfile

        return "npm", "default", None

    def _fetch_project_record(self, root: Path) -> NodeProjectRecord:
        resolved = root.resolve(strict=False)
        manifest_path = resolved / "package.json"
        if not manifest_path.exists():
            raise RuntimeError(f"No package.json was found in '{resolved}'.")

        manifest = self._read_manifest(manifest_path)
        package_name = manifest.get("name")
        if package_name is not None and not isinstance(package_name, str):
            package_name = None
        version = manifest.get("version")
        if version is not None and not isinstance(version, str):
            version = None

        package_manager, manager_source, lockfile = self._detect_package_manager(manifest, resolved)
        display_name = package_name or resolved.name
        return NodeProjectRecord(
            name=display_name,
            root=resolved,
            manifest_path=manifest_path,
            package_name=package_name,
            version=version,
            package_manager=package_manager,
            package_manager_source=manager_source,
            lockfile=lockfile,
        )

    def _default_project(self) -> NodeProjectRecord:
        if len(self.records) == 1:
            return self.records[0]
        roots = ", ".join(str(record.root) for record in self.records)
        raise ValueError(
            "Multiple Node projects are configured. Pass project_path explicitly or use an absolute path inside one of: "
            f"{roots}"
        )

    def _find_project_for_path(self, candidate: Path) -> NodeProjectRecord | None:
        resolved = candidate.resolve(strict=False)
        matches = [
            record for record in self.records if resolved == record.root or resolved.is_relative_to(record.root)
        ]
        if not matches:
            return None
        return max(matches, key=lambda record: len(str(record.root)))

    def resolve_project(self, project_path: str | None = None) -> NodeProjectRecord:
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
            raise ValueError(f"Path '{raw_candidate}' is outside the allowed Node projects. Allowed roots: {allowed}")
        return project

    def _manifest_for_project(self, project: NodeProjectRecord) -> dict[str, Any]:
        return self._read_manifest(project.manifest_path)

    def _scripts_for_manifest(self, manifest: dict[str, Any]) -> dict[str, str]:
        raw_scripts = manifest.get("scripts")
        if not isinstance(raw_scripts, dict):
            return {}

        scripts: dict[str, str] = {}
        for name, command in raw_scripts.items():
            if isinstance(name, str) and isinstance(command, str):
                scripts[name] = command
        return dict(sorted(scripts.items()))

    def _detect_framework(self, manifest: dict[str, Any]) -> str | None:
        dependencies: dict[str, Any] = {}
        for field_name in ("dependencies", "devDependencies"):
            field = manifest.get(field_name)
            if isinstance(field, dict):
                dependencies.update(field)

        if "next" in dependencies:
            return "next"
        if "react-scripts" in dependencies:
            return "create-react-app"
        if "vite" in dependencies:
            return "vite"
        if "@nestjs/core" in dependencies:
            return "nestjs"
        if "express" in dependencies:
            return "express"
        return None

    def _has_dependencies_installed(self, project: NodeProjectRecord) -> bool:
        if (project.root / "node_modules").exists():
            return True
        return (project.root / ".pnp.cjs").exists() or (project.root / ".pnp.js").exists()

    def _detect_output_directories(self, project: NodeProjectRecord) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for directory_name in COMMON_OUTPUT_DIRS:
            directory = project.root / directory_name
            if directory.exists():
                outputs.append(
                    {
                        "path": str(directory),
                        "name": directory_name,
                        "is_directory": directory.is_dir(),
                    }
                )
        return outputs

    def _resolve_manager_command(self, project: NodeProjectRecord) -> str:
        manager = project.package_manager
        command_path = self._manager_paths.get(manager)
        if not command_path:
            raise RuntimeError(
                f"The configured package manager '{manager}' for '{project.root}' is not available on this machine."
            )
        return manager

    def _prepare_install_command(self, project: NodeProjectRecord, frozen_lockfile: bool) -> list[str]:
        manager = self._resolve_manager_command(project)
        has_lockfile = bool(project.lockfile)
        if manager == "npm":
            if frozen_lockfile and has_lockfile:
                return [manager, "ci"]
            return [manager, "install"]
        if manager == "pnpm":
            command = [manager, "install"]
            if frozen_lockfile and has_lockfile:
                command.append("--frozen-lockfile")
            return command
        if manager == "yarn":
            command = [manager, "install"]
            if frozen_lockfile and has_lockfile:
                command.append("--frozen-lockfile")
            return command
        raise RuntimeError(f"Unsupported package manager '{manager}'.")

    def _prepare_run_command(
        self,
        project: NodeProjectRecord,
        script: str,
        extra_args: list[str] | None,
    ) -> list[str]:
        manager = self._resolve_manager_command(project)
        command = [manager, "run", script]
        if extra_args:
            if manager in {"npm", "pnpm"}:
                command.append("--")
            command.extend(extra_args)
        return command

    def _truncate_text(self, text: str, max_chars: int) -> tuple[str, bool]:
        normalized = text.strip()
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized, False
        return normalized[:max_chars], True

    def _run_command(
        self,
        *,
        project: NodeProjectRecord,
        command: list[str],
        timeout_seconds: int,
        max_chars: int,
    ) -> CommandResult:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        env = os.environ.copy()
        env.setdefault("FORCE_COLOR", "0")
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
                f"Node command timed out after {timeout_seconds} seconds: {' '.join(command)}\n{truncated}"
            ) from exc

        combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        truncated_output, truncated = self._truncate_text(combined, max_chars)
        if result.returncode != 0:
            details = truncated_output or f"exit code {result.returncode}"
            raise RuntimeError(f"Node command failed: {' '.join(command)}\n{details}")

        return CommandResult(
            command=command,
            output=truncated_output,
            truncated=truncated,
        )

    def list_projects(self) -> dict[str, Any]:
        projects: list[dict[str, Any]] = []
        for record in self.records:
            manifest = self._manifest_for_project(record)
            scripts = self._scripts_for_manifest(manifest)
            projects.append(
                {
                    **record.to_dict(),
                    "has_node_modules": self._has_dependencies_installed(record),
                    "script_count": len(scripts),
                    "has_build_script": "build" in scripts,
                    "framework": self._detect_framework(manifest),
                }
            )
        return {"projects": projects}

    def project_summary(self, project_path: str | None = None) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        manifest = self._manifest_for_project(project)
        scripts = self._scripts_for_manifest(manifest)
        return {
            "project": project.to_dict(),
            "private": bool(manifest.get("private", False)),
            "type": manifest.get("type"),
            "framework": self._detect_framework(manifest),
            "engines": manifest.get("engines"),
            "volta": manifest.get("volta"),
            "has_node_modules": self._has_dependencies_installed(project),
            "script_count": len(scripts),
            "scripts": list(scripts.keys()),
            "has_build_script": "build" in scripts,
            "build_script": scripts.get("build"),
            "start_script": scripts.get("start"),
            "test_script": scripts.get("test"),
            "output_directories": self._detect_output_directories(project),
        }

    def list_scripts(self, project_path: str | None = None) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        manifest = self._manifest_for_project(project)
        scripts = self._scripts_for_manifest(manifest)
        return {
            "project": project.to_dict(),
            "script_count": len(scripts),
            "scripts": scripts,
            "has_build_script": "build" in scripts,
            "recommended_build_script": "build" if "build" in scripts else None,
        }

    def install_dependencies(
        self,
        project_path: str | None = None,
        frozen_lockfile: bool = True,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        command = self._prepare_install_command(project, frozen_lockfile=frozen_lockfile)
        result = self._run_command(
            project=project,
            command=command,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "command": " ".join(result.command),
            "timeout_seconds": timeout_seconds,
            "dependencies_installed": self._has_dependencies_installed(project),
            "output": result.output or None,
            "truncated": result.truncated,
        }

    def run_script(
        self,
        script: str,
        project_path: str | None = None,
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        manifest = self._manifest_for_project(project)
        scripts = self._scripts_for_manifest(manifest)
        if script not in scripts:
            available = ", ".join(sorted(scripts.keys())) or "none"
            raise RuntimeError(
                f"Script '{script}' was not found in '{project.root}'. Available scripts: {available}"
            )

        command = self._prepare_run_command(project, script=script, extra_args=extra_args)
        result = self._run_command(
            project=project,
            command=command,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "script": script,
            "script_command": scripts.get(script),
            "command": " ".join(result.command),
            "timeout_seconds": timeout_seconds,
            "output": result.output or None,
            "truncated": result.truncated,
        }

    def build_project(
        self,
        project_path: str | None = None,
        script: str = "build",
        install_if_needed: bool = False,
        frozen_lockfile: bool = True,
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        install_result: dict[str, Any] | None = None

        if install_if_needed and not self._has_dependencies_installed(project):
            install_result = self.install_dependencies(
                project_path=str(project.root),
                frozen_lockfile=frozen_lockfile,
                timeout_seconds=timeout_seconds,
                max_chars=max_chars,
            )
        elif not install_if_needed and not self._has_dependencies_installed(project):
            raise RuntimeError(
                "Dependencies do not appear to be installed. Run install_dependencies first or call build_project with install_if_needed=true."
            )

        script_result = self.run_script(
            script=script,
            project_path=str(project.root),
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
        return {
            "project": project.to_dict(),
            "script": script,
            "install": install_result,
            "build": script_result,
            "output_directories": self._detect_output_directories(project),
        }


def build_server(service: NodeService) -> FastMCP:
    server = FastMCP("node")

    @server.tool(
        name="list_projects",
        description="List the configured local Node.js projects that this MCP server can inspect or build.",
        annotations=READ_ONLY,
    )
    def list_projects() -> dict[str, Any]:
        return service.list_projects()

    @server.tool(
        name="project_summary",
        description="Summarize one local Node.js project, including package manager, scripts, engines, and likely output directories.",
        annotations=READ_ONLY,
    )
    def project_summary(project_path: str | None = None) -> dict[str, Any]:
        return service.project_summary(project_path=project_path)

    @server.tool(
        name="list_scripts",
        description="List npm, pnpm, or yarn scripts from package.json for one configured project.",
        annotations=READ_ONLY,
    )
    def list_scripts(project_path: str | None = None) -> dict[str, Any]:
        return service.list_scripts(project_path=project_path)

    @server.tool(
        name="install_dependencies",
        description="Install project dependencies with the detected package manager. This changes node_modules or lockfile state and should only be used in write-enabled runs.",
    )
    def install_dependencies(
        project_path: str | None = None,
        frozen_lockfile: bool = True,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.install_dependencies(
            project_path=project_path,
            frozen_lockfile=frozen_lockfile,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    @server.tool(
        name="run_script",
        description="Run one package.json script through the detected package manager. This can start dev servers, tests, or build commands and should only be used in write-enabled runs.",
    )
    def run_script(
        script: str,
        project_path: str | None = None,
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.run_script(
            script=script,
            project_path=project_path,
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    @server.tool(
        name="build_project",
        description="Run the build workflow for a local Node.js project, usually through the build script in package.json. Optionally installs dependencies first.",
    )
    def build_project(
        project_path: str | None = None,
        script: str = "build",
        install_if_needed: bool = False,
        frozen_lockfile: bool = True,
        extra_args: list[str] | None = None,
        timeout_seconds: int = 1800,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.build_project(
            project_path=project_path,
            script=script,
            install_if_needed=install_if_needed,
            frozen_lockfile=frozen_lockfile,
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )

    return server


def main() -> int:
    args = parse_args()
    service = NodeService(args.project_root)
    server = build_server(service)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
