import os
import sys
from fastapi import FastAPI
from core.schemas.cmd import BaseCommand
from core.commands.controller.show import do_show_pool_id
from core.commands.controller.pair import do_pair_pd


if sys.platform == 'win32':
    try:
        from pyreadline import Readline
        readline = Readline()
    except ImportError:
        print("Warning: For Windows, install 'pyreadline' for better CLI experience.")
        # 降级为普通input
        readline = None
else:
    try:
        import readline
    except ImportError:
        # Linux/macOS 没有 readline 的情况极少
        print("Warning: readline is not available.")
        readline = None

# 指令补全
completion_words = ["history", "quit", "exit", "clear", "cls", "show", "pool_id", "pair", "pd", "switch", "scheduler"]
history = []


# 配置补全和历史（如果 readline 可用）
if readline:
    try:
        # 设置补全函数
        def completer(text, state):
            options = [c for c in completion_words if c.startswith(text)]
            return options[state] if state < len(options) else None
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
    except Exception as e:
        print("Warning: readline is not available, ", e)
        readline = None


def do_quit(server: "object"):
    """执行退出命令"""
    print("[CLI] Stopping scheduler...")
    # uvicorn.Server 有 should_exit 标志，置 True 即可让其优雅退出
    if hasattr(server, "should_exit"):
        setattr(server, "should_exit", True)
        print("[CLI] set server.should_exit = True")
    else:
        print("[CLI] Warning: server has no attribute 'should_exit', "
              "无法优雅退出，请手动终止进程。")


def do_clear():
    """实现清屏的效果"""
    os.system("cls" if os.name == "nt" else "clear")


def do_history():
    """展示历史命令"""
    for i, cmd in enumerate(history):
        print(f"[{i}] {cmd}")


def info():
    """展示命令信息"""
    print("  - show pool_id         : 显示推理池ID")
    print("  - show scheduler       : 显示Controller路由模式或全局调度策略")
    print("  - pair pd -p <p> -d <d>: 配对PD池")
    print("  - switch scheduler -m <mode>: 切换全局调度策略（可用项见 show scheduler）")
    print("  - history              : 查看历史记录")
    print("  - clear / cls          : 清空屏幕")
    print("  - quit / exit          : 停止调度器并退出")


def clear_req():
    """FIXME: 演示用，清除请求显示列表"""
    from store.controller.pool_manager import PoolManager
    pool_manager = PoolManager()
    pool_manager.clear_req()


def run_scheduler_cli(app: FastAPI, server: "object") -> None:
    """
    简单交互式命令行
    参数：
      app    : FastAPI 实例（scheduler）
      server : uvicorn.Server 实例（必须有 should_exit 属性）
    """
    global history
    print("=" * 60)
    print("[CLI] Scheduler CLI ready. Commands:")
    info()
    if readline:
        print("Tip: Use ↑/↓ for history, Tab for auto-completion")
    print("=" * 60)

    while True:
        try:
            cmd = input("scheduler-cli> ").strip()
        except (EOFError, KeyboardInterrupt):
            cmd = "quit"

        if cmd:
            history.append(cmd)
            if readline:
                readline.add_history(cmd)
        cmd = BaseCommand.build_cmd(cmd)

        # 空回车
        if cmd == BaseCommand.build_cmd(""):
            continue

        # 展示历史命令
        if cmd == BaseCommand("history"):
            do_history()

        # 清屏命令
        elif cmd == BaseCommand("clear") or cmd == BaseCommand("cls"):
            do_clear()

        # 退出命令
        elif cmd == BaseCommand("quit") or cmd == BaseCommand("exit"):
            do_quit(server)
            break

        # 展示池id与池的对应关系
        elif cmd == BaseCommand("show", "pool_id"):
            do_show_pool_id()

        elif cmd == BaseCommand("show", "scheduler"):
            scheduler = getattr(app.state, "global_scheduler", None)
            if scheduler is None:
                print("[CLI] Global scheduler is not initialized.")
            else:
                print(f"[CLI] Controller routing: {scheduler.routing_status()}")

        # 实例配对指令
        elif cmd == BaseCommand("pair", "pd"):
            do_pair_pd(cmd.params)

        # 切换调度策略
        elif cmd == BaseCommand("switch", "scheduler"):
            scheduler = getattr(app.state, "global_scheduler", None)
            if scheduler is None:
                print("[CLI] Global scheduler is not initialized.")
                continue

            mode = cmd.get("m")
            if not mode:
                print("[CLI] Missing required parameter: -m <mode>")
                print(
                    "[CLI] Available modes: "
                    f"{', '.join(scheduler.available_strategies)}"
                )
                continue

            scheduler.switch_mode(str(mode))

        # FIXME: 演示用临时函数
        elif cmd == BaseCommand("clear_req"):
            clear_req()

        else:
            print(f"[CLI] Unknown command: {cmd}. Commands:")
            info()


# 测试功能
if __name__ == "__main__":
    import uvicorn
    app = FastAPI(title="test")
    server_config = uvicorn.Config(app, host="127.0.0.1", port=8080, log_level="info")  # windows
    server = uvicorn.Server(server_config)
    run_scheduler_cli(app, server)
