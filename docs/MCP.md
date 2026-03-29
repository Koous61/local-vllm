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

Inspect the configured MCP profiles any time with:

```powershell
.\list-mcp.cmd
```

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

If you want a goal-driven workflow instead of a direct chat loop, use the terminal agent documented in `docs/AGENT.md`:

```powershell
.\agent.cmd --goal "Inspect this repo and tell me how to start the terminal agent."
```

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

## Docker MCP

The bundled Docker profile is a local read-only MCP server for Docker Desktop and Docker Compose inspection. It uses the installed `docker` CLI and is tuned for stack summaries, compose service status, logs, and container inspection.

Register it with:

```powershell
.\add-docker-mcp.cmd
.\add-docker-mcp.cmd -ProjectPath D:\Deals\local-vllm
```

If you omit `-ProjectPath`, the helper tries to detect a compose project from the repository root.

Current tools:

- `docker__list_projects`
- `docker__compose_status_summary`
- `docker__compose_ps`
- `docker__compose_logs`
- `docker__list_containers`
- `docker__container_inspect`
- `docker__list_images`

Examples:

```powershell
.\mcp-chat.cmd --server docker
.\mcp-chat.cmd --server docker --once "Use docker__compose_status_summary and summarize the current compose services."
.\mcp-chat.cmd --server docker --once "Use docker__compose_logs and show recent logs for the vllm service."
.\agent.cmd --profile ops --server docker --goal "Check whether the local Docker stack is healthy."
```

Notes:

- the server is intentionally read-only in this project version
- `docker__compose_status_summary` is the best first tool for service states and health
- `docker__compose_ps` returns the fuller per-service payload when you need more detail
- `docker__compose_logs` returns non-following logs with tail and truncation controls
- `docker__list_containers` and `docker__list_images` provide machine-wide Docker views beyond one compose project

## Git MCP

The bundled Git profile is a local read-only MCP server for Git repositories. It uses the installed `git` CLI and exposes repository inspection tools such as status, branches, remotes, log, file history, commit details, and diff.

Register it with:

```powershell
.\add-git-mcp.cmd
.\add-git-mcp.cmd -RepoPath D:\Deals\local-vllm
```

If you omit `-RepoPath`, the helper tries to detect the current repository from the project root.

Current tools:

- `git__list_repositories`
- `git__status_summary`
- `git__repository_status`
- `git__branches`
- `git__remotes`
- `git__log`
- `git__file_history`
- `git__show_commit`
- `git__diff`

Examples:

```powershell
.\mcp-chat.cmd --server git
.\mcp-chat.cmd --server git --once "Use git__status_summary and summarize the current branch and changed files."
.\mcp-chat.cmd --server git --once "Use git__log and show the last 3 commits."
.\mcp-chat.cmd --server git --once "Use git__diff for README.md and summarize the current unstaged diff."
```

Notes:

- the server is intentionally read-only in this project version
- access is restricted to the configured repository roots
- `git__status_summary` is the best first tool for branch, clean or dirty state, ahead or behind, and a short changed-file sample
- `git__repository_status` is still available when you explicitly need the fuller structured file-entry payload
- `git__diff` can inspect unstaged changes, staged changes, or diffs against a ref
- `git__file_history` follows file renames through Git history

## UVCS MCP

The bundled UVCS profile is a local read-only MCP server for UVCS / Plastic SCM repositories, including Unreal Engine projects. It shells out to the installed `cm.exe` client and only exposes inspection tools, not write actions.

Register it with:

```powershell
.\add-uvcs-mcp.cmd -WorkspacePath D:\Work\MyUnrealProject
```

If you omit `-WorkspacePath`, the helper tries to auto-detect local UVCS workspaces from `cm workspace list`.

Current tools:

- `uvcs__list_workspaces`
- `uvcs__workspace_info`
- `uvcs__status`
- `uvcs__fileinfo`
- `uvcs__history`
- `uvcs__main_branch`
- `uvcs__unreal_change_summary`
- `uvcs__unreal_asset_status`
- `uvcs__unreal_workspace_summary`
- `uvcs__unreal_plugin_status`
- `uvcs__unreal_build_script_status`
- `uvcs__unreal_config_status`
- `uvcs__unreal_gameplay_code_status`

Examples:

```powershell
.\mcp-chat.cmd --server uvcs
.\mcp-chat.cmd --server uvcs --once "Use uvcs__workspace_info and tell me the current workspace root and current branch."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__status and summarize the changed and private files in the current workspace."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_change_summary and summarize changes by Source, Config, Content, Plugins, and other Unreal areas."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_asset_status and list only changed Unreal assets and maps."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_workspace_summary and tell me what changed in this Unreal workspace for a developer."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_plugin_status and summarize changed plugin files in the current workspace."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_build_script_status and list changed Build.cs or Target.cs files in the current workspace."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_config_status and list changed Unreal config files in the current workspace."
.\mcp-chat.cmd --server uvcs --once "Use uvcs__unreal_gameplay_code_status and list changed gameplay-oriented Unreal C++ files in the current workspace."
```

Notes:

- the server is intentionally read-only in this project version
- the local `cm` command must be installed and signed into the target UVCS / Plastic SCM account
- access is restricted to the configured workspace roots
- if several workspaces are enabled, pass an explicit workspace path when a tool asks about a specific one

Unreal-specific helpers:

- `uvcs__unreal_change_summary` groups entries by top-level Unreal areas such as `Source`, `Config`, `Content`, `Plugins`, `Project`, and `Devops`
- `uvcs__unreal_asset_status` filters the current status down to `.uasset` and `.umap` entries, with an optional `maps_only` flag
- `uvcs__unreal_workspace_summary` provides a developer-oriented overview of changed project files, code, config, assets, plugin files, and build or DevOps changes
- `uvcs__unreal_plugin_status` isolates changed files under `Plugins/` and groups them by plugin name
- `uvcs__unreal_build_script_status` isolates changed `.Build.cs` and `.Target.cs` files
- `uvcs__unreal_config_status` isolates changed `Config/*.ini` and plugin config files
- `uvcs__unreal_gameplay_code_status` isolates gameplay-oriented C++ code and filters out common `Editor`, `Tests`, `Developer`, `Programs`, and `ThirdParty` code paths by heuristic module and path matching

## Config file

The local runtime config lives in `mcp-servers.json`. It is intentionally ignored by Git.

The committed template lives in `mcp-servers.example.json`.

Each server entry can include an `enabled` flag:

```json
{
  "mcpServers": {
    "filesystem": {
      "enabled": true
    },
    "playwright": {
      "enabled": false
    },
    "git": {
      "enabled": true
    },
    "uvcs": {
      "enabled": true
    }
  }
}
```

Behavior:

- if you do not pass `--server`, the terminal host loads only servers with `enabled: true`
- if you pass `--server` explicitly, that selection overrides the default enabled set

Persistent enable or disable commands:

```powershell
.\list-mcp.cmd
.\disable-mcp.cmd playwright
.\enable-mcp.cmd playwright
.\disable-mcp.cmd filesystem uvcs
```
