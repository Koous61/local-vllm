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
COMPOSE_FILE_NAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)


@dataclass(frozen=True)
class DockerProjectRecord:
    name: str
    root: Path
    compose_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
            "compose_file": self.compose_file,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only MCP server for Docker and Docker Compose inspection."
    )
    parser.add_argument(
        "--project-root",
        action="append",
        default=[],
        help="Allowed Docker Compose project root. Repeat to allow multiple projects.",
    )
    return parser.parse_args()


class DockerService:
    def __init__(self, project_roots: list[str]) -> None:
        if not shutil.which("docker"):
            raise RuntimeError("The 'docker' command was not found. Install Docker Desktop first.")

        roots = project_roots or self._detect_project_roots()
        if not roots:
            raise RuntimeError(
                "No Docker Compose projects were detected. Pass --project-root or register a project explicitly."
            )

        records_by_root: dict[Path, DockerProjectRecord] = {}
        for root in roots:
            record = self._fetch_project_record(Path(root))
            records_by_root[record.root] = record

        self._records = dict(sorted(records_by_root.items(), key=lambda item: str(item[0]).lower()))

    @property
    def records(self) -> list[DockerProjectRecord]:
        return list(self._records.values())

    def _run_docker(self, args: list[str], cwd: Path | None = None) -> str:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        env = os.environ.copy()
        env.setdefault("COMPOSE_INTERACTIVE_NO_CLI", "1")
        env.setdefault("DOCKER_CLI_HINTS", "false")
        result = subprocess.run(
            ["docker", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            command = " ".join(["docker", *args])
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            details = stderr or stdout or f"exit code {result.returncode}"
            raise RuntimeError(f"Docker command failed: {command}\n{details}")
        return result.stdout.strip()

    def _detect_project_roots(self) -> list[str]:
        cwd = Path.cwd().resolve(strict=False)
        if self._find_compose_file(cwd):
            return [str(cwd)]
        return []

    def _find_compose_file(self, root: Path) -> str | None:
        for name in COMPOSE_FILE_NAMES:
            if (root / name).exists():
                return name
        return None

    def _fetch_project_record(self, root: Path) -> DockerProjectRecord:
        resolved = root.resolve(strict=False)
        compose_file = self._find_compose_file(resolved)
        if not compose_file:
            raise RuntimeError(
                f"No compose file was found in '{resolved}'. Expected one of: {', '.join(COMPOSE_FILE_NAMES)}"
            )
        return DockerProjectRecord(
            name=resolved.name,
            root=resolved,
            compose_file=compose_file,
        )

    def _default_project(self) -> DockerProjectRecord:
        if len(self.records) == 1:
            return self.records[0]
        roots = ", ".join(str(record.root) for record in self.records)
        raise ValueError(
            "Multiple Docker Compose projects are configured. Pass project_path explicitly or use an absolute path inside one of: "
            f"{roots}"
        )

    def resolve_project(self, project_path: str | None = None) -> DockerProjectRecord:
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

        resolved = raw_candidate.resolve(strict=False)
        for record in self.records:
            if resolved == record.root:
                return record

        allowed = ", ".join(str(record.root) for record in self.records)
        raise ValueError(f"Path '{resolved}' is outside the allowed Docker projects. Allowed roots: {allowed}")

    def _parse_json_output(self, output: str) -> Any:
        stripped = output.strip()
        if not stripped:
            return []
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        items: list[Any] = []
        for raw_line in stripped.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            items.append(json.loads(line))
        return items

    def _parse_labels(self, raw_labels: str | None) -> dict[str, str]:
        if not raw_labels:
            return {}

        labels: dict[str, str] = {}
        for item in str(raw_labels).split(","):
            part = item.strip()
            if not part:
                continue
            if "=" in part:
                key, value = part.split("=", 1)
                labels[key] = value
            else:
                labels[part] = ""
        return labels

    def _infer_health(self, raw_status: str | None) -> str | None:
        if not raw_status:
            return None
        lowered = raw_status.lower()
        if "(healthy)" in lowered or " healthy" in lowered:
            return "healthy"
        if "(unhealthy)" in lowered or " unhealthy" in lowered:
            return "unhealthy"
        if "starting" in lowered and "health" in lowered:
            return "starting"
        return None

    def _list_container_entries(self, all_containers: bool = True) -> list[dict[str, Any]]:
        args = ["ps", "--format", "{{json .}}"]
        if all_containers:
            args.insert(1, "-a")
        output = self._run_docker(args)
        raw_entries = self._parse_json_output(output)
        if isinstance(raw_entries, dict):
            entries = [raw_entries]
        elif isinstance(raw_entries, list):
            entries = [item for item in raw_entries if isinstance(item, dict)]
        else:
            entries = []

        normalized: list[dict[str, Any]] = []
        for item in entries:
            labels = self._parse_labels(item.get("Labels"))
            normalized.append(
                {
                    "id": item.get("ID") or item.get("Id"),
                    "name": item.get("Names") or item.get("Name"),
                    "image": item.get("Image"),
                    "command": item.get("Command"),
                    "created_at": item.get("CreatedAt"),
                    "running_for": item.get("RunningFor"),
                    "status": item.get("Status"),
                    "state": item.get("State"),
                    "ports": item.get("Ports"),
                    "mounts": item.get("Mounts"),
                    "networks": item.get("Networks"),
                    "labels": labels,
                    "compose_project": labels.get("com.docker.compose.project"),
                    "compose_working_dir": labels.get("com.docker.compose.project.working_dir"),
                    "compose_service": labels.get("com.docker.compose.service"),
                    "health": self._infer_health(item.get("Status")),
                }
            )
        return normalized

    def _is_project_container(self, container: dict[str, Any], project: DockerProjectRecord) -> bool:
        working_dir = container.get("compose_working_dir")
        if isinstance(working_dir, str) and working_dir:
            try:
                return Path(working_dir).resolve(strict=False) == project.root
            except Exception:
                return working_dir.lower() == str(project.root).lower()
        return container.get("compose_project") == project.name

    def _normalize_compose_entries(self, raw_entries: Any) -> list[dict[str, Any]]:
        if isinstance(raw_entries, dict):
            entries = [raw_entries]
        elif isinstance(raw_entries, list):
            entries = [item for item in raw_entries if isinstance(item, dict)]
        else:
            entries = []

        normalized: list[dict[str, Any]] = []
        for item in entries:
            ports = item.get("Publishers")
            if ports is None:
                ports = item.get("Ports")
            normalized.append(
                {
                    "service": item.get("Service") or item.get("Name") or item.get("Names"),
                    "container_name": item.get("Name") or item.get("Names"),
                    "state": item.get("State"),
                    "status": item.get("Status"),
                    "health": item.get("Health"),
                    "id": item.get("ID") or item.get("Id"),
                    "image": item.get("Image"),
                    "command": item.get("Command"),
                    "ports": ports,
                    "project": item.get("Project"),
                    "exit_code": item.get("ExitCode"),
                }
            )
        return normalized

    def _truncate_text(self, text: str, max_chars: int) -> tuple[str, bool]:
        normalized = text.strip()
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized, False
        return normalized[:max_chars], True

    def list_projects(self) -> dict[str, Any]:
        return {
            "projects": [record.to_dict() for record in self.records],
        }

    def compose_ps(self, project_path: str | None = None, all_services: bool = True) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        entries = [
            {
                "service": item.get("compose_service") or item.get("name"),
                "container_name": item.get("name"),
                "state": item.get("state"),
                "status": item.get("status"),
                "health": item.get("health"),
                "id": item.get("id"),
                "image": item.get("image"),
                "command": item.get("command"),
                "ports": item.get("ports"),
                "project": item.get("compose_project"),
            }
            for item in self._list_container_entries(all_containers=all_services)
            if self._is_project_container(item, project)
        ]
        if not all_services:
            entries = [item for item in entries if item.get("state") == "running"]
        return {
            "project": project.to_dict(),
            "services": entries,
        }

    def compose_status_summary(
        self,
        project_path: str | None = None,
        all_services: bool = True,
        sample_limit: int = 12,
    ) -> dict[str, Any]:
        result = self.compose_ps(project_path=project_path, all_services=all_services)
        services = result["services"]
        normalized_limit = max(1, min(sample_limit, 50))
        state_counts: dict[str, int] = {}
        health_counts: dict[str, int] = {}
        sample: list[dict[str, Any]] = []

        for item in services:
            state = str(item.get("state") or "unknown")
            state_counts[state] = state_counts.get(state, 0) + 1
            health = item.get("health")
            if health:
                health_counts[str(health)] = health_counts.get(str(health), 0) + 1
            if len(sample) < normalized_limit:
                sample.append(
                    {
                        "service": item.get("service"),
                        "container_name": item.get("container_name"),
                        "state": item.get("state"),
                        "status": item.get("status"),
                        "health": item.get("health"),
                        "image": item.get("image"),
                    }
                )

        project = result["project"]
        running_count = state_counts.get("running", 0)
        headline = f"{project['name']}: {running_count}/{len(services)} service(s) running"
        return {
            "project": project,
            "headline": headline,
            "service_count": len(services),
            "running_count": running_count,
            "state_counts": dict(sorted(state_counts.items())),
            "health_counts": dict(sorted(health_counts.items())),
            "truncated": len(services) > normalized_limit,
            "sample_services": sample,
        }

    def compose_logs(
        self,
        project_path: str | None = None,
        service: str | None = None,
        tail: int = 200,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        project = self.resolve_project(project_path)
        normalized_tail = max(1, min(tail, 1000))
        project_containers = [
            item for item in self._list_container_entries(all_containers=True)
            if self._is_project_container(item, project)
        ]
        if service:
            selected = [item for item in project_containers if item.get("compose_service") == service]
            if not selected:
                raise ValueError(f"Service '{service}' was not found in Docker project '{project.name}'.")
        else:
            selected = project_containers

        chunks: list[str] = []
        for item in selected:
            container_name = str(item.get("name"))
            output = self._run_docker(["logs", f"--tail={normalized_tail}", container_name])
            header = f"===== {container_name} ====="
            body = output.strip()
            chunks.append(header if not body else f"{header}\n{body}")

        combined = "\n\n".join(chunk for chunk in chunks if chunk)
        logs, truncated = self._truncate_text(combined, max_chars)
        return {
            "project": project.to_dict(),
            "service": service,
            "tail": normalized_tail,
            "logs": logs or None,
            "truncated": truncated,
            "is_empty": not bool(combined.strip()),
        }

    def list_containers(self, all_containers: bool = True, limit: int = 100) -> dict[str, Any]:
        normalized_limit = max(1, min(limit, 200))
        entries = self._list_container_entries(all_containers=all_containers)
        return {
            "total_count": len(entries),
            "truncated": len(entries) > normalized_limit,
            "containers": entries[:normalized_limit],
        }

    def container_inspect(self, container: str) -> dict[str, Any]:
        output = self._run_docker(["inspect", container])
        parsed = self._parse_json_output(output)
        if isinstance(parsed, list) and parsed:
            item = parsed[0]
        elif isinstance(parsed, dict):
            item = parsed
        else:
            raise RuntimeError(f"Unexpected docker inspect output for '{container}'.")

        state = item.get("State") or {}
        config = item.get("Config") or {}
        host_config = item.get("HostConfig") or {}
        network_settings = item.get("NetworkSettings") or {}
        return {
            "id": item.get("Id"),
            "name": str(item.get("Name", "")).lstrip("/"),
            "image": config.get("Image"),
            "command": config.get("Cmd"),
            "entrypoint": config.get("Entrypoint"),
            "state": state,
            "restart_policy": host_config.get("RestartPolicy"),
            "networks": network_settings.get("Networks"),
            "mounts": item.get("Mounts"),
            "labels": config.get("Labels"),
        }

    def list_images(self, limit: int = 100) -> dict[str, Any]:
        normalized_limit = max(1, min(limit, 200))
        output = self._run_docker(["image", "ls", "--format", "{{json .}}"])
        raw_entries = self._parse_json_output(output)
        if isinstance(raw_entries, dict):
            entries = [raw_entries]
        elif isinstance(raw_entries, list):
            entries = [item for item in raw_entries if isinstance(item, dict)]
        else:
            entries = []
        return {
            "total_count": len(entries),
            "truncated": len(entries) > normalized_limit,
            "images": entries[:normalized_limit],
        }


def build_server(service: DockerService) -> FastMCP:
    server = FastMCP("docker")

    @server.tool(
        name="list_projects",
        description="List the configured Docker Compose projects that this MCP server can inspect.",
        annotations=READ_ONLY,
    )
    def list_projects() -> dict[str, Any]:
        return service.list_projects()

    @server.tool(
        name="compose_status_summary",
        description="Fast compact Docker Compose summary. Best first tool for project status, service states, health, and a short service sample.",
        annotations=READ_ONLY,
    )
    def compose_status_summary(
        project_path: str | None = None,
        all_services: bool = True,
        sample_limit: int = 12,
    ) -> dict[str, Any]:
        return service.compose_status_summary(
            project_path=project_path,
            all_services=all_services,
            sample_limit=sample_limit,
        )

    @server.tool(
        name="compose_ps",
        description="Detailed Docker Compose service listing for a configured project.",
        annotations=READ_ONLY,
    )
    def compose_ps(project_path: str | None = None, all_services: bool = True) -> dict[str, Any]:
        return service.compose_ps(project_path=project_path, all_services=all_services)

    @server.tool(
        name="compose_logs",
        description="Fetch recent Docker Compose logs for a configured project or one specific service.",
        annotations=READ_ONLY,
    )
    def compose_logs(
        project_path: str | None = None,
        service: str | None = None,
        tail: int = 200,
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        return service.compose_logs(
            project_path=project_path,
            service=service,
            tail=tail,
            max_chars=max_chars,
        )

    @server.tool(
        name="list_containers",
        description="List local Docker containers on this machine. Useful when you need a broader Docker view than one Compose project.",
        annotations=READ_ONLY,
    )
    def list_containers(all_containers: bool = True, limit: int = 100) -> dict[str, Any]:
        return service.list_containers(all_containers=all_containers, limit=limit)

    @server.tool(
        name="container_inspect",
        description="Inspect one Docker container by name or id.",
        annotations=READ_ONLY,
    )
    def container_inspect(container: str) -> dict[str, Any]:
        return service.container_inspect(container=container)

    @server.tool(
        name="list_images",
        description="List local Docker images on this machine.",
        annotations=READ_ONLY,
    )
    def list_images(limit: int = 100) -> dict[str, Any]:
        return service.list_images(limit=limit)

    return server


def main() -> int:
    args = parse_args()
    service = DockerService(args.project_root)
    server = build_server(service)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
