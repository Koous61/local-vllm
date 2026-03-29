# Local vLLM + Open WebUI on Windows + Docker

This project provides a local `vLLM` server behind an OpenAI-compatible API and an `Open WebUI` instance for browser-based testing. External tools can connect to `http://localhost:8000/v1`, while the web UI is available at `http://localhost:3010`.

## What this setup includes

- GPU-backed `vLLM` running through Docker Desktop and WSL2
- An OpenAI-compatible API for chat completions and related endpoints
- `Open WebUI` preconfigured to use the local `vLLM` backend
- Persistent Hugging Face cache in `./data/hf-cache`
- Persistent Open WebUI data in `./data/open-webui`
- PowerShell and `.cmd` helpers for startup, shutdown, logs, smoke tests, and model switching
- A default coding model tuned for a 16 GB GPU: `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`

## Requirements

- Windows with Docker Desktop installed
- WSL2 enabled
- NVIDIA GPU available inside Docker
- Optional Hugging Face token for gated models

This machine has already been validated for the core runtime requirements:

- Docker is installed
- WSL2 is running
- Docker can access the NVIDIA GPU

## Quick start

```powershell
Copy-Item .env.example .env
.\start.cmd
.\test-chat.cmd
```

The first startup downloads the `vLLM` image and the selected model, so it can take a while. `Open WebUI` also downloads its own small embedding model on first boot, so the UI container may need an extra minute or two the first time.

After startup:

- API: `http://localhost:8000/v1`
- Open WebUI: `http://localhost:3010`

## Main files

- `.env` controls model and runtime settings
- `docker-compose.yml` runs both `vLLM` and `Open WebUI`
- `scripts/start.ps1` starts the stack and waits for readiness
- `scripts/logs.ps1` tails container logs
- `scripts/test-chat.ps1` sends a real request to the local OpenAI-compatible API
- `scripts/use-model.ps1` switches the active model and can apply it immediately
- `start.cmd`, `stop.cmd`, `logs.cmd`, `test-chat.cmd`, and `use-model.cmd` avoid PowerShell execution-policy friction on Windows

## Common commands

```powershell
.\start.cmd
.\logs.cmd
.\test-chat.cmd
.\stop.cmd
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
```

To follow logs during startup:

```powershell
.\logs.cmd
```

## Switching models

The easiest way to switch the active model is:

```powershell
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
```

This command updates `.env`, derives a `SERVED_MODEL_NAME`, and applies the change to the running stack.

You can still edit `.env` manually if you want:

```dotenv
MODEL_ID=Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
SERVED_MODEL_NAME=qwen2.5-coder-14b-instruct-awq
```

Then restart:

```powershell
.\stop.cmd
.\start.cmd
```

You can also point `MODEL_ID` to a local folder mounted under `/models`:

```dotenv
MODEL_ID=/models/my-local-model
SERVED_MODEL_NAME=my-local-model
```

Place that model under `./models/my-local-model`.

Examples:

```powershell
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
.\use-model.cmd Qwen/Qwen2.5-7B-Instruct
.\use-model.cmd meta-llama/Llama-3.2-3B-Instruct
.\use-model.cmd deepseek-ai/DeepSeek-R1-Distill-Qwen-7B
.\use-model.cmd /models/my-local-model
.\use-model.cmd Qwen/Qwen2.5-VL-7B-Instruct -TrustRemoteCode
.\use-model.cmd meta-llama/Llama-3.2-3B-Instruct -HFToken hf_xxx
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ -NoRestart
```

## Gated and custom models

If a model is gated on Hugging Face, set:

```dotenv
HF_TOKEN=hf_xxx
```

If a model requires remote code:

```dotenv
TRUST_REMOTE_CODE=true
```

Tool calling is enabled by default for the current Qwen model so that IDEs such as Rider can use OpenAI-compatible requests with `tool_choice="auto"`:

```dotenv
ENABLE_AUTO_TOOL_CHOICE=true
TOOL_CALL_PARSER=hermes
```

If a model is slightly too large for VRAM, you can enable CPU offload:

```dotenv
CPU_OFFLOAD_GB=8
```

If you need extra `vLLM` flags, pass them through:

```dotenv
EXTRA_ARGS=--chat-template /models/templates/chat_template.jinja
```

## Connect from other apps

Base URL:

```text
http://localhost:8000/v1
```

API key:

```text
local-vllm-key
```

## Rider integration

You can connect JetBrains Rider to the local `vLLM` instance through AI Assistant using the OpenAI-compatible provider.

Recommended setup:

1. Open `Settings | Tools | AI Assistant | Providers & API keys`.
2. In `Third-party AI providers`, select `OpenAI-compatible`.
3. Set `URL` to `http://localhost:8000/v1`.
4. Set `API Key` to `local-vllm-key`.
5. Set `Tool calling` to `Off` unless you explicitly need MCP tool support.
6. Click `Test Connection`, then `Apply`.

After the connection is configured:

1. Stay in `Settings | Tools | AI Assistant | Providers & API keys`.
2. In `Model Assignment`, set:
   - `Core features`: `qwen2.5-coder-14b-instruct-awq`
   - `Instant helpers`: `qwen2.5-coder-14b-instruct-awq`
   - `Context window`: `8192`
3. Click `Apply`.
4. Open `AI Chat` in Rider and select `qwen2.5-coder-14b-instruct-awq` if it is not already selected.

Notes:

- This setup works best in recent Rider versions with the current AI Assistant plugin.
- For local and OpenAI-compatible endpoints, some AI Assistant capabilities depend on model compatibility and JetBrains AI subscription state.
- According to JetBrains AI Assistant documentation, pure BYOK/local setups do not provide `Next edit suggestions`, and `Code completion` requires a compatible FIM completion model.
- If Rider reports an error mentioning `tool_choice="auto"`, make sure your `.env` still contains `ENABLE_AUTO_TOOL_CHOICE=true` and `TOOL_CALL_PARSER=hermes`, then restart the stack.

## Browser UI

Open WebUI is available at:

```text
http://localhost:3010
```

On first launch, create your admin account in the browser. The UI is already configured to use your local `vLLM` endpoint, and the served model should appear in the selector automatically.

If you later change `MODEL_ID` or `SERVED_MODEL_NAME` in `.env`, restart the stack:

```powershell
.\stop.cmd
.\start.cmd
```

Open WebUI stores settings persistently after first boot. If you ever want a completely clean UI state, stop the stack and remove `./data/open-webui`.

## Python example

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

## Recommended model sizes for this GPU

Your RTX 5080 has 16 GB of VRAM, so these are practical starting points:

- `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ` as the default coding-focused choice
- `Qwen/Qwen2.5-7B-Instruct`
- `meta-llama/Llama-3.2-3B-Instruct`
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`
- `Qwen/Qwen2.5-14B-Instruct-AWQ` if you want a general quantized instruction model

The project defaults to `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ` because it is a stronger coding model than the smaller variants while still being a realistic fit for a 16 GB GPU thanks to AWQ quantization.

On Windows + WSL2, some VRAM is usually occupied by the desktop. The default `GPU_MEMORY_UTILIZATION` is intentionally conservative. The current default `MAX_MODEL_LEN=8192` is chosen as a practical compromise for Rider and other IDE integrations. If startup fails with a free-memory error, lower `GPU_MEMORY_UTILIZATION`, reduce `MAX_MODEL_LEN`, or both. If you want to push larger models later, increase values gradually after you confirm stable boots.

## Notes

- `vLLM` is best suited for open-weight models, not proprietary cloud-only models
- Not every Hugging Face model is guaranteed, but `vLLM` can also run many models through the Transformers backend
- If a larger model fails on memory, reduce `MAX_MODEL_LEN` or use a smaller or quantized checkpoint

## License

This repository is licensed under the Apache License 2.0. See [LICENSE](./LICENSE).
