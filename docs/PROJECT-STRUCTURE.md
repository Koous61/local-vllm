# Project Structure

This repository keeps user-facing commands at the root and groups implementation files by responsibility.

## Top level

- `.env` and `.env.example`: runtime configuration for the local stack
- `docker-compose.yml`: defines the `vLLM` and `Open WebUI` services
- `*.cmd`: stable Windows entry points for the main workflows
- `requirements-mcp.txt`: Python dependencies for the terminal MCP client
- `mcp-servers.example.json`: example MCP configuration template

## Runtime data

- `data/`: persistent runtime state, caches, browser outputs, and local UI data
- `models/`: optional local model mounts exposed into the `vLLM` container

## Scripts

- `scripts/lib/`: shared PowerShell helpers and config utilities
- `scripts/stack/`: local stack lifecycle, readiness checks, smoke tests, and model switching
- `scripts/mcp/`: MCP configuration helpers and the terminal MCP client
- `scripts/container/`: files mounted directly into containers, such as the `vLLM` startup entrypoint

## Design intent

- keep root commands stable even if implementation files move
- separate Docker/runtime concerns from MCP concerns
- isolate shared helper code so new features do not duplicate path and config logic
- make it easier to add future areas such as `scripts/models/`, `docs/guides/`, or tests without turning `scripts/` into a flat dump
