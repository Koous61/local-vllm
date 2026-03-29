from __future__ import annotations

import argparse
from collections import Counter
import locale
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


WORKSPACE_FORMAT = "{0}|{1}|{2}|{3}|{4}|{5}|{6}"
FILEINFO_FORMAT = "{RepSpec}|{ClientPath}|{ServerPath}|{RevisionChangeset}|{Status}"
MAIN_BRANCH_FORMAT = "{repname}|{repository}|{repserver}|{name}|{changeset}"
HISTORY_FORMAT = "{date}|{changesetid}|{owner}|{comment}"
WORKSPACE_LINE_PATTERN = re.compile(r"^(?P<label>\S+)\s+(?P<path>.+)$")
STATUS_HEADER_PATTERN = re.compile(
    r"^(?P<branch>.+?)\s+\(cs:(?P<changeset>\d+)\s*-\s*(?P<head_state>[^)]+)\)$"
)
STATUS_KIND_MAP = {
    "CH": "changed",
    "CO": "checked_out",
    "CP": "copied",
    "DE": "deleted",
    "LD": "local_deleted",
    "LM": "local_moved",
    "MV": "moved",
    "PR": "private",
}
UNREAL_AREA_ORDER = [
    "Project",
    "Source",
    "Config",
    "Content",
    "Plugins",
    "Build",
    "Binaries",
    "Intermediate",
    "Saved",
    "Devops",
    "Docs",
    "Samples",
    "Test_Data",
    "Other",
]
KNOWN_UNREAL_AREAS = set(UNREAL_AREA_ORDER[1:-1])
UNREAL_ASSET_EXTENSIONS = {".uasset", ".umap"}
UNREAL_CODE_EXTENSIONS = {".h", ".hpp", ".hh", ".inl", ".c", ".cc", ".cpp", ".cxx"}
UNREAL_GAMEPLAY_CODE_ROLES = {"code", "plugin_code"}
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)


@dataclass(frozen=True)
class WorkspaceRecord:
    name: str
    root: Path
    machine: str | None
    owner: str | None
    workspace_id: str | None
    workspace_type: str | None
    mode: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
            "machine": self.machine,
            "owner": self.owner,
            "workspace_id": self.workspace_id,
            "workspace_type": self.workspace_type,
            "mode": self.mode,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only MCP server for UVCS / Plastic SCM repositories, including Unreal Engine projects."
    )
    parser.add_argument(
        "--workspace-root",
        action="append",
        default=[],
        help="Allowed UVCS workspace root. Repeat to allow multiple workspaces.",
    )
    return parser.parse_args()


class UvcsService:
    def __init__(self, workspace_roots: list[str]) -> None:
        if not shutil.which("cm"):
            raise RuntimeError(
                "The 'cm' command was not found. Install the UVCS / Plastic SCM client first."
            )

        roots = workspace_roots or self._detect_workspace_paths()
        if not roots:
            raise RuntimeError(
                "No UVCS workspaces were detected. Pass --workspace-root or create a local workspace first."
            )

        records_by_root: dict[Path, WorkspaceRecord] = {}
        for root in roots:
            record = self._fetch_workspace_record(Path(root))
            records_by_root[record.root] = record

        self._records = dict(sorted(records_by_root.items(), key=lambda item: str(item[0]).lower()))

    @property
    def records(self) -> list[WorkspaceRecord]:
        return list(self._records.values())

    def _run_cm(self, args: list[str], cwd: Path | None = None) -> str:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        result = subprocess.run(
            ["cm", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            command = " ".join(["cm", *args])
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            details = stderr or stdout or f"exit code {result.returncode}"
            raise RuntimeError(f"UVCS command failed: {command}\n{details}")
        return result.stdout.strip()

    def _detect_workspace_paths(self) -> list[str]:
        output = self._run_cm(["workspace", "list"])
        paths: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = WORKSPACE_LINE_PATTERN.match(line)
            if not match:
                continue
            paths.append(match.group("path").strip())
        return paths

    def _fetch_workspace_record(self, root: Path) -> WorkspaceRecord:
        raw = self._run_cm(
            [
                "getworkspacefrompath",
                str(root),
                f"--format={WORKSPACE_FORMAT}",
                "--extended",
            ]
        )
        parts = raw.split("|", 6)
        if len(parts) != 7:
            raise RuntimeError(f"Unexpected workspace metadata format: {raw}")
        return WorkspaceRecord(
            name=parts[0].strip(),
            root=Path(parts[1].strip()).resolve(strict=False),
            machine=self._none_if_empty(parts[2]),
            owner=self._none_if_empty(parts[3]),
            workspace_id=self._none_if_empty(parts[4]),
            workspace_type=self._none_if_empty(parts[5]),
            mode=self._none_if_empty(parts[6]),
        )

    def _none_if_empty(self, value: str) -> str | None:
        cleaned = value.strip()
        return cleaned or None

    def _default_workspace(self) -> WorkspaceRecord:
        if len(self.records) == 1:
            return self.records[0]
        roots = ", ".join(str(record.root) for record in self.records)
        raise ValueError(
            "Multiple UVCS workspaces are configured. Pass an explicit workspace_path or an absolute path inside one of: "
            f"{roots}"
        )

    def _find_workspace_for_path(self, candidate: Path) -> WorkspaceRecord | None:
        resolved = candidate.resolve(strict=False)
        matches = [
            record for record in self.records if resolved == record.root or resolved.is_relative_to(record.root)
        ]
        if not matches:
            return None
        return max(matches, key=lambda record: len(str(record.root)))

    def resolve_workspace(self, workspace_path: str | None = None) -> WorkspaceRecord:
        if not workspace_path:
            return self._default_workspace()

        raw_candidate = Path(workspace_path).expanduser()
        if not raw_candidate.is_absolute():
            name_match = next(
                (record for record in self.records if record.name.lower() == workspace_path.strip().lower()),
                None,
            )
            if name_match:
                return name_match
            raw_candidate = self._default_workspace().root / raw_candidate

        workspace = self._find_workspace_for_path(raw_candidate)
        if workspace is None:
            allowed = ", ".join(str(record.root) for record in self.records)
            raise ValueError(
                f"Path '{raw_candidate}' is outside the allowed UVCS workspaces. Allowed roots: {allowed}"
            )
        return workspace

    def resolve_item_path(self, path: str | None = None) -> tuple[WorkspaceRecord, Path]:
        if not path:
            workspace = self._default_workspace()
            return workspace, workspace.root

        raw_candidate = Path(path).expanduser()
        if not raw_candidate.is_absolute():
            workspace = self._default_workspace()
            raw_candidate = workspace.root / raw_candidate

        workspace = self._find_workspace_for_path(raw_candidate)
        if workspace is None:
            allowed = ", ".join(str(record.root) for record in self.records)
            raise ValueError(
                f"Path '{raw_candidate}' is outside the allowed UVCS workspaces. Allowed roots: {allowed}"
            )
        return workspace, raw_candidate.resolve(strict=False)

    def _parse_fileinfo(self, target: Path) -> dict[str, Any]:
        raw = self._run_cm(["fileinfo", str(target), f"--format={FILEINFO_FORMAT}"])
        parts = raw.split("|", 4)
        if len(parts) != 5:
            raise RuntimeError(f"Unexpected fileinfo format: {raw}")
        return {
            "rep_spec": parts[0].strip() or None,
            "client_path": parts[1].strip() or None,
            "server_path": parts[2].strip() or None,
            "revision_changeset": self._parse_int(parts[3]),
            "status": parts[4].strip() or None,
        }

    def _parse_status_header(self, workspace: WorkspaceRecord) -> dict[str, Any]:
        raw = self._run_cm(["status", str(workspace.root), "--header"])
        match = STATUS_HEADER_PATTERN.match(raw)
        if not match:
            return {
                "current_branch": raw,
                "current_changeset": None,
                "branch_state": None,
            }
        return {
            "current_branch": match.group("branch").strip(),
            "current_changeset": self._parse_int(match.group("changeset")),
            "branch_state": match.group("head_state").strip(),
        }

    def _parse_int(self, value: str) -> int | None:
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None

    def _relative_path(self, workspace: WorkspaceRecord, target: Path | str) -> str:
        workspace_root = str(workspace.root.resolve(strict=False)).rstrip("\\/")
        target_path = str(Path(target).resolve(strict=False))
        if target_path.lower() == workspace_root.lower():
            return "."

        prefix = workspace_root + "\\"
        if target_path.lower().startswith(prefix.lower()):
            return target_path[len(prefix) :]

        return target_path

    def _top_level_area(self, relative_path: str) -> str:
        if not relative_path or relative_path == ".":
            return "Project"

        parts = PureWindowsPath(relative_path).parts
        if not parts:
            return "Project"

        head = parts[0]
        if head in KNOWN_UNREAL_AREAS:
            return head
        if len(parts) == 1:
            return "Project"
        return "Other"

    def _unreal_file_role(self, relative_path: str, top_level_area: str) -> str:
        if not relative_path or relative_path == ".":
            return "workspace"

        path_obj = PureWindowsPath(relative_path)
        filename = path_obj.name.lower()
        suffix = path_obj.suffix.lower()
        parts_lower = [part.lower() for part in path_obj.parts]

        if filename.endswith(".uproject"):
            return "uproject"
        if filename.endswith(".uplugin"):
            return "uplugin"
        if filename.endswith(".target.cs"):
            return "target_script"
        if filename.endswith(".build.cs"):
            return "build_script"
        if suffix == ".umap":
            return "map"
        if suffix == ".uasset":
            return "asset"
        if top_level_area == "Plugins":
            if "source" in parts_lower:
                return "plugin_code"
            if "config" in parts_lower or suffix == ".ini":
                return "plugin_config"
            if suffix == ".umap":
                return "map"
            if suffix == ".uasset":
                return "asset"
            return "plugin"
        if top_level_area == "Config" or suffix == ".ini":
            return "config"
        if top_level_area == "Source" or suffix in UNREAL_CODE_EXTENSIONS:
            return "code"
        if top_level_area == "Content":
            return "content"
        if top_level_area == "Build":
            return "build"
        if top_level_area == "Devops":
            return "devops"
        if top_level_area == "Docs":
            return "docs"
        return "other"

    def _extract_plugin_name(self, relative_path: str, top_level_area: str) -> str | None:
        if top_level_area != "Plugins":
            return None
        parts = PureWindowsPath(relative_path).parts
        if len(parts) >= 2:
            return parts[1]
        return None

    def _extract_source_module(self, relative_path: str, top_level_area: str) -> str | None:
        parts = PureWindowsPath(relative_path).parts
        if top_level_area == "Source" and len(parts) >= 2:
            return parts[1]
        if (
            top_level_area == "Plugins"
            and len(parts) >= 4
            and parts[0] == "Plugins"
            and parts[2].lower() == "source"
        ):
            return parts[3]
        return None

    def _code_bucket(
        self,
        relative_path: str,
        unreal_file_role: str,
        source_module: str | None,
    ) -> str | None:
        if unreal_file_role in {"build_script", "target_script"}:
            return "build"

        if unreal_file_role not in UNREAL_GAMEPLAY_CODE_ROLES:
            return None

        parts_lower = [part.lower() for part in PureWindowsPath(relative_path).parts]
        module_lower = (source_module or "").lower()

        if any(part in {"thirdparty", "third_party"} for part in parts_lower) or "thirdparty" in module_lower:
            return "third_party"
        if any(part in {"developer"} for part in parts_lower) or module_lower.endswith("developer"):
            return "developer"
        if any(part in {"programs"} for part in parts_lower) or module_lower.endswith("program"):
            return "program"
        if any(part in {"test", "tests"} for part in parts_lower) or module_lower.endswith(("test", "tests")):
            return "test"
        if any(part == "editor" or part.endswith("editor") for part in parts_lower) or module_lower.endswith("editor"):
            return "editor"
        return "gameplay"

    def _entry_sample(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": entry["relative_path"],
            "kind": entry["kind"],
            "role": entry["unreal_file_role"],
            "area": entry["top_level_area"],
            "plugin_name": entry["plugin_name"],
            "source_module": entry["source_module"],
            "code_bucket": entry["code_bucket"],
        }

    def _build_grouped_summaries(
        self,
        entries: list[dict[str, Any]],
        group_key: str,
        sample_limit: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            raw_group = entry.get(group_key)
            if not raw_group:
                continue
            group = str(raw_group)
            grouped.setdefault(group, []).append(entry)

        summaries: list[dict[str, Any]] = []
        for group in sorted(grouped.keys(), key=str.lower):
            group_entries = grouped[group]
            summaries.append(
                {
                    group_key: group,
                    "count": len(group_entries),
                    "by_kind": self._counter_dict([entry["kind"] for entry in group_entries]),
                    "by_role": self._counter_dict([entry["unreal_file_role"] for entry in group_entries]),
                    "sample_entries": [
                        self._entry_sample(entry)
                        for entry in group_entries[: max(1, min(sample_limit, 20))]
                    ],
                }
            )
        return summaries

    def _build_status_entry(
        self,
        workspace: WorkspaceRecord,
        kind_code: str,
        item_path: str,
        is_directory_raw: str,
        merge_status: str,
    ) -> dict[str, Any]:
        target = Path(item_path.strip()).resolve(strict=False)
        relative_path = self._relative_path(workspace, target)
        extension = PureWindowsPath(str(target)).suffix.lower() or None
        top_level_area = self._top_level_area(relative_path)
        unreal_file_role = self._unreal_file_role(relative_path, top_level_area)
        plugin_name = self._extract_plugin_name(relative_path, top_level_area)
        source_module = self._extract_source_module(relative_path, top_level_area)
        code_bucket = self._code_bucket(relative_path, unreal_file_role, source_module)
        is_unreal_asset = extension in UNREAL_ASSET_EXTENSIONS
        return {
            "kind": STATUS_KIND_MAP.get(kind_code, kind_code.lower()),
            "kind_code": kind_code,
            "path": str(target),
            "relative_path": relative_path,
            "top_level_area": top_level_area,
            "extension": extension,
            "unreal_file_role": unreal_file_role,
            "plugin_name": plugin_name,
            "source_module": source_module,
            "code_bucket": code_bucket,
            "is_build_script": unreal_file_role in {"build_script", "target_script"},
            "is_config_file": unreal_file_role in {"config", "plugin_config"},
            "is_plugin_file": top_level_area == "Plugins",
            "is_gameplay_code": code_bucket == "gameplay",
            "is_unreal_asset": is_unreal_asset,
            "is_unreal_map": extension == ".umap",
            "is_directory": is_directory_raw.strip().lower() == "true",
            "merge_status": merge_status.strip() or None,
        }

    def _collect_status_entries(
        self,
        path: str | None = None,
        include_changed: bool = True,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> tuple[WorkspaceRecord, Path, dict[str, Any], list[dict[str, Any]]]:
        workspace, target = self.resolve_item_path(path)
        args = [
            "status",
            str(target),
            "--machinereadable",
            "--fieldseparator=|",
            "--startlineseparator=",
            "--endlineseparator=\n",
            "--noheader",
        ]

        if include_changed:
            args.extend(["--controlledchanged", "--changed"])
        if include_private:
            args.append("--private")
        if include_local_deleted:
            args.append("--localdeleted")
        if include_local_moved:
            args.append("--localmoved")

        raw = self._run_cm(args)
        entries: list[dict[str, Any]] = []
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|", 3)
            while len(parts) < 4:
                parts.append("")
            kind_code, item_path, is_directory_raw, merge_status = parts
            entries.append(
                self._build_status_entry(
                    workspace=workspace,
                    kind_code=kind_code,
                    item_path=item_path,
                    is_directory_raw=is_directory_raw,
                    merge_status=merge_status,
                )
            )

        return workspace, target, self._parse_status_header(workspace), entries

    def _counter_dict(self, values: list[str]) -> dict[str, int]:
        return dict(sorted(Counter(values).items()))

    def _build_area_summaries(
        self,
        entries: list[dict[str, Any]],
        sample_limit: int,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for area in UNREAL_AREA_ORDER:
            area_entries = [entry for entry in entries if entry["top_level_area"] == area]
            if not area_entries:
                continue
            summaries.append(
                {
                    "area": area,
                    "count": len(area_entries),
                    "by_kind": self._counter_dict([entry["kind"] for entry in area_entries]),
                    "sample_entries": [
                        self._entry_sample(entry)
                        for entry in area_entries[: max(1, min(sample_limit, 20))]
                    ],
                }
            )
        return summaries

    def list_workspaces(self) -> dict[str, Any]:
        return {
            "workspaces": [record.to_dict() for record in self.records],
        }

    def workspace_info(self, workspace_path: str | None = None) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace_path)
        info = workspace.to_dict()
        info.update(self._parse_fileinfo(workspace.root))
        info.update(self._parse_status_header(workspace))
        return info

    def status(
        self,
        path: str | None = None,
        limit: int = 200,
        include_changed: bool = True,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(limit, 500))
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=include_changed,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )

        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(entries),
            "truncated": len(entries) > normalized_limit,
            "entries": entries[:normalized_limit],
        }

    def fileinfo(self, path: str | None = None) -> dict[str, Any]:
        workspace, target = self.resolve_item_path(path)
        return {
            "workspace_root": str(workspace.root),
            **self._parse_fileinfo(target),
        }

    def history(self, path: str | None = None, limit: int = 10) -> dict[str, Any]:
        workspace, target = self.resolve_item_path(path)
        normalized_limit = max(1, min(limit, 100))
        raw = self._run_cm(
            [
                "history",
                str(target),
                f"--limit={normalized_limit}",
                f"--format={HISTORY_FORMAT}",
            ]
        )
        entries: list[dict[str, Any]] = []
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            date_text, changeset_text, owner, comment = (line.split("|", 3) + ["", "", "", ""])[:4]
            entries.append(
                {
                    "date": date_text.strip() or None,
                    "changeset_id": self._parse_int(changeset_text),
                    "owner": owner.strip() or None,
                    "comment": comment.strip() or None,
                }
            )

        return {
            "workspace_root": str(workspace.root),
            "path": str(target),
            "entries": entries,
        }

    def main_branch(self, workspace_path: str | None = None) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace_path)
        raw = self._run_cm(
            ["branch", "showmain", f"--format={MAIN_BRANCH_FORMAT}"],
            cwd=workspace.root,
        )
        parts = raw.split("|", 4)
        if len(parts) != 5:
            raise RuntimeError(f"Unexpected main branch format: {raw}")
        return {
            "workspace_root": str(workspace.root),
            "rep_name": parts[0].strip() or None,
            "repository": parts[1].strip() or None,
            "rep_server": parts[2].strip() or None,
            "name": parts[3].strip() or None,
            "changeset": self._parse_int(parts[4]),
        }

    def unreal_change_summary(
        self,
        path: str | None = None,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        sample_limit_per_area: int = 10,
    ) -> dict[str, Any]:
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(sample_limit_per_area, 20))
        asset_entries = [entry for entry in entries if entry["is_unreal_asset"]]
        map_entries = [entry for entry in entries if entry["is_unreal_map"]]
        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(entries),
            "by_kind": self._counter_dict([entry["kind"] for entry in entries]),
            "areas": self._build_area_summaries(entries, normalized_limit),
            "asset_counts": {
                "total_assets": len(asset_entries),
                "maps": len(map_entries),
                "non_map_assets": len(asset_entries) - len(map_entries),
            },
        }

    def unreal_asset_status(
        self,
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        maps_only: bool = False,
    ) -> dict[str, Any]:
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(limit, 500))
        filtered_entries = [
            entry
            for entry in entries
            if entry["is_unreal_asset"] and (not maps_only or entry["is_unreal_map"])
        ]
        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(filtered_entries),
            "truncated": len(filtered_entries) > normalized_limit,
            "by_kind": self._counter_dict([entry["kind"] for entry in filtered_entries]),
            "by_extension": self._counter_dict([entry["extension"] or "" for entry in filtered_entries]),
            "entries": filtered_entries[:normalized_limit],
        }

    def unreal_workspace_summary(
        self,
        workspace_path: str | None = None,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        sample_limit: int = 8,
    ) -> dict[str, Any]:
        workspace = self.resolve_workspace(workspace_path)
        _, _, header, entries = self._collect_status_entries(
            path=str(workspace.root),
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(sample_limit, 20))

        def sample_for(predicate: Any) -> list[dict[str, Any]]:
            matched = [entry for entry in entries if predicate(entry)]
            return [
                {
                    "path": entry["relative_path"],
                    "kind": entry["kind"],
                    "role": entry["unreal_file_role"],
                    "area": entry["top_level_area"],
                }
                for entry in matched[:normalized_limit]
            ]

        return {
            "workspace_root": str(workspace.root),
            **header,
            "total_count": len(entries),
            "counts_by_kind": self._counter_dict([entry["kind"] for entry in entries]),
            "counts_by_area": self._counter_dict([entry["top_level_area"] for entry in entries]),
            "project_files": sample_for(
                lambda entry: entry["unreal_file_role"] in {"uproject", "uplugin", "build_script", "target_script"}
            ),
            "code_files": sample_for(
                lambda entry: entry["unreal_file_role"] in {"code", "plugin_code", "build_script", "target_script"}
            ),
            "gameplay_code_files": sample_for(lambda entry: entry["is_gameplay_code"]),
            "config_files": sample_for(
                lambda entry: entry["unreal_file_role"] in {"config", "plugin_config"}
            ),
            "asset_files": sample_for(lambda entry: entry["is_unreal_asset"] and not entry["is_unreal_map"]),
            "map_files": sample_for(lambda entry: entry["is_unreal_map"]),
            "plugin_files": sample_for(lambda entry: entry["top_level_area"] == "Plugins"),
            "build_script_files": sample_for(lambda entry: entry["is_build_script"]),
            "build_and_devops_files": sample_for(
                lambda entry: entry["top_level_area"] in {"Build", "Devops"}
            ),
            "other_files": sample_for(
                lambda entry: entry["top_level_area"] not in {"Project", "Source", "Config", "Content", "Plugins", "Build", "Devops"}
            ),
        }

    def unreal_plugin_status(
        self,
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        sample_limit_per_plugin: int = 8,
    ) -> dict[str, Any]:
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(limit, 500))
        normalized_sample_limit = max(1, min(sample_limit_per_plugin, 20))
        filtered_entries = [entry for entry in entries if entry["is_plugin_file"]]
        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(filtered_entries),
            "truncated": len(filtered_entries) > normalized_limit,
            "by_kind": self._counter_dict([entry["kind"] for entry in filtered_entries]),
            "by_role": self._counter_dict([entry["unreal_file_role"] for entry in filtered_entries]),
            "plugins": self._build_grouped_summaries(
                filtered_entries,
                group_key="plugin_name",
                sample_limit=normalized_sample_limit,
            ),
            "entries": [self._entry_sample(entry) for entry in filtered_entries[:normalized_limit]],
        }

    def unreal_build_script_status(
        self,
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> dict[str, Any]:
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(limit, 500))
        filtered_entries = [entry for entry in entries if entry["is_build_script"]]
        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(filtered_entries),
            "truncated": len(filtered_entries) > normalized_limit,
            "by_kind": self._counter_dict([entry["kind"] for entry in filtered_entries]),
            "by_role": self._counter_dict([entry["unreal_file_role"] for entry in filtered_entries]),
            "by_module": self._counter_dict(
                [entry["source_module"] for entry in filtered_entries if entry["source_module"]]
            ),
            "entries": [self._entry_sample(entry) for entry in filtered_entries[:normalized_limit]],
        }

    def unreal_config_status(
        self,
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> dict[str, Any]:
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(limit, 500))
        filtered_entries = [entry for entry in entries if entry["is_config_file"]]
        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(filtered_entries),
            "truncated": len(filtered_entries) > normalized_limit,
            "by_kind": self._counter_dict([entry["kind"] for entry in filtered_entries]),
            "by_role": self._counter_dict([entry["unreal_file_role"] for entry in filtered_entries]),
            "by_plugin": self._counter_dict(
                [entry["plugin_name"] for entry in filtered_entries if entry["plugin_name"]]
            ),
            "entries": [self._entry_sample(entry) for entry in filtered_entries[:normalized_limit]],
        }

    def unreal_gameplay_code_status(
        self,
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        include_plugin_code: bool = True,
    ) -> dict[str, Any]:
        workspace, target, header, entries = self._collect_status_entries(
            path=path,
            include_changed=True,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )
        normalized_limit = max(1, min(limit, 500))
        filtered_entries = [
            entry
            for entry in entries
            if entry["is_gameplay_code"]
            and (include_plugin_code or entry["top_level_area"] != "Plugins")
        ]
        return {
            "workspace_root": str(workspace.root),
            "scope_path": str(target),
            **header,
            "total_count": len(filtered_entries),
            "truncated": len(filtered_entries) > normalized_limit,
            "by_kind": self._counter_dict([entry["kind"] for entry in filtered_entries]),
            "by_area": self._counter_dict([entry["top_level_area"] for entry in filtered_entries]),
            "by_module": self._counter_dict(
                [entry["source_module"] for entry in filtered_entries if entry["source_module"]]
            ),
            "by_plugin": self._counter_dict(
                [entry["plugin_name"] for entry in filtered_entries if entry["plugin_name"]]
            ),
            "entries": [self._entry_sample(entry) for entry in filtered_entries[:normalized_limit]],
        }


def build_server(service: UvcsService) -> FastMCP:
    server = FastMCP("uvcs")

    @server.tool(
        name="list_workspaces",
        description="List the allowed UVCS / Plastic SCM workspaces.",
        annotations=READ_ONLY,
    )
    def list_workspaces() -> dict[str, Any]:
        return service.list_workspaces()

    @server.tool(
        name="workspace_info",
        description="Get repository and current branch info for an allowed UVCS workspace. If omitted, uses the only configured workspace.",
        annotations=READ_ONLY,
    )
    def workspace_info(workspace_path: str | None = None) -> dict[str, Any]:
        return service.workspace_info(workspace_path=workspace_path)

    @server.tool(
        name="status",
        description="Inspect UVCS status for a workspace, directory, or file inside an allowed workspace.",
        annotations=READ_ONLY,
    )
    def status(
        path: str | None = None,
        limit: int = 200,
        include_changed: bool = True,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> dict[str, Any]:
        return service.status(
            path=path,
            limit=limit,
            include_changed=include_changed,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )

    @server.tool(
        name="fileinfo",
        description="Get UVCS file metadata for a file, directory, or workspace root inside an allowed workspace.",
        annotations=READ_ONLY,
    )
    def fileinfo(path: str | None = None) -> dict[str, Any]:
        return service.fileinfo(path=path)

    @server.tool(
        name="history",
        description="Get recent UVCS history for a file, directory, or workspace root inside an allowed workspace.",
        annotations=READ_ONLY,
    )
    def history(path: str | None = None, limit: int = 10) -> dict[str, Any]:
        return service.history(path=path, limit=limit)

    @server.tool(
        name="main_branch",
        description="Get the main branch identity for an allowed UVCS workspace.",
        annotations=READ_ONLY,
    )
    def main_branch(workspace_path: str | None = None) -> dict[str, Any]:
        return service.main_branch(workspace_path=workspace_path)

    @server.tool(
        name="unreal_change_summary",
        description="Summarize changed UVCS entries for an Unreal Engine workspace by top-level areas such as Source, Config, Content, and Plugins.",
        annotations=READ_ONLY,
    )
    def unreal_change_summary(
        path: str | None = None,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        sample_limit_per_area: int = 10,
    ) -> dict[str, Any]:
        return service.unreal_change_summary(
            path=path,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
            sample_limit_per_area=sample_limit_per_area,
        )

    @server.tool(
        name="unreal_asset_status",
        description="List only changed Unreal asset files such as .uasset and .umap inside an allowed UVCS workspace.",
        annotations=READ_ONLY,
    )
    def unreal_asset_status(
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        maps_only: bool = False,
    ) -> dict[str, Any]:
        return service.unreal_asset_status(
            path=path,
            limit=limit,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
            maps_only=maps_only,
        )

    @server.tool(
        name="unreal_workspace_summary",
        description="Provide a developer-focused Unreal Engine workspace summary covering project files, code, config, assets, plugins, and build-related files.",
        annotations=READ_ONLY,
    )
    def unreal_workspace_summary(
        workspace_path: str | None = None,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        sample_limit: int = 8,
    ) -> dict[str, Any]:
        return service.unreal_workspace_summary(
            workspace_path=workspace_path,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
            sample_limit=sample_limit,
        )

    @server.tool(
        name="unreal_plugin_status",
        description="List only changed Unreal plugin-related files and summarize them by plugin.",
        annotations=READ_ONLY,
    )
    def unreal_plugin_status(
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        sample_limit_per_plugin: int = 8,
    ) -> dict[str, Any]:
        return service.unreal_plugin_status(
            path=path,
            limit=limit,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
            sample_limit_per_plugin=sample_limit_per_plugin,
        )

    @server.tool(
        name="unreal_build_script_status",
        description="List only changed Unreal Build.cs and Target.cs files.",
        annotations=READ_ONLY,
    )
    def unreal_build_script_status(
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> dict[str, Any]:
        return service.unreal_build_script_status(
            path=path,
            limit=limit,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )

    @server.tool(
        name="unreal_config_status",
        description="List only changed Unreal config files such as Config/*.ini and plugin config files.",
        annotations=READ_ONLY,
    )
    def unreal_config_status(
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
    ) -> dict[str, Any]:
        return service.unreal_config_status(
            path=path,
            limit=limit,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
        )

    @server.tool(
        name="unreal_gameplay_code_status",
        description="List only changed gameplay-oriented Unreal C++ code, excluding common Editor, Tests, Developer, Programs, and ThirdParty code paths.",
        annotations=READ_ONLY,
    )
    def unreal_gameplay_code_status(
        path: str | None = None,
        limit: int = 200,
        include_private: bool = True,
        include_local_deleted: bool = True,
        include_local_moved: bool = True,
        include_plugin_code: bool = True,
    ) -> dict[str, Any]:
        return service.unreal_gameplay_code_status(
            path=path,
            limit=limit,
            include_private=include_private,
            include_local_deleted=include_local_deleted,
            include_local_moved=include_local_moved,
            include_plugin_code=include_plugin_code,
        )

    return server


def main() -> int:
    args = parse_args()
    service = UvcsService(args.workspace_root)
    server = build_server(service)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
