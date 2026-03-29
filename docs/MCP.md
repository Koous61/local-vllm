# MCP Guide

This project includes a terminal MCP host that uses the same local `vLLM` endpoint as the rest of the stack.

## First-time setup

```powershell
.\setup-mcp.cmd
```

That command:

- creates `./.venv`
- installs `mcp==1.26.0` and `openai==2.30.0`
- creates or updates `./mcp-servers.json`
- registers a local `filesystem` MCP server through `npx`

## Terminal usage

Interactive mode:

```powershell
.\mcp-chat.cmd --server filesystem
```

One-shot mode:

```powershell
.\mcp-chat.cmd --server filesystem --once "List the files in D:\\Deals\\local-vllm and tell me where the main startup script lives."
```

Useful commands inside the interactive chat:

- `/tools`
- `/clear`
- `/exit`

If you need raw stderr from the MCP server process for debugging, add `--show-server-logs`.

## Reliability behavior

The terminal host is designed to be forgiving with local models:

- it supports both standard OpenAI `tool_calls` and Qwen-style tool call tags
- it keeps a short memory of recent tool outputs
- it substitutes common placeholders such as `<result-of-tool>` and `<result_of_previous_code>`
- it retries invalid `write_file` and `edit_file` steps when the model drops an exact value between tool calls

## Filesystem MCP

The bundled `filesystem` profile uses the official `@modelcontextprotocol/server-filesystem` package through `npx`.

Default setup:

```powershell
.\setup-mcp.cmd
```

Update allowed directories later:

```powershell
.\add-filesystem-mcp.cmd -AllowedPath D:\Deals\local-vllm
.\add-filesystem-mcp.cmd -AllowedPath D:\Deals\local-vllm D:\Deals\another-project
```

Notes:

- it only sees directories explicitly passed as allowed paths
- `Node.js` is required because the server is started through `npx`

## Browser MCP

The bundled browser profile uses the official Playwright MCP server.

Register it with:

```powershell
.\add-browser-mcp.cmd
```

Default behavior on this machine:

- server name: `playwright`
- package: `@playwright/mcp@latest`
- browser: `msedge`
- output directory: `./data/playwright-mcp`
- mode: headed

Examples:

```powershell
.\mcp-chat.cmd --server playwright
.\mcp-chat.cmd --server playwright --once "Open https://example.com and tell me the page title."
.\mcp-chat.cmd --server filesystem --server playwright --once "Open https://example.com, get the page title, and write it into a file in this repo."
```

Optional flags:

```powershell
.\add-browser-mcp.cmd -Browser chrome
.\add-browser-mcp.cmd -Headless
.\add-browser-mcp.cmd -Isolated
```

## Config file

The local runtime config lives in `mcp-servers.json`. It is intentionally ignored by Git.

The committed template lives in `mcp-servers.example.json`.
