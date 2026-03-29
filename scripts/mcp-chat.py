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


@dataclass
class RegisteredTool:
    public_name: str
    server_name: str
    original_name: str
    description: str
    input_schema: dict[str, Any]
    session: ClientSession


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
) -> str:
    function_data = tool_call["function"]
    public_name = function_data["name"]
    registered = registry.get(public_name)
    if not registered:
        return json.dumps(
            {"error": f"Unknown tool '{public_name}'."},
            ensure_ascii=False,
            indent=2,
        )

    arguments_raw = function_data.get("arguments") or "{}"
    try:
        arguments = json.loads(arguments_raw)
    except json.JSONDecodeError as exc:
        return json.dumps(
            {
                "error": f"Could not decode tool arguments for '{public_name}'.",
                "details": str(exc),
                "raw": arguments_raw,
            },
            ensure_ascii=False,
            indent=2,
        )

    if not isinstance(arguments, dict):
        return json.dumps(
            {
                "error": f"Tool arguments for '{public_name}' must decode to an object.",
                "raw": arguments,
            },
            ensure_ascii=False,
            indent=2,
        )

    print(f"tool> {public_name} {json.dumps(arguments, ensure_ascii=False)}")
    result = await registered.session.call_tool(registered.original_name, arguments)
    return serialize_tool_result(result)


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

    for _ in range(max_tool_rounds + 1):
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }
        if openai_tools:
            request["tools"] = openai_tools
            request["tool_choice"] = "auto"

        response = await client.chat.completions.create(**request)
        message = response.choices[0].message
        tool_calls, fallback_from_content = normalize_tool_calls(message)

        if tool_calls:
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "" if fallback_from_content else stringify_message_content(message.content),
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)

            for tool_call in tool_calls:
                tool_output = await call_registered_tool(tool_call, registry)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_output,
                    }
                )
            continue

        answer = stringify_message_content(message.content).strip()
        messages.append({"role": "assistant", "content": answer})
        return answer

    raise RuntimeError(
        f"The model exceeded the maximum number of tool rounds ({max_tool_rounds})."
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
