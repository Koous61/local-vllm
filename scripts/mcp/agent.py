from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from local_mcp_runtime import (
    DEFAULT_SYSTEM_PROMPT,
    RegisteredTool,
    build_openai_tools,
    connect_servers,
    extract_exact_result,
    filter_registry_for_mode,
    get_api_key,
    get_enabled_server_names,
    get_project_root,
    load_config,
    optimize_registry_for_prompt,
    parse_json_values,
    resolve_model,
    run_single_prompt,
    serialize_tool_result,
    truncate_text,
)


JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)
STATUS_LINE_PATTERN = re.compile(r"(?im)^status\s*:\s*(continue|finish)\s*$")
SUMMARY_LINE_PATTERN = re.compile(r"(?im)^step[_ -]?summary\s*:\s*(.+)$")
NEXT_FOCUS_LINE_PATTERN = re.compile(r"(?im)^next[_ -]?(?:focus|step|action)\s*:\s*(.+)$")
USER_RESPONSE_PATTERN = re.compile(r"(?ims)^user[_ -]?response\s*:\s*(.+)$")


@dataclass(frozen=True)
class AgentProfile:
    name: str
    description: str
    preferred_servers: tuple[str, ...]
    extra_rules: tuple[str, ...]


@dataclass
class AgentDecision:
    status: str
    step_summary: str
    user_response: str
    next_focus: str | None
    raw_response: str


AGENT_PROFILES: dict[str, AgentProfile] = {
    "coder": AgentProfile(
        name="coder",
        description="Code-oriented terminal agent for local repositories and files.",
        preferred_servers=("filesystem", "git"),
        extra_rules=(
            "Prefer filesystem and git tools for codebase facts.",
            "Keep outputs developer-friendly and implementation-oriented.",
            "For repository entrypoints or startup questions, inspect the repo root and README before inventing wildcard patterns.",
        ),
    ),
    "repo": AgentProfile(
        name="repo",
        description="Repository analysis agent for branches, history, diffs, and summaries.",
        preferred_servers=("git", "filesystem"),
        extra_rules=(
            "Favor focused git queries over broad repository scans.",
            "Summarize branch, status, history, and diff findings clearly.",
            "For most repository questions, start with git__status_summary instead of the heavier raw repository status payload.",
        ),
    ),
    "ops": AgentProfile(
        name="ops",
        description="Docker and local stack inspection agent for containers, compose services, and logs.",
        preferred_servers=("docker", "git", "filesystem"),
        extra_rules=(
            "For stack status questions, start with docker__compose_status_summary.",
            "For service issues, fetch a compact status view before reading logs.",
            "Prefer compose-scoped tools over global Docker tools unless the user asks for a machine-wide view.",
        ),
    ),
    "node": AgentProfile(
        name="node",
        description="Node.js build agent for package.json inspection, scripts, installs, and project builds.",
        preferred_servers=("node", "git", "filesystem", "docker"),
        extra_rules=(
            "Start with node__project_summary or node__list_scripts before running build-like tools.",
            "Use node__build_project for build goals and node__run_script only when the task explicitly needs a different script.",
            "If write-like tools are blocked, explain that the run needs --allow-writes.",
        ),
    ),
    "unreal": AgentProfile(
        name="unreal",
        description="Unreal Engine workspace agent for UVCS and source tree inspection.",
        preferred_servers=("uvcs", "filesystem"),
        extra_rules=(
            "Prefer UVCS Unreal-specific tools when they match the task.",
            "Group findings by Source, Config, Content, Plugins, and project files when helpful.",
        ),
    ),
    "research": AgentProfile(
        name="research",
        description="Local research agent for browser-driven fact gathering and note synthesis.",
        preferred_servers=("playwright", "filesystem", "git"),
        extra_rules=(
            "Prefer Playwright for live page facts and filesystem for local note materialization.",
            "Keep source-backed conclusions explicit and avoid guessing page contents.",
        ),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal agent on top of the local vLLM OpenAI-compatible API and MCP tools."
    )
    parser.add_argument(
        "--config",
        default="mcp-servers.json",
        help="Path to the MCP servers config file. Default: %(default)s",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000/v1",
        help="OpenAI-compatible base URL. Default: %(default)s",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key for the OpenAI-compatible endpoint. Defaults to API_KEY in .env.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Served model name. Defaults to the first model from /models.",
    )
    parser.add_argument(
        "--server",
        action="append",
        default=[],
        help="Server name from the config to enable. Repeat to enable multiple servers.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(AGENT_PROFILES.keys()),
        default="coder",
        help="Agent profile that tunes instructions and default server selection.",
    )
    parser.add_argument(
        "--goal",
        default=None,
        help="Agent goal to execute.",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Resume a previous session by id or by path to session.json.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=6,
        help="Maximum agent steps before stopping. Default: %(default)s",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=8,
        help="Maximum tool-calling rounds inside one agent step. Default: %(default)s",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allow write-like MCP tools. Default behavior is read-only.",
    )
    parser.add_argument(
        "--show-server-logs",
        action="store_true",
        help="Show stderr output from stdio MCP servers.",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Optional extra system prompt text appended after the built-in agent instructions.",
    )
    parser.add_argument(
        "goal_parts",
        nargs="*",
        help="Optional goal text. If set, it is joined into one goal.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(value: str, limit: int = 32) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        slug = "task"
    return slug[:limit].rstrip("-")


def get_sessions_root() -> Path:
    return get_project_root() / "data" / "agent-sessions"


def build_session_id(goal: str, profile_name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{profile_name}-{slugify(goal)}"


def resolve_session_file(resume_value: str) -> Path:
    candidate = Path(resume_value).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        return candidate / "session.json"
    return get_sessions_root() / resume_value / "session.json"


def save_session(session: dict[str, Any]) -> Path:
    session_id = str(session["session_id"])
    session_dir = get_sessions_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / "session.json"
    session_file.write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session_file


def load_session(resume_value: str) -> dict[str, Any]:
    session_file = resolve_session_file(resume_value)
    if not session_file.exists():
        raise FileNotFoundError(f"Agent session was not found: {session_file}")
    data = json.loads(session_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Agent session has invalid JSON: {session_file}")
    return data


def get_goal(args: argparse.Namespace, resumed_session: dict[str, Any] | None) -> str:
    if args.goal:
        return args.goal.strip()
    if args.goal_parts:
        return " ".join(args.goal_parts).strip()
    if resumed_session is not None:
        return str(resumed_session["goal"])
    raise ValueError("Provide an agent goal through --goal or positional text, or use --resume.")


def build_agent_system_prompt(
    profile: AgentProfile,
    *,
    allow_writes: bool,
    available_tool_names: list[str],
    blocked_tools: list[str],
    runtime_notes: list[str],
    extra_prompt: str | None,
) -> str:
    lines = [
        DEFAULT_SYSTEM_PROMPT,
        "",
        "You are operating as a terminal agent, not as a casual chat assistant.",
        f"Agent profile: {profile.name} - {profile.description}",
        "Work in short iterative steps.",
        "Use MCP tools whenever they reduce uncertainty.",
        "Prefer compact, focused tool calls over broad scans.",
        "Never invent repository state, file contents, or browser results.",
        "If the configured tools clearly point to one repository or workspace, use it directly instead of asking the operator to choose.",
        "When the task asks for an exact path, file, command, or entrypoint, verify that exact artifact with a tool before finishing.",
        "After each step, return exactly one JSON object and nothing else.",
        'The JSON object must contain: {"status":"continue|finish","step_summary":"...","user_response":"...","next_focus":"..."}',
        'Set "status" to "finish" only when the goal is solved or clearly blocked.',
        'When "status" is "continue", include a concrete "next_focus".',
        'When "status" is "finish", "next_focus" may be null or omitted.',
    ]
    if allow_writes:
        lines.append("Write-like tools are allowed, but use them only when clearly necessary.")
    else:
        lines.append(
            "Write-like tools are disabled in this run. Do not call write, edit, create, delete, rename, commit, push, or similar tools."
        )
    if available_tool_names:
        lines.append(f"Available tools: {', '.join(available_tool_names)}")
    if blocked_tools:
        lines.append(f"Blocked tools in this run: {', '.join(blocked_tools)}")
    if runtime_notes:
        lines.append("Runtime notes:")
        for note in runtime_notes:
            lines.append(f"- {note}")
    if profile.extra_rules:
        lines.append("Profile rules:")
        for rule in profile.extra_rules:
            lines.append(f"- {rule}")
    if extra_prompt:
        lines.extend(["", "Additional operator instruction:", extra_prompt.strip()])
    return "\n".join(lines)


def choose_servers(
    config: dict[str, Any],
    profile: AgentProfile,
    explicit_servers: list[str],
    resumed_session: dict[str, Any] | None,
) -> tuple[list[str], str]:
    if explicit_servers:
        return explicit_servers, "explicit"

    if resumed_session is not None:
        resumed_servers = [
            str(name) for name in resumed_session.get("selected_servers", []) if str(name).strip()
        ]
        if resumed_servers:
            return resumed_servers, "session"

    enabled_servers = get_enabled_server_names(config)
    preferred_enabled = [name for name in profile.preferred_servers if name in enabled_servers]
    if preferred_enabled:
        return [preferred_enabled[0]], "profile"

    return enabled_servers, "enabled"


def build_goal_hints(goal: str) -> list[str]:
    lowered = goal.lower()
    hints: list[str] = []
    if any(keyword in lowered for keyword in ("entrypoint", "startup", "start ", "how to start", "run ", "launch")):
        hints.append("Inspect the repository root, README, and top-level *.cmd or *.ps1 files before broad searches.")
        hints.append("If you find a candidate file whose name already matches the request, inspect that exact file before finishing.")
    if any(keyword in lowered for keyword in ("status", "branch", "diff", "history", "commit", "repo")):
        hints.append("Prefer focused git status, log, diff, or branch tools over generic filesystem browsing when git tools are available.")
    if any(keyword in lowered for keyword in ("working tree", "clean", "current branch", "branch and", "branch ")) or (
        "status" in lowered and "git" in lowered
    ):
        hints.append("For current branch and cleanliness questions, start with git__status_summary.")
    if any(keyword in lowered for keyword in ("docker", "compose", "container", "service status", "stack status", "healthy", "unhealthy")):
        hints.append("For Docker or compose service state questions, start with docker__compose_status_summary.")
    if any(keyword in lowered for keyword in ("docker logs", "compose logs", "container logs", "service logs", "logs for")):
        hints.append("For Docker log questions, use docker__compose_logs for compose services or docker__container_inspect after identifying the container.")
    if any(
        keyword in lowered
        for keyword in (
            "package.json",
            "node",
            "npm",
            "pnpm",
            "yarn",
            "frontend",
            "backend",
            "react",
        )
    ):
        hints.append("For Node.js questions, start with node__project_summary or node__list_scripts before broader filesystem browsing.")
    if any(keyword in lowered for keyword in (" build", "build ", "compile", "bundle", "production build")):
        hints.append("For Node.js build tasks, prefer node__build_project and use --allow-writes when build tools are blocked.")
    if any(keyword in lowered for keyword in ("install dependencies", "npm install", "pnpm install", "yarn install", "node_modules")):
        hints.append("For dependency setup, prefer node__install_dependencies and keep the target project explicit when multiple Node projects are configured.")
    if any(keyword in lowered for keyword in ("unreal", "plugin", "uasset", "umap", "build.cs", "target.cs", "config")):
        hints.append("Prefer Unreal-specific UVCS tools when they match the request.")
    if any(keyword in lowered for keyword in ("browser", "page", "website", "url", "open ")) or "http" in lowered:
        hints.append("Prefer Playwright for live page facts instead of guessing page contents.")
    return hints


def build_step_prompt(
    *,
    goal: str,
    profile: AgentProfile,
    selected_servers: list[str],
    step_number: int,
    max_steps: int,
    step_summaries: list[str],
    allow_writes: bool,
) -> str:
    summaries_text = "\n".join(f"- {summary}" for summary in step_summaries[-6:])
    if not summaries_text:
        summaries_text = "- none yet"

    lines = [
        "Agent task context:",
        f"Goal: {goal}",
        f"Profile: {profile.name}",
        f"Current step: {step_number} of {max_steps}",
        f"Active MCP servers: {', '.join(selected_servers) if selected_servers else 'none'}",
        f"Write mode: {'enabled' if allow_writes else 'disabled'}",
        "Completed step summaries:",
        summaries_text,
        "",
        "Instructions for this step:",
        "- Move the task forward materially.",
        "- Prefer the smallest useful tool call.",
        "- If you already have enough information, do not call extra tools.",
        "- If the task asks for an exact file, path, command, or entrypoint, verify it directly before finishing.",
        "- If the goal is complete or blocked, return status='finish'.",
        "- Otherwise return status='continue' with a concrete next_focus.",
        "- Return JSON only.",
    ]
    goal_hints = build_goal_hints(goal)
    if goal_hints:
        lines.append("- Task-specific hints:")
        for hint in goal_hints:
            lines.append(f"  - {hint}")
    if step_number >= max_steps:
        lines.append("- This is the last budgeted step. Finish with the best complete answer you can.")
    return "\n".join(lines)


def extract_agent_json_candidates(raw_response: str) -> list[str]:
    candidates: list[str] = []
    stripped = raw_response.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(JSON_BLOCK_PATTERN.findall(raw_response))
    first_brace = raw_response.find("{")
    last_brace = raw_response.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidates.append(raw_response[first_brace : last_brace + 1])
    return [candidate.strip() for candidate in candidates if candidate.strip()]


def is_exact_artifact_goal(goal: str) -> bool:
    lowered = goal.lower()
    artifact_keywords = ("entrypoint", "startup", "how to start", "command", "path", "exact file")
    return any(keyword in lowered for keyword in artifact_keywords)


def step_verified_startup_artifact(step_messages: list[dict[str, Any]]) -> bool:
    direct_read_tools = {"filesystem__read_text_file", "filesystem__get_file_info"}
    for message in step_messages:
        tool_calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            function_data = tool_call.get("function", {})
            tool_name = str(function_data.get("name", ""))
            if tool_name not in direct_read_tools:
                continue
            arguments_raw = function_data.get("arguments") or "{}"
            try:
                arguments = json.loads(arguments_raw)
            except json.JSONDecodeError:
                continue
            path = str(arguments.get("path", "")).lower()
            if path.endswith((".cmd", ".ps1", ".bat", ".sh")):
                return True
    return False


def validate_finish_decision(
    *,
    goal: str,
    decision: AgentDecision,
    step_messages: list[dict[str, Any]],
) -> str | None:
    if decision.status != "finish":
        return None
    if is_exact_artifact_goal(goal) and not step_verified_startup_artifact(step_messages):
        return (
            "Before finishing, verify the exact startup file or command directly with a tool "
            "such as reading or inspecting the matching .cmd/.ps1 file instead of relying only on summaries or README text."
        )
    return None


def normalize_decision_payload(payload: dict[str, Any], raw_response: str) -> AgentDecision:
    status = str(payload.get("status", "")).strip().lower()
    if not status and "done" in payload:
        status = "finish" if bool(payload.get("done")) else "continue"
    if status not in {"continue", "finish"}:
        raise ValueError("Agent response JSON does not include a valid status.")

    summary_keys = ("step_summary", "summary", "stepSummary")
    response_keys = ("user_response", "response", "final_response", "message", "answer")
    next_keys = ("next_focus", "next_step", "next_action", "next_prompt", "next")

    step_summary = ""
    for key in summary_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            step_summary = value.strip()
            break
    if not step_summary:
        step_summary = truncate_text(raw_response, 200)

    user_response = ""
    for key in response_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            user_response = value.strip()
            break
    if not user_response:
        user_response = step_summary

    next_focus: str | None = None
    for key in next_keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            next_focus = value.strip()
            break
    if status == "continue" and not next_focus:
        next_focus = "Continue the task with the next most informative action."

    return AgentDecision(
        status=status,
        step_summary=step_summary,
        user_response=user_response,
        next_focus=next_focus,
        raw_response=raw_response,
    )


def parse_agent_decision(raw_response: str) -> AgentDecision:
    for candidate in extract_agent_json_candidates(raw_response):
        for parsed in parse_json_values(candidate):
            if isinstance(parsed, dict):
                return normalize_decision_payload(parsed, raw_response)

    status_match = STATUS_LINE_PATTERN.search(raw_response)
    if status_match:
        summary_match = SUMMARY_LINE_PATTERN.search(raw_response)
        response_match = USER_RESPONSE_PATTERN.search(raw_response)
        next_match = NEXT_FOCUS_LINE_PATTERN.search(raw_response)
        return AgentDecision(
            status=status_match.group(1).lower(),
            step_summary=summary_match.group(1).strip() if summary_match else truncate_text(raw_response, 200),
            user_response=response_match.group(1).strip() if response_match else raw_response.strip(),
            next_focus=next_match.group(1).strip() if next_match else None,
            raw_response=raw_response,
        )

    raise ValueError("Agent response could not be parsed as the expected JSON decision.")


async def repair_agent_decision(
    *,
    client: AsyncOpenAI,
    model: str,
    messages: list[dict[str, Any]],
    raw_response: str,
) -> AgentDecision:
    repair_prompt = "\n".join(
        [
            "Your previous reply was not valid agent JSON.",
            "Re-express the same result as exactly one JSON object with the keys status, step_summary, user_response, and next_focus.",
            'Use status="finish" or status="continue".',
            "Do not call any tools.",
            "Do not add markdown fences or commentary.",
            "",
            "Previous reply:",
            raw_response,
        ]
    )
    repaired = await run_single_prompt(
        client=client,
        model=model,
        prompt=repair_prompt,
        messages=messages,
        openai_tools=[],
        registry={},
        max_tool_rounds=1,
        temperature=0,
    )
    return parse_agent_decision(repaired)


def create_new_session(
    *,
    goal: str,
    profile: AgentProfile,
    selected_servers: list[str],
    server_strategy: str,
    model_name: str,
    allow_writes: bool,
    blocked_tools: list[str],
    max_steps: int,
    max_tool_rounds: int,
    system_prompt: str,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "session_id": build_session_id(goal, profile.name),
        "created_at": now,
        "updated_at": now,
        "status": "running",
        "goal": goal,
        "profile": profile.name,
        "selected_servers": selected_servers,
        "server_strategy": server_strategy,
        "model": model_name,
        "allow_writes": allow_writes,
        "blocked_tools": blocked_tools,
        "max_steps": max_steps,
        "max_tool_rounds": max_tool_rounds,
        "messages": [{"role": "system", "content": system_prompt}],
        "steps": [],
        "final_response": None,
        "error": None,
    }


def format_blocked_tools(blocked_tools: list[str], limit: int = 8) -> str:
    if not blocked_tools:
        return "none"
    if len(blocked_tools) <= limit:
        return ", ".join(blocked_tools)
    visible = ", ".join(blocked_tools[:limit])
    return f"{visible}, ... (+{len(blocked_tools) - limit} more)"


async def collect_runtime_notes(registry: dict[str, RegisteredTool]) -> list[str]:
    probes: tuple[tuple[str, dict[str, Any], str], ...] = (
        ("filesystem__list_allowed_directories", {}, "Filesystem allowed directories"),
        ("git__list_repositories", {}, "Git repositories"),
        ("docker__list_projects", {}, "Docker compose projects"),
        ("node__list_projects", {}, "Node projects"),
        ("uvcs__list_workspaces", {}, "UVCS workspaces"),
    )
    notes: list[str] = []
    for public_name, arguments, label in probes:
        registered = registry.get(public_name)
        if not registered:
            continue
        try:
            result = await registered.session.call_tool(registered.original_name, arguments)
        except Exception as exc:
            notes.append(f"{label}: unavailable ({exc})")
            continue

        text = serialize_tool_result(result).strip()
        if public_name == "git__list_repositories":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("repositories"), list) and len(parsed["repositories"]) == 1:
                repo = parsed["repositories"][0]
                notes.append(
                    f"One Git repository is configured: {repo.get('name')} ({repo.get('root')}). Use it by default."
                )
                continue
        if public_name == "docker__list_projects":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("projects"), list) and len(parsed["projects"]) == 1:
                project = parsed["projects"][0]
                notes.append(
                    f"One Docker Compose project is configured: {project.get('name')} ({project.get('root')}). Use it by default."
                )
                continue
        if public_name == "node__list_projects":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("projects"), list) and len(parsed["projects"]) == 1:
                project = parsed["projects"][0]
                notes.append(
                    f"One Node project is configured: {project.get('name')} ({project.get('root')}). Use it by default."
                )
                continue
        exact = extract_exact_result(text)
        payload = exact or text
        payload = truncate_text(payload.replace("\r", " ").replace("\n", " | "), 320)
        notes.append(f"{label}: {payload}")
    return notes


def print_agent_banner(session: dict[str, Any]) -> None:
    print(f"Session: {session['session_id']}")
    print(f"Model: {session['model']}")
    print(f"Profile: {session['profile']}")
    print(f"Goal: {session['goal']}")
    print(f"Servers: {', '.join(session['selected_servers'])}")
    print(f"Writes: {'enabled' if session['allow_writes'] else 'disabled'}")
    if not session["allow_writes"]:
        print(f"Blocked write-like tools: {format_blocked_tools(list(session.get('blocked_tools', [])))}")
    print("")


async def run_agent(
    *,
    client: AsyncOpenAI,
    model: str,
    session: dict[str, Any],
    registry: dict[str, RegisteredTool],
    profile: AgentProfile,
) -> str:
    messages = session["messages"]
    start_step = len(session.get("steps", [])) + 1
    max_steps = int(session["max_steps"])
    max_tool_rounds = int(session["max_tool_rounds"])
    goal = str(session["goal"])
    selected_servers = [str(name) for name in session["selected_servers"]]
    allow_writes = bool(session["allow_writes"])

    for step_number in range(start_step, max_steps + 1):
        step_summaries = [str(step["decision"]["step_summary"]) for step in session["steps"] if step.get("decision")]
        step_prompt = build_step_prompt(
            goal=goal,
            profile=profile,
            selected_servers=selected_servers,
            step_number=step_number,
            max_steps=max_steps,
            step_summaries=step_summaries,
            allow_writes=allow_writes,
        )
        print(f"[step {step_number}/{max_steps}] thinking...")
        message_count_before = len(messages)
        step_registry = optimize_registry_for_prompt(registry, step_prompt)
        step_openai_tools = build_openai_tools(step_registry)
        raw_response = await run_single_prompt(
            client=client,
            model=model,
            prompt=step_prompt,
            messages=messages,
            openai_tools=step_openai_tools,
            registry=step_registry,
            max_tool_rounds=max_tool_rounds,
            temperature=0.1,
        )
        try:
            decision = parse_agent_decision(raw_response)
        except ValueError:
            decision = await repair_agent_decision(
                client=client,
                model=model,
                messages=messages,
                raw_response=raw_response,
            )
        step_messages = messages[message_count_before:]
        finish_issue = validate_finish_decision(
            goal=goal,
            decision=decision,
            step_messages=step_messages,
        )
        if finish_issue:
            decision = AgentDecision(
                status="continue",
                step_summary=f"{decision.step_summary} Additional verification is still required.",
                user_response=decision.user_response,
                next_focus=finish_issue,
                raw_response=decision.raw_response,
            )

        step_record = {
            "index": step_number,
            "created_at": utc_now(),
            "prompt": step_prompt,
            "decision": asdict(decision),
        }
        session["steps"].append(step_record)
        session["updated_at"] = utc_now()
        save_session(session)

        print(f"[step {step_number}/{max_steps}] {decision.step_summary}")
        print(f"[agent] {decision.user_response}")
        if decision.status == "finish":
            session["status"] = "completed"
            session["updated_at"] = utc_now()
            session["final_response"] = decision.user_response
            save_session(session)
            return decision.user_response

        if decision.next_focus:
            print(f"[next] {decision.next_focus}")
        print("")

    tail_summaries = [
        str(step["decision"]["step_summary"])
        for step in session["steps"][-3:]
        if step.get("decision")
    ]
    fallback_response = "\n".join(
        [
            "Agent reached the configured step limit before finishing cleanly.",
            "Recent progress:",
            *[f"- {summary}" for summary in tail_summaries],
        ]
    )
    session["status"] = "completed"
    session["updated_at"] = utc_now()
    session["final_response"] = fallback_response
    save_session(session)
    return fallback_response


async def async_main() -> int:
    args = parse_args()
    resumed_session = load_session(args.resume) if args.resume else None

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = get_project_root() / config_path
    config = load_config(config_path)

    goal = get_goal(args, resumed_session)
    profile = AGENT_PROFILES[resumed_session["profile"]] if resumed_session is not None else AGENT_PROFILES[args.profile]
    api_key = get_api_key(args.api_key)
    client = AsyncOpenAI(base_url=args.base_url, api_key=api_key)

    selected_servers, server_strategy = choose_servers(
        config=config,
        profile=profile,
        explicit_servers=args.server,
        resumed_session=resumed_session,
    )

    async with AsyncExitStack() as exit_stack:
        registry = await connect_servers(
            exit_stack,
            config,
            selected_servers,
            args.show_server_logs,
        )
        filtered_registry, blocked_tools = filter_registry_for_mode(
            registry,
            allow_writes=args.allow_writes if resumed_session is None else bool(resumed_session["allow_writes"]),
        )
        runtime_notes = await collect_runtime_notes(filtered_registry)
        model = await resolve_model(client, args.model if args.model else (resumed_session or {}).get("model"))

        if resumed_session is not None:
            session = resumed_session
            if session.get("status") == "completed":
                final_response = str(session.get("final_response") or "Session already completed.")
                print(f"Session: {session['session_id']}")
                print(final_response)
                return 0
            session["updated_at"] = utc_now()
            session["selected_servers"] = selected_servers
            session["server_strategy"] = server_strategy
            session["model"] = model
            session["blocked_tools"] = blocked_tools
        else:
            system_prompt = build_agent_system_prompt(
                profile,
                allow_writes=args.allow_writes,
                available_tool_names=sorted(filtered_registry.keys()),
                blocked_tools=blocked_tools,
                runtime_notes=runtime_notes,
                extra_prompt=args.system_prompt,
            )
            session = create_new_session(
                goal=goal,
                profile=profile,
                selected_servers=selected_servers,
                server_strategy=server_strategy,
                model_name=model,
                allow_writes=args.allow_writes,
                blocked_tools=blocked_tools,
                max_steps=args.max_steps,
                max_tool_rounds=args.max_tool_rounds,
                system_prompt=system_prompt,
            )
        session_file = save_session(session)
        print_agent_banner(session)
        print(f"Session file: {session_file}")
        print("")

        final_response = await run_agent(
            client=client,
            model=model,
            session=session,
            registry=filtered_registry,
            profile=profile,
        )
        print("Final result:")
        print(final_response)
        return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
