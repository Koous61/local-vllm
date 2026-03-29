# Local vLLM + Open WebUI on Windows + Docker

This project gives you a local `vLLM` server behind an OpenAI-compatible API plus `Open WebUI` for interactive testing in the browser. You can point external tools at `http://localhost:8000/v1`, while using the UI on `http://localhost:3010`.

## What you get

- GPU-backed `vLLM` through Docker Desktop and WSL2
- OpenAI-compatible endpoint for chat/completions and related APIs
- `Open WebUI` already wired to the local `vLLM` backend
- Persistent Hugging Face cache in `./data/hf-cache`
- Persistent Open WebUI data in `./data/open-webui`
- Simple PowerShell scripts for start, stop, logs, and a real test request
- Model switching through `.env` without editing the compose file

## Requirements

- Windows with Docker Desktop
- WSL2 enabled
- NVIDIA GPU available in Docker
- Optional Hugging Face token for gated models

This machine has already been validated for the hard part:

- Docker is installed
- WSL2 is running
- Docker can access the NVIDIA GPU

## Quick start

```powershell
Copy-Item .env.example .env
.\start.cmd
.\test-chat.cmd
```

The first start downloads both the `vLLM` image and the chosen model, so it can take a while.
Open WebUI also downloads its own small embedding model on first boot, so the UI container may need an extra minute or two the very first time.

After startup:

- API: `http://localhost:8000/v1`
- UI: `http://localhost:3010`

## Main files

- `.env` controls the model and server settings
- `docker-compose.yml` runs both `vLLM` and `Open WebUI`
- `scripts/start.ps1` starts the service and waits for readiness
- `scripts/logs.ps1` tails container logs
- `scripts/test-chat.ps1` sends a real chat request to the local API
- `scripts/use-model.ps1` switches the served model and can apply it immediately
- `start.cmd`, `stop.cmd`, `logs.cmd`, `test-chat.cmd`, `use-model.cmd` avoid PowerShell execution-policy friction on Windows

## Useful commands

```powershell
.\start.cmd
.\logs.cmd
.\test-chat.cmd
.\stop.cmd
.\use-model.cmd Qwen/Qwen2.5-7B-Instruct
```

To follow logs immediately during boot:

```powershell
.\logs.cmd
```

## Change the model

The easiest way is:

```powershell
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
```

That command updates `.env`, derives a `SERVED_MODEL_NAME`, and applies the change to the running stack.

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

You can also point `MODEL_ID` to a local folder mounted under `/models`, for example:

```dotenv
MODEL_ID=/models/my-local-model
SERVED_MODEL_NAME=my-local-model
```

Put that model into `./models/my-local-model`.

Examples:

```powershell
.\use-model.cmd Qwen/Qwen2.5-Coder-14B-Instruct-AWQ
.\use-model.cmd meta-llama/Llama-3.2-3B-Instruct
.\use-model.cmd deepseek-ai/DeepSeek-R1-Distill-Qwen-7B
.\use-model.cmd /models/my-local-model
.\use-model.cmd Qwen/Qwen2.5-VL-7B-Instruct -TrustRemoteCode
.\use-model.cmd meta-llama/Llama-3.2-3B-Instruct -HFToken hf_xxx
.\use-model.cmd Qwen/Qwen2.5-7B-Instruct -NoRestart
```

## Gated and custom models

If a model is gated on Hugging Face, set:

```dotenv
HF_TOKEN=hf_xxx
```

If a model needs remote code:

```dotenv
TRUST_REMOTE_CODE=true
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

## Browser UI

Open WebUI is available at:

```text
http://localhost:3010
```

On first launch, create your admin account in the browser. The UI is preconfigured to use your local `vLLM` endpoint, and the served model should already appear in the selector.

If you later change `MODEL_ID` or `SERVED_MODEL_NAME` in `.env`, restart the stack:

```powershell
.\stop.cmd
.\start.cmd
```

Open WebUI stores some settings persistently after first boot. If you ever want a completely fresh UI setup, stop the stack and remove `./data/open-webui`.

### Python example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="local-vllm-key",
)

response = client.chat.completions.create(
    model="qwen2.5-0.5b-instruct",
    messages=[{"role": "user", "content": "Say hello from local vLLM."}],
)

print(response.choices[0].message.content)
```

## Recommended model sizes for this GPU

Your RTX 5080 has 16 GB VRAM, so these are practical starting points:

- `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ` as the default coding-focused choice
- `Qwen/Qwen2.5-7B-Instruct`
- `meta-llama/Llama-3.2-3B-Instruct`
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`
- `Qwen/Qwen2.5-14B-Instruct-AWQ` if you specifically want quantized weights

The project now defaults to `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`, because it is a stronger coding model while still being a realistic fit for a 16 GB GPU thanks to AWQ quantization.

On Windows + WSL2, some VRAM is typically occupied by the desktop, so the default `GPU_MEMORY_UTILIZATION` is intentionally conservative. If startup fails with a free-memory error, lower it further, for example to `0.78`. If you want to squeeze in larger models later, raise it gradually after you confirm stable boots.

## Notes

- `vLLM` is best for open-weight models, not proprietary cloud-only models
- Not every Hugging Face model is guaranteed, but `vLLM` can also run many models through the Transformers backend
- If a bigger model fails on memory, reduce `MAX_MODEL_LEN` or choose a smaller or quantized checkpoint
