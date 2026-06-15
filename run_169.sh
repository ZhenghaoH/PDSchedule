#!/bin/bash

# ================= 配置 =================
CONTAINER="test"
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
CONFIG_PATH="/workspace/llm-stack/workspace_HZH/FlexPD/lmcache.yaml"
MODEL_PATH="/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
REDIS_URL="redis://172.18.0.170:6378"

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
        echo -e "\033[33m=== [0/9] 启动 Metadata Server ===\033[0m"
        mooncake_master --rpc_address 172.18.0.169 --rpc_port 50051 --metrics_port 9002 --enable_http_metadata_server=true --http_metadata_server_host=172.18.0.169 --http_metadata_server_port=8080
        ;;
    "p1")
        echo -e "\033[32m=== [1/9] 启动 Prefill 实例 1 ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD
        export CUDA_VISIBLE_DEVICES=0,1
        export PYTHONHASHSEED=0
        export LMCACHE_LOCAL_CPU="False"
        export LMCACHE_REMOTE_URL=$REDIS_URL
        unset LMCACHE_REMOTE_SERDE
        export LMCACHE_CONFIG_FILE=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8001 \
        --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_producer","kv_connector_extra_config":{"discard_partial_chunks":false}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 10240 \
        --kv-offloading-backend lmcache \
        --kv-offloading-size 64 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=2 \
        --no-enable-prefix-caching  \
        --disable-uvicorn-access-log
        ;;
    "p2")
        echo -e "\033[34m=== [2/9] 启动 Prefill 实例 2 ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD
        export CUDA_VISIBLE_DEVICES=6,7
        export PYTHONHASHSEED=0
        export LMCACHE_REMOTE_URL=$REDIS_URL
        unset LMCACHE_REMOTE_SERDE
        export LMCACHE_CONFIG_FILE=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8002 \
        --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_producer","kv_connector_extra_config":{"discard_partial_chunks":false}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 10240 \
        --kv-offloading-backend lmcache \
        --kv-offloading-size 64 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=2  \
        --disable-uvicorn-access-log
        ;;
    "s")
        wait_for_loading 10 "Scheduler"

        echo -e "\033[31m=== [3/9] 启动 Scheduler ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        export CUDA_VISIBLE_DEVICES=4
        python3 controller.py
        ;;
    "pd")
        wait_for_loading 40 "Decode Proxy"
	    export MY_MEM_BANDWIDTH=1792
        echo -e "\033[35m=== [4/9] 启动 Decode Proxy ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 gatewayD.py
        ;;
    "pp")
        wait_for_loading 42 "Prefill Proxy"

        echo -e "\033[36m=== [5/9] 启动 Prefill Proxy ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        export MY_FLOPS=209.6
        python3 gatewayP.py
        ;;
    "id")
        wait_for_loading 45 "Decode Instance"

        echo -e "\033[36m=== [6/9] 启动 Decode Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceD.py 
        ;;
    "ip1")
        wait_for_loading 47 "Prefill Instance"

        echo -e "\033[36m=== [7/9] 启动 Prefill Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceP.py --port 7001 --vllm_port 8001 --gateway
        ;;
    "ip2")
        wait_for_loading 47 "Prefill Instance"

        echo -e "\033[36m=== [8/9] 启动 Prefill Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceP.py --port 7002 --vllm_port 8002
        ;;
    "kdn")
        echo -e "\033[36m=== [8/9] 启动 KDN Server ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 demo_kdn.py
        ;;
    "cli")
        echo -e "\033[36m=== [9/9] 启动 KDN Server ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/
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

launch_tab "1.Prefill 1" "p1"
sleep 0.5

launch_tab "2.Prefill 2" "p2"
sleep 0.5

# launch_tab "3.Controller" "s"
# sleep 0.5

# launch_tab "4.Gateway-D" "pd"
# sleep 0.5

# launch_tab "5.Gateway-P" "pp"
# sleep 0.5

launch_tab "Instance-P 1" "ip1"
sleep 0.5

launch_tab "Instance-P 2" "ip2"
sleep 0.5
# launch_tab "7.Instance-D" "id"
# sleep 0.5

# launch_tab "8.KDN-Server" "kdn"
# sleep 0.5

# launch_tab "9.KDN-CLI" "cli"

echo ">>> 所有服务窗口已打开。"
echo ">>> P/D/Meta 正在启动，Scheduler 和 Proxy 将在 90 秒后自动启动。"
unset SUDO_PASS