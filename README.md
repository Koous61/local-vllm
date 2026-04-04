# Local Agent Stack for Windows

Run a local OpenAI-compatible model stack on Windows with one repo. This project bundles `vLLM`, `Open WebUI`, MCP tooling, and a terminal agent behind simple `.cmd` entrypoints, so you can use the same local model from your browser, IDE, scripts, and terminal workflows.

It is designed for developers who want a practical local setup instead of stitching together Docker files, runtime flags, model configs, and MCP profiles by hand.

## What You Get

- A local `vLLM` server with an OpenAI-compatible API at `http://localhost:8000/v1`
- `Open WebUI` prewired to the same model at `http://localhost:3010`
- Windows-friendly `.cmd` wrappers for startup, shutdown, diagnostics, logs, smoke tests, and model switching
- Optional MCP tooling with bundled `filesystem`, browser, Git, Docker, Node.js, Python, and UVCS profiles
- A terminal agent that runs on top of the local model and can route across enabled MCP servers
- Persistent model cache in `./data/hf-cache` and persistent Open WebUI state in `./data/open-webui`
- A default coding model tuned for a 16 GB GPU: `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`

## Who This Is For

This repository is a good fit if you want to:

- run local open-weight models behind an OpenAI-style API
- test prompts or chat in a browser without giving up API access
- point local tools and IDEs at one stable localhost endpoint
- experiment with MCP workflows without assembling the host runtime yourself
- use a terminal agent against the same local stack

## Requirements

Core stack:

- Windows with Docker Desktop installed
- WSL2 enabled
- An NVIDIA GPU available inside Docker

Optional features:

- A Hugging Face token for gated models
- Python for the MCP host virtual environment
- Node.js and `npx` for MCP servers that use Node-based packages

## Quick Start

```powershell
Copy-Item .env.example .env
.\start.cmd
.\test-chat.cmd
```

After startup:

- API base URL: `http://localhost:8000/v1`
- API key: `local-vllm-key`
- Open WebUI: `http://localhost:3010`

Notes:

- The first startup downloads the Docker image and the selected model, so it can take a while.
- Open WebUI also downloads its own small embedding model on first boot.
- The UI waits for a healthy `vLLM` backend before completing startup, which helps avoid cold-boot race conditions.

## Common Commands

| Command | Purpose |
| --- | --- |
| `.\start.cmd` | Start or update the Docker stack |
| `.\stop.cmd` | Stop the stack |
| `.\logs.cmd` | Follow container logs |
| `.\doctor.cmd` | Check local prerequisites, config, and live endpoints |
| `.\test-chat.cmd` | Run a quick API smoke test |
| `.\use-model.cmd <model>` | Switch to another model |
| `.\setup-mcp.cmd` | Create the local MCP environment |
| `.\list-mcp.cmd` | Show configured MCP profiles |
| `.\mcp-chat.cmd --server filesystem` | Start a direct MCP chat session |
| `.\agent.cmd --goal "..."` | Run the terminal agent |

## Configure the Model

The main configuration lives in `.env`.

Important variables:

```dotenv
MODEL_ID=Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
SERVED_MODEL_NAME=qwen2.5-coder-14b-instruct-awq
HF_TOKEN=
TRUST_REMOTE_CODE=false
GPU_MEMORY_UTILIZATION=0.82
MAX_MODEL_LEN=8192
CPU_OFFLOAD_GB=0
EXTRA_ARGS=
```

Switch models with the helper command:

```powershell
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
.\use-model.cmd Qwen/Qwen2.5-7B-Instruct
.\use-model.cmd meta-llama/Llama-3.2-3B-Instruct
.\use-model.cmd deepseek-ai/DeepSeek-R1-Distill-Qwen-7B
.\use-model.cmd Qwen/Qwen2.5-VL-7B-Instruct -TrustRemoteCode
.\use-model.cmd meta-llama/Llama-3.2-3B-Instruct -HFToken hf_xxx
.\use-model.cmd /models/my-local-model
```

You can also point `MODEL_ID` to a local directory mounted under `/models`. For example, `MODEL_ID=/models/my-local-model` maps to `./models/my-local-model` in this repository.

## Use the Local API From Other Tools

The stack exposes a standard OpenAI-style endpoint:

- Base URL: `http://localhost:8000/v1`
- API key: `local-vllm-key`

Example with the OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="local-vllm-key",
)

response = client.chat.completions.create(
    model="qwen2.5-coder-14b-instruct-awq",
    messages=[
        {"role": "user", "content": "Write a short Python function that reverses a string."}
    ],
)

print(response.choices[0].message.content)
```

## Open WebUI

Open `http://localhost:3010` in your browser after the stack is ready.

On first launch:

1. Create your admin account.
2. Confirm that the served model appears in the model selector.
3. Start chatting against the same local `vLLM` backend that powers the API.

If you later change `MODEL_ID` or `SERVED_MODEL_NAME`, restart the stack:

```powershell
.\stop.cmd
.\start.cmd
```

## MCP and Terminal Agent

MCP support is optional, but this repository includes a ready-to-run host and helper scripts for common local profiles.

First-time setup:

```powershell
.\setup-mcp.cmd
.\list-mcp.cmd
```

Start a direct MCP chat session:

```powershell
.\mcp-chat.cmd --server filesystem
```

Run the terminal agent:

```powershell
.\agent.cmd --goal "Inspect this repo and explain how to start the stack."
```

Add more profiles as needed:

```powershell
.\add-browser-mcp.cmd
.\add-docker-mcp.cmd
.\add-git-mcp.cmd
.\add-node-mcp.cmd -ProjectPath D:\path\to\node-project
.\add-python-mcp.cmd -ProjectPath D:\path\to\python-project
.\add-uvcs-mcp.cmd -WorkspacePath D:\Work\MyUnrealProject
```

The terminal agent defaults to read-only behavior and stores sessions under `./data/agent-sessions`.

## Recommended Models for 16 GB VRAM

Practical starting points for a 16 GB GPU:

- `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
- `Qwen/Qwen2.5-7B-Instruct`
- `meta-llama/Llama-3.2-3B-Instruct`
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`
- `Qwen/Qwen2.5-14B-Instruct-AWQ`

The default is `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ` because it offers a strong coding-focused baseline while still being a realistic fit for 16 GB VRAM thanks to AWQ quantization.

## Troubleshooting

- Run `.\doctor.cmd` for a combined environment, config, and live-endpoint check.
- If `vLLM` fails with a memory error, reduce `MAX_MODEL_LEN`, lower `GPU_MEMORY_UTILIZATION`, or choose a smaller or quantized model.
- If a Hugging Face model is gated, set `HF_TOKEN` in `.env`.
- If a model requires custom code, set `TRUST_REMOTE_CODE=true` or use `-TrustRemoteCode` with `.\use-model.cmd`.
- If you need extra `vLLM` flags, pass them through `EXTRA_ARGS`.

## Repository Layout

```text
.
|-- docs/
|-- scripts/
|   |-- container/
|   |-- lib/
|   |-- mcp/
|   `-- stack/
|-- data/
|-- models/
|-- *.cmd
|-- docker-compose.yml
`-- README.md
```

The root contains the commands you use most often. Implementation details live under `scripts/`, and deeper documentation lives under `docs/`.

## Documentation

- [`docs/MCP.md`](docs/MCP.md): MCP setup, profiles, and usage
- [`docs/AGENT.md`](docs/AGENT.md): terminal agent behavior, profiles, and resume flow
- [`docs/PROJECT-STRUCTURE.md`](docs/PROJECT-STRUCTURE.md): repository layout and ownership
- [`CHANGELOG.md`](CHANGELOG.md): project history

## License

Licensed under the Apache License 2.0. See [`LICENSE`](LICENSE).
