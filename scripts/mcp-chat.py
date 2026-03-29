from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from openai import AsyncOpenAI


DEFAULT_SYSTEM_PROMPT = """You are a local coding assistant connected to MCP tools.
Use tools when they can provide a more reliable answer than guessing.
Tool names are prefixed with the MCP server name, for example filesystem__read_file.
When you decide to call a tool, do not describe the plan first. Emit only the tool call.
When you use filesystem tools, prefer absolute Windows paths.
After tool results arrive, answer clearly and concisely."""

TOOL_BLOCK_PATTERNS = (
    re.compile(r"<tools>\s*(.*?)\s*</tools>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE),
)
CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(r"<(?:exact-)?result-of-([a-zA-Z0-9._-]+)>", re.IGNORECASE)
LAST_RESULT_PATTERN = re.compile(
    r"<result(?:_|-)of(?:_|-)(?:previous|last)(?:(?:_|-)(?:code|tool|step|call|result))?>",
    re.IGNORECASE,
)
RESULT_SECTION_PATTERN = re.compile(r"### Result\s*(.*?)(?=\n### |\Z)", re.DOTALL)
EMPTY_ASSIGNMENT_PATTERN = re.compile(r"(?m)^[A-Za-z0-9_. \\/()-]{2,}\s*(=|:)\s*$")
ANGLE_INSTRUCTION_PATTERN = re.compile(r"<[^>\n]*\s+[^>\n]*>")
MAX_TOOL_MEMORY_ITEMS = 5


@dataclass
class RegisteredTool:
    public_name: str
    server_name: str
    original_name: str
    description: str
    input_schema: dict[str, Any]
    session: ClientSession


@dataclass
class ToolMemory:
    tool_name: str
    call_id: str
    text: str
    exact_result: str | None


@dataclass
class ValidationIssue:
    message: str


@dataclass
class ToolExecution:
    call: dict[str, Any]
    message_content: str
    memory: ToolMemory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal MCP client that uses the local vLLM OpenAI-compatible API."
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
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="Custom system prompt for the chat session.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=8,
        help="Maximum tool-calling rounds per user message. Default: %(default)s",
    )
    parser.add_argument(
        "--once",
        default=None,
        help="Run one prompt and exit.",
    )
    parser.add_argument(
        "--show-server-logs",
        action="store_true",
        help="Show stderr output from stdio MCP servers.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Optional prompt text. If set, the client runs once and exits.",
    )
    return parser.parse_args()


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"MCP config file not found: {path}. Run .\\setup-mcp.cmd first."
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"{path} does not contain a non-empty 'mcpServers' object.")

    return data


def stringify_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif hasattr(item, "type") and getattr(item, "type", None) == "text":
                parts.append(str(getattr(item, "text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(part for part in parts if part)
    return str(content)


def truncate_text(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def stringify_scalar(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def extract_exact_result(text: str) -> str | None:
    match = RESULT_SECTION_PATTERN.search(text)
    if match:
        candidate = match.group(1).strip()
        if candidate:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                return candidate

            if isinstance(parsed, (str, int, float, bool)) or parsed is None:
                return stringify_scalar(parsed)

            return json.dumps(parsed, ensure_ascii=False)

    stripped = text.strip()
    if stripped and "### " not in stripped and "\n" not in stripped:
        return stripped

    return None


def build_tool_message_content(tool_name: str, text: str, exact_result: str | None) -> str:
    parts = [f"[tool={tool_name}]"]
    if exact_result is not None:
        parts.extend(
            [
                "[exact_result]",
                exact_result,
            ]
        )
    parts.extend(
        [
            "[full_result]",
            text,
        ]
    )
    return "\n".join(parts)


def summarize_memories(memories: list[ToolMemory]) -> str | None:
    if not memories:
        return None

    lines = [
        "Tool result memory. Reuse the exact values below in future tool calls and final answers.",
        "Do not write placeholders like <result-of-tool-name>.",
    ]
    for memory in memories[-MAX_TOOL_MEMORY_ITEMS:]:
        if memory.exact_result is not None:
            value = truncate_text(memory.exact_result)
            lines.append(f"- {memory.tool_name}: exact_result={value}")
        else:
            lines.append(f"- {memory.tool_name}: summary={truncate_text(memory.text)}")
    return "\n".join(lines)


def inject_runtime_messages(
    messages: list[dict[str, Any]],
    memories: list[ToolMemory],
    repair_note: str | None,
) -> list[dict[str, Any]]:
    runtime_messages = list(messages)
    injected: list[dict[str, Any]] = []

    if runtime_messages and runtime_messages[0].get("role") == "system":
        injected.append(runtime_messages[0])
        runtime_messages = runtime_messages[1:]

    memory_note = summarize_memories(memories)
    if memory_note:
        injected.append({"role": "system", "content": memory_note})
    if repair_note:
        injected.append({"role": "system", "content": repair_note})

    injected.extend(runtime_messages)
    return injected


def get_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key

    env_values = load_dotenv(get_project_root() / ".env")
    return env_values.get("API_KEY", "local-vllm-key")


def cleanup_tool_json(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 2:
            value = "\n".join(lines[1:-1]).strip()
    return value


def parse_tool_calls_from_content(content: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    candidates: list[str] = []

    for pattern in TOOL_BLOCK_PATTERNS:
        candidates.extend(pattern.findall(content))

    candidates.extend(CODE_BLOCK_PATTERN.findall(content))

    stripped_content = content.strip()
    if stripped_content.startswith("{") or stripped_content.startswith("["):
        candidates.append(stripped_content)

    for payload_raw in candidates:
        payload = cleanup_tool_json(payload_raw)
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue

        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            arguments = item.get("arguments", {})
            if not isinstance(name, str):
                continue
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append(
                {
                    "id": f"fallback-tool-call-{len(calls) + 1}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )

    return calls


def normalize_tool_calls(message: Any) -> tuple[list[dict[str, Any]], bool]:
    if getattr(message, "tool_calls", None):
        return (
            [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in message.tool_calls
            ],
            False,
        )

    content = stringify_message_content(getattr(message, "content", None))
    fallback_calls = parse_tool_calls_from_content(content)
    return fallback_calls, bool(fallback_calls)


def make_public_tool_name(server_name: str, tool_name: str) -> str:
    return f"{server_name}__{tool_name}"


def serialize_tool_result(result: Any) -> str:
    dumped = result.model_dump(mode="json", exclude_none=True)
    if result.isError:
        return json.dumps(dumped, ensure_ascii=False, indent=2)

    content = dumped.get("content") or []
    if content and all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        texts = [item.get("text", "") for item in content]
        text_result = "\n\n".join(part for part in texts if part)
        if text_result:
            return text_result

    return json.dumps(dumped, ensure_ascii=False, indent=2)


def get_memory_for_tool(memories: list[ToolMemory], tool_name: str) -> ToolMemory | None:
    for memory in reversed(memories):
        if memory.tool_name == tool_name:
            return memory
    return None


def get_latest_exact_memory(memories: list[ToolMemory]) -> ToolMemory | None:
    for memory in reversed(memories):
        if memory.exact_result is not None:
            return memory
    return None


def replace_placeholders_in_string(value: str, memories: list[ToolMemory]) -> tuple[str, list[str]]:
    unresolved: list[str] = []
    latest_memory = get_latest_exact_memory(memories)

    def replace(match: re.Match[str]) -> str:
        tool_name = match.group(1)
        memory = get_memory_for_tool(memories, tool_name)
        if not memory:
            unresolved.append(tool_name)
            return match.group(0)
        return memory.exact_result or memory.text

    updated = PLACEHOLDER_PATTERN.sub(replace, value)

    def replace_last(match: re.Match[str]) -> str:
        if latest_memory is None:
            unresolved.append("latest-result")
            return match.group(0)
        return latest_memory.exact_result or latest_memory.text

    updated = LAST_RESULT_PATTERN.sub(replace_last, updated)
    return updated, unresolved


def replace_placeholders(value: Any, memories: list[ToolMemory]) -> tuple[Any, list[str]]:
    if isinstance(value, str):
        return replace_placeholders_in_string(value, memories)

    if isinstance(value, list):
        updated_items: list[Any] = []
        unresolved: list[str] = []
        for item in value:
            updated_item, item_unresolved = replace_placeholders(item, memories)
            updated_items.append(updated_item)
            unresolved.extend(item_unresolved)
        return updated_items, unresolved

    if isinstance(value, dict):
        updated_map: dict[str, Any] = {}
        unresolved: list[str] = []
        for key, item in value.items():
            updated_item, item_unresolved = replace_placeholders(item, memories)
            updated_map[key] = updated_item
            unresolved.extend(item_unresolved)
        return updated_map, unresolved

    return value, []


def find_missing_exact_result_issue(
    tool_name: str,
    arguments: dict[str, Any],
    memories: list[ToolMemory],
) -> ValidationIssue | None:
    if tool_name not in {"filesystem__write_file", "filesystem__edit_file"}:
        return None

    content = arguments.get("content")
    if not isinstance(content, str) or not EMPTY_ASSIGNMENT_PATTERN.search(content):
        return None

    recent_exact = [
        memory.exact_result
        for memory in reversed(memories)
        if memory.exact_result is not None
    ][:3]
    missing = [value for value in recent_exact if value and value not in content]
    if not missing:
        return None

    preview = ", ".join(truncate_text(value, 80) for value in missing)
    return ValidationIssue(
        "The next file write appears to contain an empty field while recent tool results "
        f"already produced exact values: {preview}. Re-issue the tool call with the exact values inserted."
    )


def find_instruction_placeholder_issue(
    tool_name: str,
    arguments: dict[str, Any],
    memories: list[ToolMemory],
) -> ValidationIssue | None:
    if tool_name not in {"filesystem__write_file", "filesystem__edit_file"}:
        return None

    content = arguments.get("content")
    if not isinstance(content, str) or not ANGLE_INSTRUCTION_PATTERN.search(content):
        return None

    recent_exact = [
        memory.exact_result
        for memory in reversed(memories)
        if memory.exact_result is not None
    ][:3]
    if not recent_exact:
        return None

    if any(value and value in content for value in recent_exact):
        return None

    preview = ", ".join(truncate_text(value, 80) for value in recent_exact if value)
    return ValidationIssue(
        "The next file write still contains an instructional placeholder such as "
        f"{truncate_text(content, 100)}. Replace that placeholder with the concrete exact value(s): {preview}."
    )


def prepare_tool_call(
    tool_call: dict[str, Any],
    memories: list[ToolMemory],
) -> tuple[dict[str, Any] | None, ValidationIssue | None]:
    function_data = tool_call["function"]
    public_name = function_data["name"]

    arguments_raw = function_data.get("arguments") or "{}"
    try:
        arguments = json.loads(arguments_raw)
    except json.JSONDecodeError as exc:
        return None, ValidationIssue(
            f"Could not decode tool arguments for '{public_name}': {exc}"
        )

    if not isinstance(arguments, dict):
        return None, ValidationIssue(
            f"Tool arguments for '{public_name}' must decode to an object."
        )

    resolved_arguments, unresolved = replace_placeholders(arguments, memories)
    if unresolved:
        unique_names = ", ".join(sorted(set(unresolved)))
        return None, ValidationIssue(
            f"The previous tool call for '{public_name}' referenced unresolved placeholders: {unique_names}. "
            "Use the exact remembered values instead of placeholders."
        )

    issue = find_missing_exact_result_issue(public_name, resolved_arguments, memories)
    if issue:
        return None, issue

    issue = find_instruction_placeholder_issue(public_name, resolved_arguments, memories)
    if issue:
        return None, issue

    prepared = {
        "id": tool_call["id"],
        "type": "function",
        "function": {
            "name": public_name,
            "arguments": json.dumps(resolved_arguments, ensure_ascii=False),
        },
    }
    return prepared, None


def build_repair_note(issue: ValidationIssue, memories: list[ToolMemory]) -> str:
    lines = [
        issue.message,
        "Retry the next step now.",
    ]
    recent_exact = [
        memory
        for memory in reversed(memories)
        if memory.exact_result is not None
    ][:3]
    if recent_exact:
        lines.append("Recent exact values:")
        for memory in recent_exact:
            lines.append(f"- {memory.tool_name}: {truncate_text(memory.exact_result or '', 120)}")
    return "\n".join(lines)


def contains_unresolved_placeholders(text: str) -> bool:
    return bool(PLACEHOLDER_PATTERN.search(text))


async def list_all_tools(session: ClientSession) -> list[Any]:
    cursor: str | None = None
    tools: list[Any] = []
    while True:
        result = await session.list_tools(cursor=cursor)
        tools.extend(result.tools)
        cursor = result.nextCursor
        if not cursor:
            return tools


async def connect_servers(
    exit_stack: AsyncExitStack,
    config: dict[str, Any],
    selected_servers: list[str],
    show_server_logs: bool,
) -> dict[str, RegisteredTool]:
    registry: dict[str, RegisteredTool] = {}
    available_servers = config["mcpServers"]
    server_names = selected_servers or list(available_servers.keys())

    for server_name in server_names:
        if server_name not in available_servers:
            raise KeyError(f"MCP server '{server_name}' was not found in the config.")

        server_config = available_servers[server_name]
        transport = str(server_config.get("transport", "")).strip().lower()
        if not transport:
            transport = "streamable-http" if server_config.get("url") else "stdio"

        if transport == "stdio":
            params = StdioServerParameters(
                command=server_config["command"],
                args=list(server_config.get("args", [])),
                env=dict(server_config.get("env", {})) or None,
                cwd=server_config.get("cwd"),
            )
            errlog = sys.stderr
            if not show_server_logs:
                errlog = exit_stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
            read, write = await exit_stack.enter_async_context(stdio_client(params, errlog=errlog))
        elif transport in {"streamable-http", "streamable_http", "http"}:
            read, write, _ = await exit_stack.enter_async_context(
                streamablehttp_client(
                    url=server_config["url"],
                    headers=dict(server_config.get("headers", {})) or None,
                )
            )
        else:
            raise ValueError(
                f"MCP server '{server_name}' uses unsupported transport '{transport}'."
            )

        session = await exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        for tool in await list_all_tools(session):
            public_name = make_public_tool_name(server_name, tool.name)
            registry[public_name] = RegisteredTool(
                public_name=public_name,
                server_name=server_name,
                original_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                session=session,
            )

    return registry


def build_openai_tools(registry: dict[str, RegisteredTool]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for name in sorted(registry.keys()):
        tool = registry[name]
        description = tool.description or "No description provided."
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.public_name,
                    "description": f"[{tool.server_name}] {description}",
                    "parameters": tool.input_schema,
                },
            }
        )
    return tools


async def call_registered_tool(
    tool_call: dict[str, Any], registry: dict[str, RegisteredTool]
) -> ToolExecution:
    function_data = tool_call["function"]
    public_name = function_data["name"]
    registered = registry.get(public_name)
    if not registered:
        error_text = json.dumps(
            {"error": f"Unknown tool '{public_name}'."},
            ensure_ascii=False,
            indent=2,
        )
        return ToolExecution(
            call=tool_call,
            message_content=error_text,
            memory=ToolMemory(
                tool_name=public_name,
                call_id=tool_call["id"],
                text=error_text,
                exact_result=None,
            ),
        )

    arguments_raw = function_data.get("arguments") or "{}"
    try:
        arguments = json.loads(arguments_raw)
    except json.JSONDecodeError as exc:
        error_text = json.dumps(
            {
                "error": f"Could not decode tool arguments for '{public_name}'.",
                "details": str(exc),
                "raw": arguments_raw,
            },
            ensure_ascii=False,
            indent=2,
        )
        return ToolExecution(
            call=tool_call,
            message_content=error_text,
            memory=ToolMemory(
                tool_name=public_name,
                call_id=tool_call["id"],
                text=error_text,
                exact_result=None,
            ),
        )

    if not isinstance(arguments, dict):
        error_text = json.dumps(
            {
                "error": f"Tool arguments for '{public_name}' must decode to an object.",
                "raw": arguments,
            },
            ensure_ascii=False,
            indent=2,
        )
        return ToolExecution(
            call=tool_call,
            message_content=error_text,
            memory=ToolMemory(
                tool_name=public_name,
                call_id=tool_call["id"],
                text=error_text,
                exact_result=None,
            ),
        )

    print(f"tool> {public_name} {json.dumps(arguments, ensure_ascii=False)}")
    result = await registered.session.call_tool(registered.original_name, arguments)
    text = serialize_tool_result(result)
    exact_result = extract_exact_result(text)
    return ToolExecution(
        call=tool_call,
        message_content=build_tool_message_content(public_name, text, exact_result),
        memory=ToolMemory(
            tool_name=public_name,
            call_id=tool_call["id"],
            text=text,
            exact_result=exact_result,
        ),
    )


async def resolve_model(client: AsyncOpenAI, model_name: str | None) -> str:
    if model_name:
        return model_name

    models = await client.models.list()
    if not models.data:
        raise RuntimeError("The OpenAI-compatible endpoint returned no models.")

    return models.data[0].id


async def run_single_prompt(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    messages: list[dict[str, Any]],
    openai_tools: list[dict[str, Any]],
    registry: dict[str, RegisteredTool],
    max_tool_rounds: int,
) -> str:
    messages.append({"role": "user", "content": prompt})
    memories: list[ToolMemory] = []
    repair_note: str | None = None

    for _ in range((max_tool_rounds * 2) + 4):
        request_messages = inject_runtime_messages(messages, memories, repair_note)
        repair_note = None
        request: dict[str, Any] = {
            "model": model,
            "messages": request_messages,
            "temperature": 0.2,
        }
        if openai_tools:
            request["tools"] = openai_tools
            request["tool_choice"] = "auto"

        response = await client.chat.completions.create(**request)
        message = response.choices[0].message
        tool_calls, fallback_from_content = normalize_tool_calls(message)

        if tool_calls:
            prepared_tool_calls: list[dict[str, Any]] = []
            executions: list[ToolExecution] = []
            working_memories = list(memories)
            issue: ValidationIssue | None = None
            for tool_call in tool_calls:
                prepared_tool_call, issue = prepare_tool_call(tool_call, working_memories)
                if issue:
                    break
                if prepared_tool_call is not None:
                    prepared_tool_calls.append(prepared_tool_call)
                    execution = await call_registered_tool(prepared_tool_call, registry)
                    executions.append(execution)
                    working_memories.append(execution.memory)

            if prepared_tool_calls:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": "" if fallback_from_content else stringify_message_content(message.content),
                    "tool_calls": prepared_tool_calls,
                }
                messages.append(assistant_message)

                for execution in executions:
                    memories.append(execution.memory)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": execution.call["id"],
                            "content": execution.message_content,
                        }
                    )

            if issue:
                repair_note = build_repair_note(issue, working_memories)
                continue
            continue

        answer = stringify_message_content(message.content).strip()
        if contains_unresolved_placeholders(answer):
            repair_note = (
                "Your last answer still contains unresolved placeholders. "
                "Replace them with the exact remembered tool values and answer again."
            )
            continue
        messages.append({"role": "assistant", "content": answer})
        return answer

    raise RuntimeError(
        "The model exceeded the maximum number of tool or repair rounds."
    )


def print_tools(registry: dict[str, RegisteredTool]) -> None:
    if not registry:
        print("No MCP tools are loaded.")
        return

    print("Loaded MCP tools:")
    for name in sorted(registry.keys()):
        tool = registry[name]
        print(f"  - {name}: {tool.description or 'No description provided.'}")


async def interactive_chat(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    openai_tools: list[dict[str, Any]],
    registry: dict[str, RegisteredTool],
    max_tool_rounds: int,
) -> None:
    base_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages = list(base_messages)

    print(f"Model: {model}")
    print_tools(registry)
    print("Commands: /tools, /clear, /exit")

    while True:
        try:
            prompt = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return

        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return
        if prompt == "/clear":
            messages = list(base_messages)
            print("Conversation cleared.")
            continue
        if prompt == "/tools":
            print_tools(registry)
            continue

        answer = await run_single_prompt(
            client=client,
            model=model,
            prompt=prompt,
            messages=messages,
            openai_tools=openai_tools,
            registry=registry,
            max_tool_rounds=max_tool_rounds,
        )
        print(f"\nassistant> {answer}")


async def async_main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = get_project_root() / config_path

    config = load_config(config_path)
    api_key = get_api_key(args)

    client = AsyncOpenAI(base_url=args.base_url, api_key=api_key)

    async with AsyncExitStack() as exit_stack:
        registry = await connect_servers(
            exit_stack,
            config,
            args.server,
            args.show_server_logs,
        )
        openai_tools = build_openai_tools(registry)
        model = await resolve_model(client, args.model)

        if args.once:
            prompt = args.once
        elif args.prompt:
            prompt = " ".join(args.prompt)
        else:
            prompt = None

        if prompt is not None:
            messages = [{"role": "system", "content": args.system_prompt}]
            answer = await run_single_prompt(
                client=client,
                model=model,
                prompt=prompt,
                messages=messages,
                openai_tools=openai_tools,
                registry=registry,
                max_tool_rounds=args.max_tool_rounds,
            )
            print(answer)
            return 0

        await interactive_chat(
            client=client,
            model=model,
            system_prompt=args.system_prompt,
            openai_tools=openai_tools,
            registry=registry,
            max_tool_rounds=args.max_tool_rounds,
        )
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
