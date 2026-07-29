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
export FORCE_STOP_ON_UPDATE=0
export VLLM_USE_V1=0
export VLLM_USE_MODELSCOPE=True
CONFIG_PATH="/workspace/workspace/mooncake/mooncake.json"
MODEL_PATH="/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"

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
    "p")
        echo -e "\033[32m=== [1/8] 启动 Prefill 实例 (GPU 0) ===\033[0m"
        cd /workspace/vllm
        export HTTP_IP=127.0.0.1:8101
        export CUDA_VISIBLE_DEVICES=0
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server --host 127.0.0.1 --port 8001 --model $MODEL_PATH --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}'
        ;;
    "d")
        echo -e "\033[34m=== [2/8] 启动 Decode 实例 (GPU 1) ===\033[0m"
        cd /workspace/vllm
        export HTTP_IP=127.0.0.1:8102
        export CUDA_VISIBLE_DEVICES=1
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server --host 127.0.0.1 --port 8002 --model $MODEL_PATH --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}'
        ;;
    "meta")
        echo -e "\033[33m=== [3/8] 启动 Metadata Server ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 http_server.py
        ;;
    "s")
        # === 核心修改：Scheduler 加入 等待 ===
        # wait_for_loading 90 "Scheduler"

        echo -e "\033[31m=== [4/8] 启动 Scheduler ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 controller.py
        ;;
    "pd")
        # === 核心修改：Decode Proxy 加入 等待 (多等2秒确保调度器先一步) ===
        wait_for_loading 40 "Decode Proxy"

        echo -e "\033[35m=== [5/8] 启动 Decode Proxy ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 gatewayD.py
        ;;
    "pp")
        # === 核心修改：Prefill Proxy 加入 等待 ===
        wait_for_loading 42 "Prefill Proxy"

        echo -e "\033[36m=== [6/8] 启动 Prefill Proxy ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 gatewayP.py
        ;;
    "id")
        # === 核心修改：Decode Instance 加入 等待 ===
        wait_for_loading 45 "Decode Instance"

        echo -e "\033[36m=== [7/8] 启动 Decode Instance ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 instanceD.py
        ;;
    "ip")
        # === 核心修改：Prefill Instance 加入 等待 ===
        wait_for_loading 47 "Prefill Instance"

        echo -e "\033[36m=== [8/8] 启动 Prefill Instance ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 instanceP.py
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

launch_tab "1.Prefill" "p"
sleep 1

launch_tab "2.Decode" "d"
sleep 0.5

launch_tab "3.Meta" "meta"
sleep 0.5

launch_tab "4.Sched" "s"
sleep 0.5

launch_tab "5.Proxy-D" "pd"
sleep 0.5

launch_tab "6.Proxy-P" "pp"
sleep 0.5

launch_tab "7.Instance-P" "ip"
sleep 0.5

launch_tab "8.Instance-D" "id"
sleep 0.5

echo ">>> 所有服务窗口已打开。"
echo ">>> P/D/Meta 正在启动，Scheduler 和 Proxy 将在 90 秒后自动启动。"
unset SUDO_PASS
