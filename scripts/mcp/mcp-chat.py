from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from local_mcp_runtime import (
    DEFAULT_SYSTEM_PROMPT,
    RegisteredTool,
    build_openai_tools,
    connect_servers,
    get_api_key,
    get_project_root,
    load_config,
    resolve_model,
    run_single_prompt,
)


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
    api_key = get_api_key(args.api_key)
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
