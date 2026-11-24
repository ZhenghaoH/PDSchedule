#!/usr/bin/env bash
# Start one component of the two-node Ascend 910B Mooncake P2P deployment.
#
#   bash examples/ascend/start.sh <node> <role>
#
#   node  s1 | s2                       which machine this shell runs on
#   role  vllm-p | vllm-d               vLLM engine (MooncakeConnectorV1)
#         controller                    Controller, request entry point
#         gateway-p | gateway-d         Gateway for that pool
#         instance-p | instance-d       Instance proxy for that pool
#
# Start order is vLLM on both nodes, then Controller, then both Gateways,
# then both Instance proxies. Every role waits for the dependency it needs.
#
# All four entry points pass --connector-name mooncake_p2p_ascend explicitly;
# nothing here relies on the CLI default, which stays "nixl".

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${ASCEND_CONFIG:-$HERE/config.env}"

usage() {
    cat <<'USAGE'
usage: bash examples/ascend/start.sh <node> <role>

  node  s1 | s2
  role  vllm-p | vllm-d | controller | gateway-p | gateway-d
        | instance-p | instance-d

Copy config.example.env to config.env and edit it first.
USAGE
}

if [ ! -f "$CONFIG" ]; then
    echo "config not found: $CONFIG" >&2
    echo "cp $HERE/config.example.env $CONFIG   # then edit it" >&2
    exit 2
fi

# shellcheck disable=SC1090
. "$CONFIG"

NODE="${1:-}"
ROLE="${2:-}"

case "$NODE" in
    s1) HOST_IP="${S1_HOST:-}"; NPU="${S1_NPU:-0}" ;;
    s2) HOST_IP="${S2_HOST:-}"; NPU="${S2_NPU:-0}" ;;
    *) usage; exit 2 ;;
esac

if [ -z "${HOST_IP:-}" ]; then
    echo "node $NODE has no address configured (S1_HOST / S2_HOST)" >&2
    exit 2
fi

need() {
    local name="$1" value="${2:-}"
    if [ -z "$value" ]; then
        echo "missing required config value: $name" >&2
        exit 2
    fi
}

need REPO "${REPO:-}"
need MODEL_PATH "${MODEL_PATH:-}"
need MODEL_NAME "${MODEL_NAME:-}"
need LOG_DIR "${LOG_DIR:-}"
need CONNECTOR_NAME "${CONNECTOR_NAME:-}"

if [ "$CONNECTOR_NAME" != "mooncake_p2p_ascend" ]; then
    echo "note: CONNECTOR_NAME is '$CONNECTOR_NAME'; this example documents mooncake_p2p_ascend" >&2
fi

# --- helpers -------------------------------------------------------------

# Exports every value the component may read, so that both the current shell
# and `docker exec` see the same configuration.
build_exports() {
    local pair
    for pair in \
        "HOST_IP=${HOST_IP}" \
        "NPU=${NPU}" \
        "REPO=${REPO}" \
        "MODEL_PATH=${MODEL_PATH}" \
        "MODEL_NAME=${MODEL_NAME}" \
        "LOG_DIR=${LOG_DIR}" \
        "CANN_SET_ENV=${CANN_SET_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}" \
        "CONTROLLER_HOST=${CONTROLLER_HOST}" \
        "CONTROLLER_PORT=${CONTROLLER_PORT}" \
        "GATEWAY_P_PORT=${GATEWAY_P_PORT}" \
        "GATEWAY_D_PORT=${GATEWAY_D_PORT}" \
        "INSTANCE_P_PORT=${INSTANCE_P_PORT}" \
        "INSTANCE_D_PORT=${INSTANCE_D_PORT}" \
        "VLLM_P_PORT=${VLLM_P_PORT}" \
        "VLLM_D_PORT=${VLLM_D_PORT}" \
        "TP_SIZE=${TP_SIZE}" \
        "MAX_MODEL_LEN=${MAX_MODEL_LEN}" \
        "MAX_BATCHED_TOKENS=${MAX_BATCHED_TOKENS}" \
        "GPU_UTIL=${GPU_UTIL}" \
        "CONNECTOR_NAME=${CONNECTOR_NAME}" \
        "CONNECTOR_VERSION=${CONNECTOR_VERSION}" \
        "S2_HOST=${S2_HOST}" \
        "MY_GPU_TYPE=${MY_GPU_TYPE:-Ascend 910B}" \
        "MY_FLOPS=${MY_FLOPS:-280}" \
        "MY_MAX_PROMPT_THROUGHPUT=${MY_MAX_PROMPT_THROUGHPUT:-37829}" \
        "MY_MEM_BANDWIDTH=${MY_MEM_BANDWIDTH:-800}" \
        "MY_MAX_DECODE_REQ_NUM=${MY_MAX_DECODE_REQ_NUM:-128}"
    do
        printf 'export %s=%q\n' "${pair%%=*}" "${pair#*=}"
    done
}

run_in_env() {
    local command="$1" exports
    exports="$(build_exports)"
    if [ -n "${CONTAINER_NAME:-}" ]; then
        docker exec -i "$CONTAINER_NAME" bash -lc "set -eo pipefail
${exports}
${command}"
    else
        bash -lc "set -eo pipefail
${exports}
${command}"
    fi
}

wait_vllm() {
    local host="$1" port="$2" name="$3" tries="${4:-150}" i
    echo ">>> waiting for ${name} on ${host}:${port}"
    for ((i = 0; i < tries; i++)); do
        if curl -fsS "http://${host}:${port}/v1/models" >/dev/null 2>&1; then
            echo ">>> ${name} is ready"
            return 0
        fi
        sleep 2
    done
    echo ">>> ${name} did not become ready in $((tries * 2))s" >&2
    return 1
}

wait_tcp() {
    local host="$1" port="$2" name="$3" tries="${4:-150}" i
    echo ">>> waiting for ${name} on ${host}:${port}"
    for ((i = 0; i < tries; i++)); do
        if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
            exec 3<&- 3>&-
            echo ">>> ${name} is reachable"
            return 0
        fi
        sleep 2
    done
    echo ">>> ${name} not reachable in $((tries * 2))s" >&2
    return 1
}

instance_metadata() {
    printf '{"side_channel_host":"%s","side_channel_port":%s,"tp_size":%s}' \
        "$HOST_IP" "$1" "${TP_SIZE:-1}"
}

vllm_command() {
    local kv_role="$1" port="$2" kv_port="$3" log_name="$4" kv_config
    kv_config="$(printf '{"kv_connector":"MooncakeConnectorV1","kv_role":"%s","kv_port":%s,"kv_connector_extra_config":{"prefill":{"dp_size":1,"tp_size":%s},"decode":{"dp_size":1,"tp_size":%s},"svd_enabled":false}}' \
        "$kv_role" "$kv_port" "${TP_SIZE:-1}" "${TP_SIZE:-1}")"

    cat <<EOF
mkdir -p "\$LOG_DIR"
source "\$CANN_SET_ENV"
export VLLM_TARGET_DEVICE=npu
export ASCEND_RT_VISIBLE_DEVICES="\$NPU"
export VLLM_HOST_IP="\$HOST_IP"
export HCCL_IF_IP="\$HOST_IP"
export PYTHONHASHSEED=0
if [ -d /usr/local/lib64/python3.12/site-packages/mooncake ]; then
    export LD_LIBRARY_PATH="/usr/local/lib64/python3.12/site-packages/mooncake:\${LD_LIBRARY_PATH:-}"
fi
unset ASCEND_VISIBLE_DEVICES CUDA_VISIBLE_DEVICES DEVICE_ID RANK_ID LOCAL_RANK
# Start vLLM outside the repository: a same-named package directory in the
# working tree would shadow the installed one.
cd "\${TMPDIR:-/tmp}" || exit 1
python3 -m vllm.entrypoints.openai.api_server \\
    --model "\$MODEL_PATH" \\
    --served-model-name "\$MODEL_NAME" "\$MODEL_PATH" \\
    --host 0.0.0.0 --port ${port} \\
    --trust-remote-code --enforce-eager --no-enable-prefix-caching \\
    --tensor-parallel-size "\$TP_SIZE" --block-size 128 \\
    --max-model-len "\$MAX_MODEL_LEN" \\
    --max-num-batched-tokens "\$MAX_BATCHED_TOKENS" \\
    --gpu-memory-utilization "\$GPU_UTIL" \\
    --disable-uvicorn-access-log \\
    --kv-transfer-config '${kv_config}' \\
    2>&1 | tee -a "\$LOG_DIR/${log_name}"
EOF
}

controller_command() {
    cat <<'EOF'
mkdir -p "$LOG_DIR"
export PDSCHEDULE_MODEL_PATH="$MODEL_PATH"
export PDSCHEDULE_MODEL_NAME="$MODEL_NAME"
export PDSCHEDULE_TOKENIZER_PATH="$MODEL_PATH"
cd "$REPO" || exit 1
python3 -m entrypoints.controller \
    --host "$CONTROLLER_HOST" --port "$CONTROLLER_PORT" --no-cli \
    2>&1 | tee -a "$LOG_DIR/controller.log"
EOF
}

gateway_p_command() {
    cat <<'EOF'
mkdir -p "$LOG_DIR"
export MY_GPU_TYPE MY_FLOPS MY_MAX_PROMPT_THROUGHPUT
cd "$REPO" || exit 1
python3 -m entrypoints.gatewayP \
    --host "$HOST_IP" --port "$GATEWAY_P_PORT" \
    --controller "$CONTROLLER_HOST:$CONTROLLER_PORT" \
    --prefill "$HOST_IP:$INSTANCE_P_PORT" \
    --decode "$S2_HOST:$GATEWAY_D_PORT" \
    --prefill-scheduler-strategy round_robin --no-ttft-warmup \
    --connector-name "$CONNECTOR_NAME" --connector-version "$CONNECTOR_VERSION" \
    --no-cli \
    2>&1 | tee -a "$LOG_DIR/gateway_p.log"
EOF
}

gateway_d_command() {
    cat <<'EOF'
mkdir -p "$LOG_DIR"
export MY_GPU_TYPE MY_MEM_BANDWIDTH MY_MAX_DECODE_REQ_NUM
cd "$REPO" || exit 1
python3 -m entrypoints.gatewayD \
    --host "$HOST_IP" --port "$GATEWAY_D_PORT" \
    --controller "$CONTROLLER_HOST:$CONTROLLER_PORT" \
    --decode "$HOST_IP:$INSTANCE_D_PORT" \
    --decode-scheduler-strategy round_robin --no-tpot-warmup \
    --connector-name "$CONNECTOR_NAME" --connector-version "$CONNECTOR_VERSION" \
    --no-cli \
    2>&1 | tee -a "$LOG_DIR/gateway_d.log"
EOF
}

instance_p_command() {
    cat <<EOF
mkdir -p "\$LOG_DIR"
cd "\$REPO" || exit 1
python3 -m entrypoints.instanceP \\
    --host "\$HOST_IP" --port "\$INSTANCE_P_PORT" \\
    --vllm_port "\$VLLM_P_PORT" \\
    --gateway "\$HOST_IP:\$GATEWAY_P_PORT" \\
    --gpu-indices "\$NPU" \\
    --connector-name "\$CONNECTOR_NAME" --connector-version "\$CONNECTOR_VERSION" \\
    --connector-metadata '$(instance_metadata "${KV_PORT_P}")' \\
    2>&1 | tee -a "\$LOG_DIR/instance_p.log"
EOF
}

instance_d_command() {
    cat <<EOF
mkdir -p "\$LOG_DIR"
cd "\$REPO" || exit 1
python3 -m entrypoints.instanceD \\
    --host "\$HOST_IP" --port "\$INSTANCE_D_PORT" \\
    --vllm_port "\$VLLM_D_PORT" \\
    --gateway "\$HOST_IP:\$GATEWAY_D_PORT" \\
    --gpu-indices "\$NPU" \\
    --connector-name "\$CONNECTOR_NAME" --connector-version "\$CONNECTOR_VERSION" \\
    --connector-metadata '$(instance_metadata "${KV_PORT_D}")' \\
    2>&1 | tee -a "\$LOG_DIR/instance_d.log"
EOF
}

# --- dispatch ------------------------------------------------------------

case "$ROLE" in
    vllm-p)
        run_in_env "$(vllm_command kv_producer "${VLLM_P_PORT}" "${KV_PORT_P}" vllm_p.log)"
        ;;
    vllm-d)
        run_in_env "$(vllm_command kv_consumer "${VLLM_D_PORT}" "${KV_PORT_D}" vllm_d.log)"
        ;;
    controller)
        run_in_env "$(controller_command)"
        ;;
    gateway-p)
        wait_tcp "$CONTROLLER_HOST" "$CONTROLLER_PORT" Controller || exit 1
        run_in_env "$(gateway_p_command)"
        ;;
    gateway-d)
        wait_tcp "$CONTROLLER_HOST" "$CONTROLLER_PORT" Controller || exit 1
        run_in_env "$(gateway_d_command)"
        ;;
    instance-p)
        wait_vllm "$HOST_IP" "$VLLM_P_PORT" vLLM-P || exit 1
        wait_tcp "$HOST_IP" "$GATEWAY_P_PORT" Gateway-P || exit 1
        run_in_env "$(instance_p_command)"
        ;;
    instance-d)
        wait_vllm "$HOST_IP" "$VLLM_D_PORT" vLLM-D || exit 1
        wait_tcp "$HOST_IP" "$GATEWAY_D_PORT" Gateway-D || exit 1
        run_in_env "$(instance_d_command)"
        ;;
    ""|-h|--help|help)
        usage
        exit 0
        ;;
    *)
        echo "unknown role: $ROLE" >&2
        usage
        exit 2
        ;;
esac
