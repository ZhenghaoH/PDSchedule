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

# 1. 生成容器内部的 Controller 启动器
cat << 'EOF' > .internal_script.sh
#!/bin/bash
MODE=$1

wait_for_loading() {
    SECONDS=$1
    NAME=$2
    echo -e "\033[33m>>> [$NAME] 检测到前序服务正在启动..."
    echo -e ">>> 将暂停 $SECONDS 秒...\033[0m"

    for ((i=SECONDS; i>0; i--)); do
        echo -ne "正在倒计时: $i 秒   \r"
        sleep 1
    done
    echo -e "\n>>> 等待结束，开始启动服务！"
}

case "$MODE" in
    "s")
        wait_for_loading 10 "Scheduler"

        echo -e "\033[31m=== [3/7] 启动 Scheduler ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        export CUDA_VISIBLE_DEVICES=4
        python3 controller.py
        ;;
esac
echo "--------------------------------------------------------"
exec /bin/bash
EOF

# 2. 注入脚本到容器
chmod +x .internal_script.sh
echo "$SUDO_PASS" | sudo -S cp .internal_script.sh temp_script.sh 2>/dev/null
echo "$SUDO_PASS" | sudo -S docker cp .internal_script.sh $CONTAINER:/usr/local/bin/run_flexpd_controller
rm .internal_script.sh

# 3. 启动 Controller 标签页
launch_tab() {
    TITLE=$1
    ARG=$2
    echo "正在打开标签页: $TITLE ..."

    CMD="echo '$SUDO_PASS' | sudo -S bash -c \"docker exec -it $CONTAINER /bin/bash -c '/usr/local/bin/run_flexpd_controller $ARG' < /dev/tty\"; exec bash"

    gnome-terminal --tab --title="$TITLE" -- bash -c "$CMD"
}

launch_tab "3.Controller" "s"
sleep 0.5

echo ">>> Controller 服务窗口已打开。"
unset SUDO_PASS
