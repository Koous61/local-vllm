# Local vLLM on Windows + Docker

This project gives you a local `vLLM` server behind an OpenAI-compatible API, so you can point external tools at `http://localhost:8000/v1` and swap models through `.env`.

## What you get

- GPU-backed `vLLM` through Docker Desktop and WSL2
- OpenAI-compatible endpoint for chat/completions and related APIs
- Persistent Hugging Face cache in `./data/hf-cache`
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

## Main files

- `.env` controls the model and server settings
- `docker-compose.yml` runs the GPU container
- `scripts/start.ps1` starts the service and waits for readiness
- `scripts/logs.ps1` tails container logs
- `scripts/test-chat.ps1` sends a real chat request to the local API
- `start.cmd`, `stop.cmd`, `logs.cmd`, `test-chat.cmd` avoid PowerShell execution-policy friction on Windows

## Useful commands

```powershell
.\start.cmd
.\logs.cmd
.\test-chat.cmd
.\stop.cmd
```

To follow logs immediately during boot:

```powershell
.\logs.cmd
```

## Change the model

Edit `.env` and update:

```dotenv
MODEL_ID=Qwen/Qwen2.5-7B-Instruct
SERVED_MODEL_NAME=qwen2.5-7b-instruct
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

- `Qwen/Qwen2.5-7B-Instruct`
- `meta-llama/Llama-3.2-3B-Instruct`
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`
- `Qwen/Qwen2.5-14B-Instruct-AWQ` if you specifically want quantized weights

For the first launch, the project defaults to a smaller model only to validate the stack quickly.

On Windows + WSL2, some VRAM is typically occupied by the desktop, so the default `GPU_MEMORY_UTILIZATION` is intentionally conservative. If startup fails with a free-memory error, lower it further, for example to `0.78`. If you want to squeeze in larger models later, raise it gradually after you confirm stable boots.

## Notes

- `vLLM` is best for open-weight models, not proprietary cloud-only models
- Not every Hugging Face model is guaranteed, but `vLLM` can also run many models through the Transformers backend
- If a bigger model fails on memory, reduce `MAX_MODEL_LEN` or choose a smaller or quantized checkpoint
