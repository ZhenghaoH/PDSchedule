#!/usr/bin/env bash

set -uo pipefail

CONTAINER="${CONTAINER:-new_env}"
TERM_WAIT_SECONDS="${TERM_WAIT_SECONDS:-5}"
MAX_GPU_CLEANUP_PASSES="${MAX_GPU_CLEANUP_PASSES:-3}"

# 宿主机 nvidia-smi 和 docker top 返回的都是宿主机 PID。GPU 兜底清理必须
# 在宿主机侧完成，并且只能处理同时属于目标容器的 vLLM 进程。
if ((EUID != 0)); then
    exec sudo /usr/bin/env bash "$0" "$@"
fi

log() {
    printf '[stop] %s\n' "$*"
}

error() {
    printf '[stop] ERROR: %s\n' "$*" >&2
}

require_command() {
    local command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        error "Required command not found: $command_name"
        exit 1
    fi
}

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

require_command docker
require_command nvidia-smi

if [[ ! "$TERM_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    error "TERM_WAIT_SECONDS must be a positive integer."
    exit 1
fi

if [[ ! "$MAX_GPU_CLEANUP_PASSES" =~ ^[1-9][0-9]*$ ]]; then
    error "MAX_GPU_CLEANUP_PASSES must be a positive integer."
    exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    error "Container does not exist: $CONTAINER"
    exit 1
fi

if [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" != "true" ]]; then
    error "Container is not running: $CONTAINER"
    exit 1
fi

log "Stopping PDSchedule/vLLM processes in container $CONTAINER ..."

# 保留原脚本对专用运行容器内 Python、vLLM 和设备占用进程的清理能力。
# set +e 确保没有匹配进程时仍继续执行后续 GPU PID 核验。
docker exec "$CONTAINER" /bin/bash -c '
    set +e

    echo "[stop/container] Sending TERM to Python and vLLM services ..."
    pkill -TERM -f "[p]ython3"
    pkill -TERM -f "[v]llm"
    pkill -TERM -f "[m]ain.py"
    sleep 2

    echo "[stop/container] Force-stopping remaining services ..."
    pkill -KILL -f "[p]ython3"
    pkill -KILL -f "[v]llm"
    pkill -KILL -f "[m]ain.py"

    if command -v fuser >/dev/null 2>&1; then
        for device in /dev/nvidia[0-9]*; do
            [ -e "$device" ] || continue
            fuser -k -9 "$device" >/dev/null 2>&1
        done
    else
        echo "[stop/container] fuser is unavailable; skipping device-file cleanup."
    fi
' || log "Container cleanup returned a non-zero status; continuing with GPU PID verification."

declare -a MATCHED_PIDS=()
declare -a MATCHED_NAMES=()

scan_container_vllm_gpu_processes() {
    local container_processes
    local gpu_processes
    local pid
    local process_name
    declare -A container_pid_set=()

    MATCHED_PIDS=()
    MATCHED_NAMES=()

    if ! container_processes="$(docker top "$CONTAINER" -eo pid 2>/dev/null)"; then
        error "Unable to read the process list for container $CONTAINER."
        return 1
    fi

    while IFS= read -r pid; do
        pid="$(trim "$pid")"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        container_pid_set["$pid"]=1
    done <<< "$container_processes"

    if ! gpu_processes="$(
        nvidia-smi \
            --query-compute-apps=pid,process_name \
            --format=csv,noheader,nounits 2>/dev/null
    )"; then
        error "Unable to query NVIDIA compute processes."
        return 1
    fi

    while IFS=',' read -r pid process_name; do
        pid="$(trim "${pid:-}")"
        process_name="$(trim "${process_name:-}")"

        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        [[ "${process_name,,}" == *vllm* ]] || continue
        [[ -n "${container_pid_set[$pid]:-}" ]] || continue

        MATCHED_PIDS+=("$pid")
        MATCHED_NAMES+=("$process_name")
    done <<< "$gpu_processes"
}

terminate_matched_processes() {
    local index
    local second
    local pid
    local -a alive_pids=()

    for index in "${!MATCHED_PIDS[@]}"; do
        log "Sending TERM to GPU process pid=${MATCHED_PIDS[$index]} name=${MATCHED_NAMES[$index]}"
    done
    kill -TERM "${MATCHED_PIDS[@]}" 2>/dev/null || true

    for ((second = 0; second < TERM_WAIT_SECONDS; second++)); do
        alive_pids=()
        for pid in "${MATCHED_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive_pids+=("$pid")
            fi
        done
        ((${#alive_pids[@]} == 0)) && return 0
        sleep 1
    done

    log "TERM timeout reached; sending KILL to remaining PIDs: ${alive_pids[*]}"
    kill -KILL "${alive_pids[@]}" 2>/dev/null || true
    sleep 1
}

for ((pass = 1; pass <= MAX_GPU_CLEANUP_PASSES; pass++)); do
    if ! scan_container_vllm_gpu_processes; then
        exit 1
    fi

    if ((${#MATCHED_PIDS[@]} == 0)); then
        log "No vLLM GPU process remains in container $CONTAINER."
        log "Final NVIDIA status:"
        nvidia-smi
        exit 0
    fi

    log "GPU cleanup pass $pass/$MAX_GPU_CLEANUP_PASSES found ${#MATCHED_PIDS[@]} vLLM process(es)."
    terminate_matched_processes
done

if ! scan_container_vllm_gpu_processes; then
    exit 1
fi

if ((${#MATCHED_PIDS[@]} > 0)); then
    error "vLLM GPU processes still remain in container $CONTAINER after cleanup:"
    for index in "${!MATCHED_PIDS[@]}"; do
        printf '  pid=%s name=%s\n' "${MATCHED_PIDS[$index]}" "${MATCHED_NAMES[$index]}" >&2
    done
    nvidia-smi
    exit 1
fi

log "All vLLM GPU processes in container $CONTAINER were stopped."
log "Final NVIDIA status:"
nvidia-smi
