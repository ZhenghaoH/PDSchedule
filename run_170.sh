#!/bin/bash

# ================= 配置 =================
CONTAINER="new_env"
# =======================================

# 0. 提前询问 sudo 密码
echo ">>> 请输入 sudo 密码（输入时不可见，回车确认）："
read -s SUDO_PASS
echo ">>> 密码已捕获。"

# 简单验证密码
if ! echo "$SUDO_PASS" | sudo -S -k true 2>/dev/null; then
    echo "❌ 密码错误，退出。"
    exit 1
fi

echo ">>> 正在初始化容器环境..."

# 1. 生成容器内部的统一启动器
cat << 'EOF' > .internal_script.sh
#!/bin/bash
MODE=$1
MODEL_PATH="/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"

# 定义一个通用的倒计时函数，方便复用
wait_for_loading() {
    SECONDS=$1
    NAME=$2
    echo -e "\033[33m>>> [$NAME] 检测到前序服务正在启动..."
    echo -e ">>> 为了等待 P/D 实例加载模型，将暂停 $SECONDS 秒...\033[0m"

    for ((i=SECONDS; i>0; i--)); do
        # -ne 不换行，\r 回到行首，实现原地刷新倒计时效果
        echo -ne "正在倒计时: $i 秒   \r"
        sleep 1
    done
    echo -e "\n>>> 等待结束，开始启动服务！"
}

case "$MODE" in
    "meta")
        echo -e "\033[33m=== [0/13] 启动 Metadata Server ===\033[0m"
        mooncake_master --rpc_address 172.18.0.169 --rpc_port 50051 --metrics_port 9002 --enable_http_metadata_server=true --http_metadata_server_host=172.18.0.169 --http_metadata_server_port=8080
        ;;
    "p1")
        echo -e "\033[32m=== [1/13] 启动 Prefill vLLM 实例 1 (GPU 0,1) ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD
        export CUDA_VISIBLE_DEVICES=0,1
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.170
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5557
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8001 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=2 \
        --disable-uvicorn-access-log \
        --no-enable-prefix-caching \
        --attention-backend TRITON_ATTN
        ;;
    "p2")
        echo -e "\033[32m=== [2/13] 启动 Prefill vLLM 实例 2 (GPU 2,3) ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD
        export CUDA_VISIBLE_DEVICES=2,3
        export PYTHONHASHSEED=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.170
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5558
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8002 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=2 \
        --disable-uvicorn-access-log \
        --no-enable-prefix-caching \
        --attention-backend TRITON_ATTN
        ;;
    "d1")
        echo -e "\033[34m=== [3/13] 启动 Decode vLLM 实例 1 (GPU 4,5) ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD
        export CUDA_VISIBLE_DEVICES=4,5
        export PYTHONHASHSEED=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.170
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5559
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8003 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=2  \
        --disable-uvicorn-access-log \
        --attention-backend TRITON_ATTN
        ;;
    "d2")
        echo -e "\033[34m=== [4/13] 启动 Decode vLLM 实例 2 (GPU 6,7) ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD
        export CUDA_VISIBLE_DEVICES=6,7
        export PYTHONHASHSEED=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.170
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5560
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8004 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=2  \
        --disable-uvicorn-access-log \
        --attention-backend TRITON_ATTN
        ;;
    "s")
        wait_for_loading 10 "Scheduler"

        echo -e "\033[31m=== [5/13] 启动 Scheduler ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        export CUDA_VISIBLE_DEVICES=4
        python3 controller.py 
        ;;
    "pd")
        wait_for_loading 50 "Decode Proxy"
	    export MY_MEM_BANDWIDTH=3584
        export MY_MAX_DECODE_REQ_NUM=256
        echo -e "\033[35m=== [6/13] 启动 Decode Proxy ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 gatewayD.py --decode 172.18.0.170:7003 172.18.0.170:7004
        ;;
    "pp")
        wait_for_loading 52 "Prefill Proxy"

        echo -e "\033[36m=== [7/13] 启动 Prefill Proxy ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        export MY_MAX_PROMPT_THROUGHPUT=25499.77
        export MY_FLOPS=209.6
        python3 gatewayP.py --prefill 172.18.0.170:7001 172.18.0.170:7002
        ;;
    "id1")
        wait_for_loading 55 "Decode Instance 1"

        echo -e "\033[36m=== [8/13] 启动 Decode Instance Proxy 1 ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 instanceD.py --port 7003 --vllm_port 8003
        ;;
    "id2")
        wait_for_loading 55 "Decode Instance 2"

        echo -e "\033[36m=== [9/13] 启动 Decode Instance Proxy 2 ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 instanceD.py --port 7004 --vllm_port 8004
        ;;
    "ip1")
        wait_for_loading 57 "Prefill Instance 1"

        echo -e "\033[36m=== [10/13] 启动 Prefill Instance Proxy 1 ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 instanceP.py --port 7001 --vllm_port 8001
        ;;
    "ip2")
        wait_for_loading 57 "Prefill Instance 2"

        echo -e "\033[36m=== [11/13] 启动 Prefill Instance Proxy 2 ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 instanceP.py --port 7002 --vllm_port 8002
        ;;
    "kdn")
        echo -e "\033[36m=== [12/13] 启动 KDN Server ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 demo_kdn.py
        ;;
    "cli")
        echo -e "\033[36m=== [13/13] 启动 KDN CLI ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/
        python3 ./kdn_server/kdn_register_cli.py
        ;;
esac
echo "--------------------------------------------------------"
exec /bin/bash
EOF

# 2. 注入脚本到容器
chmod +x .internal_script.sh
echo "$SUDO_PASS" | sudo -S cp .internal_script.sh temp_script.sh 2>/dev/null
echo "$SUDO_PASS" | sudo -S docker cp .internal_script.sh $CONTAINER:/usr/local/bin/run_flexpd
rm .internal_script.sh

# 3. 逐个启动终端标签页
launch_tab() {
    TITLE=$1
    ARG=$2
    echo "正在打开标签页: $TITLE ..."

    # 强制 Docker 使用 TTY 解决 input device is not a TTY 报错
    CMD="echo '$SUDO_PASS' | sudo -S bash -c \"docker exec -it $CONTAINER /bin/bash -c '/usr/local/bin/run_flexpd $ARG' < /dev/tty\"; exec bash"

    gnome-terminal --tab --title="$TITLE" -- bash -c "$CMD"
}

# --- 快速顺序打开所有窗口 ---
# 宿主机的 sleep 只是为了让 GNOME Terminal 标签页排序整齐，不需要很久

#launch_tab "0.Meta" "meta"
sleep 1

launch_tab "1.Prefill-vLLM-1" "p1"
sleep 0.5

launch_tab "2.Prefill-vLLM-2" "p2"
sleep 0.5

launch_tab "3.Decode-vLLM-1" "d1"
sleep 0.5

launch_tab "4.Decode-vLLM-2" "d2"
sleep 0.5

launch_tab "5.Controller" "s"
sleep 0.5

launch_tab "6.Gateway-D" "pd"
sleep 0.5

launch_tab "7.Gateway-P" "pp"
sleep 0.5

launch_tab "8.Instance-D-1" "id1"
sleep 0.5

launch_tab "9.Instance-D-2" "id2"
sleep 0.5

launch_tab "10.Instance-P-1" "ip1"
sleep 0.5

launch_tab "11.Instance-P-2" "ip2"
sleep 0.5

launch_tab "12.KDN-Server" "kdn"
sleep 0.5

launch_tab "13.KDN-CLI" "cli"

echo ">>> 所有服务窗口已打开。"
echo ">>> P/D/Meta 正在启动，Scheduler 和 Proxy 将在 90 秒后自动启动。"
unset SUDO_PASS