#!/usr/bin/env bash
set -euo pipefail

bool_true() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

args=(
  --host 0.0.0.0
  --port 8000
  --api-key "${API_KEY:-local-vllm-key}"
  --dtype "${DTYPE:-auto}"
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-1}"
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.82}"
  --max-model-len "${MAX_MODEL_LEN:-4096}"
  --max-num-seqs "${MAX_NUM_SEQS:-8}"
)

if [[ -n "${SERVED_MODEL_NAME:-}" ]]; then
  args+=(--served-model-name "${SERVED_MODEL_NAME}")
fi

if bool_true "${TRUST_REMOTE_CODE:-false}"; then
  args+=(--trust-remote-code)
fi

if bool_true "${ENABLE_PREFIX_CACHING:-true}"; then
  args+=(--enable-prefix-caching)
fi

if [[ "${CPU_OFFLOAD_GB:-0}" != "0" ]]; then
  args+=(--cpu-offload-gb "${CPU_OFFLOAD_GB}")
fi

if [[ -n "${EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  extra_args=( ${EXTRA_ARGS} )
  args+=("${extra_args[@]}")
fi

echo "Starting vLLM"
echo "  MODEL_ID=${MODEL_ID:?MODEL_ID is required}"
echo "  SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-$MODEL_ID}"
echo "  GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.82}"

exec vllm serve "${MODEL_ID}" "${args[@]}"
