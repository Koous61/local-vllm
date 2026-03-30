# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- terminal MCP host for the local `vLLM` endpoint
- `filesystem` MCP profile
- Playwright browser MCP profile
- read-only Git MCP profile for local repositories
- read-only UVCS MCP profile for UVCS / Plastic SCM repositories, including Unreal Engine projects
- `doctor.cmd` / `scripts/stack/doctor.ps1` for environment and service diagnostics
- `docs/MCP.md` and `docs/PROJECT-STRUCTURE.md`
- terminal agent with profiles, resumable sessions, and read-only defaults
- `docs/AGENT.md`
- compact Git status summary tool for faster local-model repository inspection
- `list-mcp.cmd`, `enable-mcp.cmd`, and `disable-mcp.cmd` for persistent MCP profile toggling
- read-only Docker MCP profile for compose status, logs, container inspection, and images
- Node.js MCP profile for package.json inspection, dependency install, script execution, and builds
- Python MCP profile for project inspection, syntax checks, script or module execution, and test runs
- Unreal-oriented UVCS tools for area summaries, asset-only views, and workspace overviews
- additional Unreal UVCS tools for plugin changes, Build.cs or Target.cs files, config files, and gameplay-code-only views

### Changed

- improved MCP reliability with placeholder substitution, tool-result memory, and repair retries
- extracted shared MCP Python runtime logic so `mcp-chat` and the terminal agent use the same transport and tool loop
- tuned Git MCP and repo-agent guidance so simple branch and clean/dirty questions prefer a smaller summary tool over the heavier raw status payload
- added Docker-aware agent/profile guidance and Docker tool narrowing for stack questions
- added Node-aware MCP and agent guidance, plus explicit write-gating for local build and install workflows
- added Python-aware MCP and agent guidance, plus explicit write-gating for script, module, and test execution workflows
- added agent-side MCP server routing so the terminal agent can choose a small server subset automatically before the main run
- added a Docker healthcheck for `vLLM` and made `Open WebUI` wait for a healthy backend before startup
- reorganized scripts into `scripts/lib`, `scripts/stack`, and `scripts/mcp`
- simplified `README.md` by linking deeper MCP details to dedicated docs
- added an `enabled` flag for MCP server entries so the default loaded server set can be controlled from config
- diagnostics now report the local `git` command used by the Git profile
- diagnostics now report the optional local `cm` command used by the UVCS profile
- diagnostics now report local `npm`, `pnpm`, and `yarn` commands used by the Node profile

## [1.0.0] - 2026-03-29

### Added

- local `vLLM` stack through Docker Desktop and WSL2
- OpenAI-compatible API on `http://localhost:8000/v1`
- `Open WebUI` on `http://localhost:3010`
- `use-model.cmd` for one-command model switching
- default coding model `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
- Rider integration notes
- Apache-2.0 license

### Changed

- enabled Rider-compatible auto tool choice with the `hermes` parser
- raised the default context window to `8192`
