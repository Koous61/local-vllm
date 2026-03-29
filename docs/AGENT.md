# Terminal Agent

This repository includes a terminal agent that runs on top of the local `vLLM` endpoint and the bundled MCP profiles.

## What it does

- accepts a goal from the terminal
- selects a small MCP toolset based on the chosen profile
- iterates in short steps until it can finish or the step budget runs out
- stores session state under `./data/agent-sessions`
- defaults to read-only mode by filtering out write-like MCP tools

## Quick start

```powershell
.\setup-mcp.cmd
.\agent.cmd --goal "Inspect this repo and tell me how to start the terminal agent."
```

By default, the `coder` profile is used. It currently prefers a minimal default toolset for stability, so it starts with `filesystem` unless you override the servers explicitly.

## Profiles

- `coder`: local codebase and file inspection
- `repo`: Git-oriented repository analysis, with a compact Git status summary as the preferred first step
- `ops`: Docker and local stack inspection for compose services, containers, and logs
- `node`: Node.js project inspection and build workflows
- `unreal`: Unreal Engine and UVCS workspace analysis
- `research`: browser-driven exploration with Playwright

Example:

```powershell
.\agent.cmd --profile unreal --server uvcs --goal "Summarize Unreal gameplay-code changes in the current workspace."
.\agent.cmd --profile ops --server docker --goal "Check whether the local Docker stack is healthy."
.\agent.cmd --profile node --server node --goal "Inspect the configured Node project and list the available scripts."
.\agent.cmd --profile node --server node --allow-writes --goal "Build the configured Node project."
```

## Server selection

Behavior:

- if you pass `--server`, that exact set is used
- otherwise the profile picks a minimal default server from the enabled config
- if you need multiple MCP servers in one run, pass them explicitly

Examples:

```powershell
.\agent.cmd --goal "Inspect this repo and tell me the main entrypoints."
.\agent.cmd --profile repo --server git --goal "Summarize the current branch and dirty files."
.\agent.cmd --profile research --server playwright --server filesystem --goal "Open example.com and save the title into a local note."
```

## Sessions and resume

Each run creates `./data/agent-sessions/<session-id>/session.json`.

Resume a session with:

```powershell
.\agent.cmd --resume 20260329-232248-coder-sample-task
```

You can also pass a direct path to `session.json`.

If the session is already completed, the agent prints the saved final result and exits.

## Safety

Default behavior:

- write-like tools are filtered out
- the agent prefers exact verification before finishing tasks that ask for a specific path, command, or entrypoint
- session history is persisted after each completed step

If you later add write-capable workflows, expose them behind an explicit `--allow-writes` run.

The Node profile already follows that pattern: dependency install, script execution, and build tools stay blocked until `--allow-writes` is present.

## Notes

- the current local Qwen model is strongest with focused tools and short step budgets
- Git-heavy tasks can still be slower than filesystem-only runs
- if a task truly needs multiple MCP servers, pass them explicitly instead of relying on the profile default
