import os
import sys
import threading
import time
from typing import Any, Dict, Optional

from core.schemas.cmd import BaseCommand
from store.gateway.manager import GatewayStoreManager


if sys.platform == "win32":
    try:
        from pyreadline import Readline

        readline = Readline()
    except ImportError:
        readline = None
else:
    try:
        import readline
    except ImportError:
        readline = None


PREFILL_STRATEGIES = ("slo_aware_prefill", "jsq", "round_robin")
DECODE_STRATEGIES = (
    "round_robin",
    "lowest_generate_throughput",
    "slo_tpot_admission",
    "distserve",
)
COMMON_COMPLETION_WORDS = (
    "history",
    "quit",
    "exit",
    "clear",
    "cls",
    "show",
    "switch",
    "scheduler",
)


def _strategies(role: str):
    return PREFILL_STRATEGIES if role == "prefill" else DECODE_STRATEGIES


def gateway_completion_words(role: str):
    """Return stable, role-specific completion words without duplicates."""
    return tuple(dict.fromkeys((*COMMON_COMPLETION_WORDS, *_strategies(role))))


def build_gateway_completer(role: str):
    words = gateway_completion_words(role)

    def completer(text: str, state: int):
        options = [word for word in words if word.startswith(text)]
        return options[state] if state < len(options) else None

    return completer


def configure_gateway_readline(role: str, readline_module=None) -> bool:
    """Enable Tab completion when readline/pyreadline is available."""
    target = readline if readline_module is None else readline_module
    if target is None:
        return False
    try:
        target.set_completer(build_gateway_completer(role))
        target.parse_and_bind("tab: complete")
        return True
    except Exception as exc:
        print(f"Warning: gateway CLI auto-completion is unavailable: {exc}")
        return False


def get_gateway_scheduler(
    role: str,
    storage: GatewayStoreManager,
    request_batcher: Optional[Any] = None,
) -> str:
    if role == "prefill":
        if request_batcher is not None and request_batcher.event_loop is not None:
            return request_batcher.scheduler_mgr.strategy
        return storage.get_prefill_scheduler_strategy()
    return storage.get_decode_scheduler_strategy()


def switch_gateway_scheduler(
    role: str,
    mode: str,
    storage: GatewayStoreManager,
    request_batcher: Optional[Any] = None,
) -> Dict[str, Any]:
    if mode not in _strategies(role):
        raise ValueError(f"Unsupported {role} scheduler strategy: {mode}")

    if role == "prefill":
        if request_batcher is None:
            raise RuntimeError("RequestBatcher is unavailable")
        return request_batcher.switch_strategy_threadsafe(mode)

    previous = storage.set_decode_scheduler_strategy(mode)
    return {
        "previous": previous,
        "current": mode,
        "pending_count": None,
        "adaptive_switch": False,
    }


def _print_help(role: str) -> None:
    available = ", ".join(_strategies(role))
    print(f"  - show scheduler             : show current {role} scheduler")
    print(f"  - switch scheduler -m <mode>: switch scheduler ({available})")
    print("  - history                    : show command history")
    print("  - clear / cls                : clear the screen")
    print("  - quit / exit                : stop the gateway")


def run_gateway_cli(
    role: str,
    storage: GatewayStoreManager,
    server: Any,
    request_batcher: Optional[Any] = None,
) -> None:
    history = []
    available = ", ".join(_strategies(role))
    prompt = f"{role}-gateway-cli> "
    completion_enabled = configure_gateway_readline(role)

    print("=" * 60)
    print(f"[CLI] {role.title()} gateway CLI ready. Commands:")
    _print_help(role)
    if completion_enabled:
        print("Tip: Use ↑/↓ for history, Tab for auto-completion")
    print("=" * 60)

    while True:
        try:
            raw_command = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raw_command = "quit"

        if raw_command:
            history.append(raw_command)
            if readline:
                readline.add_history(raw_command)
        command = BaseCommand.build_cmd(raw_command)

        if command == BaseCommand.build_cmd(""):
            continue
        if command == BaseCommand("history"):
            for index, item in enumerate(history):
                print(f"[{index}] {item}")
        elif command == BaseCommand("clear") or command == BaseCommand("cls"):
            os.system("cls" if os.name == "nt" else "clear")
        elif command == BaseCommand("quit") or command == BaseCommand("exit"):
            print(f"[CLI] Stopping {role} gateway...")
            server.should_exit = True
            break
        elif command == BaseCommand("show", "scheduler"):
            current = get_gateway_scheduler(role, storage, request_batcher)
            print(f"[CLI] role={role}, scheduler={current}, available={available}")
        elif command == BaseCommand("switch", "scheduler"):
            mode = command.get("m")
            if not mode:
                print("[CLI] Missing required parameter: -m <mode>")
                print(f"[CLI] Available modes: {available}")
                continue
            try:
                result = switch_gateway_scheduler(
                    role,
                    str(mode),
                    storage,
                    request_batcher,
                )
            except (RuntimeError, ValueError, TimeoutError) as exc:
                print(f"[CLI] Scheduler switch failed: {exc}")
                continue

            message = (
                f"[CLI] Scheduler switched: {result['previous']} -> {result['current']}"
            )
            if result["pending_count"] is not None:
                message += f", pending requests rescheduled={result['pending_count']}"
            print(message)
            if result["adaptive_switch"]:
                print("[CLI] Warning: adaptive switching is enabled and may change the strategy later.")
        else:
            print(f"[CLI] Unknown command: {command}")
            _print_help(role)


def run_gateway_server(
    app: Any,
    host: str,
    port: int,
    role: str,
    storage: GatewayStoreManager,
    no_cli: bool,
    request_batcher: Optional[Any] = None,
) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    if no_cli:
        server.run()
        return

    server_thread = threading.Thread(target=server.run, name=f"{role}-gateway-http")
    server_thread.start()
    deadline = time.time() + 30.0
    while server_thread.is_alive() and not server.started and time.time() < deadline:
        time.sleep(0.05)

    if not server_thread.is_alive():
        raise RuntimeError(f"{role.title()} gateway HTTP server failed to start")

    try:
        run_gateway_cli(role, storage, server, request_batcher)
    finally:
        server.should_exit = True
        server_thread.join()
