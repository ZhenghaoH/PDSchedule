import argparse
import os
import shlex
import requests
import sys
from pathlib import Path

"""
KDN 统一注册/构建工具（Text + KV）

HTTP endpoints:
- POST /knowledge/register_text
- POST /knowledge/build_kv

交互模式:
- 单行：粘贴后回车 -> register_text
- :file <path> -> register_text (file content)
- :buildkv <kid> [--api-url ... --model ... --max-tokens ... --redis-host ...]
- :buildkv_file <path> [kv args...] -> 先 register_text(file) 再 build_kv(kid)

命令行模式:
- --file <path>                 只注册文本
- --build-kv-kid <kid>          只触发 build_kv
- --build-kv-file <path>        先注册文本再 build_kv（推荐）
"""

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))


from config.kdn import *


def register_text(base_url: str, content: str, timeout_s: int = 30) -> dict:
    r = requests.post(
        f"{base_url.rstrip('/')}/knowledge/register_text",
        json={"content": content},
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


def build_kv(base_url: str, kid: str, kv_args: dict, timeout_s: int = 600) -> dict:
    payload = {"kid": kid}
    payload.update(kv_args)
    r = requests.post(
        f"{base_url.rstrip('/')}/knowledge/build_kv",
        json=payload,
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


def query_kid_status(base_url: str, kid: str, timeout_s: int = 15) -> dict:
    # 尽量使用 need_fields，避免 content/embedding 太大
    payload = {
        "knowledge_ids": [kid],
        "need_fields": [
            "rel_path",
            "length",
            "embed_dim",
            "kv_ready",
            "kv_rel_dir",
            "kv_dumped_keys",
            "kv_updated_at",
        ],
    }
    r = requests.post(
        f"{base_url.rstrip('/')}/knowledge/search/text",
        json=payload,
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def print_text_ok(resp: dict):
    print(
        f"[TEXT OK] kid={resp.get('kid')}  "
        f"status={resp.get('status')}  "
        f"length={resp.get('length')}"
    )


def print_kv_ok(resp: dict):
    print(
        f"[KV OK] kid={resp.get('kid')}  "
        f"dumped_keys={resp.get('dumped_keys')}  "
        f"kv_dir={resp.get('kv_dir')}"
    )


def print_status(resp: dict, kid: str):
    items = resp.get("items") or []
    miss = resp.get("miss") or []
    if miss and kid in miss:
        print(f"[STATUS] kid={kid}  NOT_FOUND")
        return
    if not items:
        print(f"[STATUS] kid={kid}  EMPTY_RESPONSE")
        return

    it = items[0]
    # 兼容：有的实现字段名可能不同
    rel_path = it.get("rel_path") or it.get("path")
    length = it.get("length")
    import math

    embed_dim = it.get("embed_dim")
    embedding = it.get("embedding")

    has_embedding = isinstance(embedding, list) and len(embedding) > 0

    emb_head = None
    emb_norm = None
    if has_embedding:
        # 前 10 维
        emb_head = embedding[:10]
        # 简单 L2 norm（sanity check）
        try:
            emb_norm = math.sqrt(sum(x * x for x in embedding))
        except Exception:
            emb_norm = None

    kv_ready = it.get("kv_ready", 0)
    kv_rel_dir = it.get("kv_rel_dir")
    kv_dumped_keys = it.get("kv_dumped_keys")
    kv_updated_at = it.get("kv_updated_at")

    print(
        "[STATUS]\n"
        f"  kid: {kid}\n"
        f"  rel_path: {rel_path}\n"
        f"  length: {length}\n"
        f"  embedding: {'yes' if has_embedding else 'no'}"
        + (f" (dim={embed_dim})" if embed_dim is not None else "")
        + ("\n  embedding_head[0:10]: " + str(emb_head) if emb_head else "")
        + ("\n  embedding_l2_norm: " + f"{emb_norm:.4f}" if emb_norm is not None else "")
        + "\n"
          f"  kv_ready: {kv_ready}\n"
          f"  kv_rel_dir: {kv_rel_dir}\n"
          f"  kv_dumped_keys: {kv_dumped_keys}\n"
          f"  kv_updated_at: {kv_updated_at}"
    )


def parse_kv_cli_tokens(tokens: list[str]) -> dict:
    """
    解析 build_kv 的可选参数（适用于交互命令和命令行模式）
    支持：
      --api-url, --model, --max-tokens, --temperature,
      --redis-host, --redis-port, --redis-db, --redis-password,
      --match, --scan-count, --flushdb
    """
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--api-url", default=DEFAULT_API_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    ap.add_argument("--redis-host", default=DEFAULT_REDIS_HOST)
    ap.add_argument("--redis-port", type=int, default=DEFAULT_REDIS_PORT)
    ap.add_argument("--redis-db", type=int, default=DEFAULT_REDIS_DB)
    ap.add_argument("--redis-password", default=None)
    ap.add_argument("--match", default=DEFAULT_MATCH)
    ap.add_argument("--scan-count", type=int, default=DEFAULT_SCAN_COUNT)
    ap.add_argument("--flushdb", action="store_true")

    ns = ap.parse_args(tokens)
    return {
        "api_url": ns.api_url,
        "model": ns.model,
        "max_tokens": ns.max_tokens,
        "temperature": ns.temperature,
        "redis_host": ns.redis_host,
        "redis_port": ns.redis_port,
        "redis_db": ns.redis_db,
        "redis_password": ns.redis_password,
        "match": ns.match,
        "scan_count": ns.scan_count,
        "flushdb": bool(ns.flushdb),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("KDN_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--timeout", type=int, default=30, help="timeout seconds for register_text")
    ap.add_argument("--warn-len", type=int, default=DEFAULT_WARN_LEN)

    # 非交互：文本注册
    ap.add_argument("--file", help="Register text from a file, then exit")

    # 非交互：KV build
    ap.add_argument("--build-kv-kid", help="Build KV for an existing kid, then exit")
    ap.add_argument("--build-kv-file", help="Register text from file and then build KV, then exit")
    ap.add_argument("--status-kid", help="Query status for a kid, then exit")

    # build_kv 的可选参数（非交互用）
    ap.add_argument("--api-url", default=DEFAULT_API_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    ap.add_argument("--redis-host", default=DEFAULT_REDIS_HOST)
    ap.add_argument("--redis-port", type=int, default=DEFAULT_REDIS_PORT)
    ap.add_argument("--redis-db", type=int, default=DEFAULT_REDIS_DB)
    ap.add_argument("--redis-password", default=None)
    ap.add_argument("--match", default=DEFAULT_MATCH)
    ap.add_argument("--scan-count", type=int, default=DEFAULT_SCAN_COUNT)
    ap.add_argument("--flushdb", action="store_true")

    args = ap.parse_args()
    base_url = args.base_url.rstrip("/")

    kv_args_from_flags = {
        "api_url": args.api_url,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "redis_host": args.redis_host,
        "redis_port": args.redis_port,
        "redis_db": args.redis_db,
        "redis_password": args.redis_password,
        "match": args.match,
        "scan_count": args.scan_count,
        "flushdb": bool(args.flushdb),
    }

    # ========== 非交互模式 1：只注册文本 ==========
    if args.file:
        content = read_file(args.file)
        if not content.strip():
            print("[WARN] file content is empty (after stripping), skipped")
            raise SystemExit(0)
        resp = register_text(base_url, content, timeout_s=args.timeout)
        print_text_ok(resp)
        return

    # ========== 非交互模式 2：只 build kv ==========
    if args.build_kv_kid:
        kid = args.build_kv_kid.strip().lower()
        resp = build_kv(base_url, kid, kv_args_from_flags, timeout_s=max(600, args.timeout))
        print_kv_ok(resp)
        return

    # ========== 非交互模式 3：文件 -> 注册 -> build kv（最常用） ==========
    if args.build_kv_file:
        content = read_file(args.build_kv_file)
        if not content.strip():
            print("[WARN] file content is empty (after stripping), skipped")
            raise SystemExit(0)
        r1 = register_text(base_url, content, timeout_s=args.timeout)
        print_text_ok(r1)
        kid = str(r1.get("kid", "")).strip().lower()
        if not kid:
            raise SystemExit("[ERROR] register_text did not return kid")
        r2 = build_kv(base_url, kid, kv_args_from_flags, timeout_s=max(600, args.timeout))
        print_kv_ok(r2)
        return

    if args.status_kid:
        kid = args.status_kid.strip().lower()
        resp = query_kid_status(base_url, kid, timeout_s=min(60, args.timeout))
        print_status(resp, kid)
        return

    # ========== 交互模式 ==========
    print("=" * 80)
    print("KDN Register CLI (Text + KV)")
    print(f"KDN: {base_url}")
    print("")
    print("Research commands:")
    print("  :status <kid>               show knowledge block status (text/embedding/kv)")
    print("")
    print("Text commands:")
    print("  <one line>                  register_text immediately")
    print('  :file <path>                register_text from file (recommended for long text)')
    print("")
    print("KV commands:")
    print("  :buildkv <kid> [kv args...]")
    print("  :buildkv_file <path> [kv args...]   (register file -> build kv)")
    print("")
    print("KV args (optional):")
    print("  --api-url ... --model ... --max-tokens N --temperature T")
    print("  --redis-host ... --redis-port N --redis-db N --redis-password ...")
    print("  --match ... --scan-count N --flushdb")
    print("")
    print("Other:")
    print("  :quit / :exit")
    print("=" * 80)

    while True:
        try:
            line = input("[kdn] ").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            print("\n^C")
            break

        if not line:
            continue
        if line in (":quit", ":exit"):
            break

        # -------- KV: buildkv_file --------
        if line.startswith(":buildkv_file "):
            rest = line[len(":buildkv_file ") :].strip()
            # 用 shlex 支持带空格路径的引号
            tokens = shlex.split(rest)
            if not tokens:
                print("[ERROR] usage: :buildkv_file <path> [kv args...]")
                continue
            path = tokens[0]
            kv_tokens = tokens[1:]
            try:
                kv_args = parse_kv_cli_tokens(kv_tokens)
                content = read_file(path)
                if not content.strip():
                    print("[WARN] file content is empty (after stripping), skipped")
                    continue
                r1 = register_text(base_url, content, timeout_s=args.timeout)
                print_text_ok(r1)
                kid = str(r1.get("kid", "")).strip().lower()
                if not kid:
                    print("[ERROR] register_text did not return kid")
                    continue
                r2 = build_kv(base_url, kid, kv_args, timeout_s=max(600, args.timeout))
                print_kv_ok(r2)
            except Exception as e:
                print(f"[ERROR] buildkv_file failed: {e}")
            continue

        # -------- KV: buildkv --------
        if line.startswith(":buildkv "):
            rest = line[len(":buildkv ") :].strip()
            tokens = shlex.split(rest)
            if not tokens:
                print("[ERROR] usage: :buildkv <kid> [kv args...]")
                continue
            kid = tokens[0].strip().lower()
            kv_tokens = tokens[1:]
            try:
                kv_args = parse_kv_cli_tokens(kv_tokens)
                r = build_kv(base_url, kid, kv_args, timeout_s=max(600, args.timeout))
                print_kv_ok(r)
            except Exception as e:
                print(f"[ERROR] buildkv failed: {e}")
            continue

        # -------- Status --------
        if line.startswith(":status ") or line.startswith(":stat "):
            rest = line.split(None, 1)[1].strip()
            kid = rest.lower()
            try:
                resp = query_kid_status(base_url, kid, timeout_s=min(60, args.timeout))
                print_status(resp, kid)
            except Exception as e:
                print(f"[ERROR] status failed: {e}")
            continue

        # -------- Text: file --------
        if line.startswith(":file "):
            path = line[len(":file ") :].strip().strip('"').strip("'")
            try:
                content = read_file(path)
            except Exception as e:
                print(f"[ERROR] cannot read file: {e}")
                continue
            if not content.strip():
                print("[WARN] file content is empty (after stripping), skipped")
                continue
            try:
                resp = register_text(base_url, content, timeout_s=args.timeout)
                print_text_ok(resp)
            except Exception as e:
                print(f"[ERROR] register_text failed: {e}")
            continue

        # -------- Text: single line --------
        content = line
        if len(content) >= args.warn_len:
            print(
                f"[WARN] input length={len(content)} is very large. "
                "Some terminals may truncate long single-line paste. "
                "Use ':file <path>' or '--file <path>' for long text."
            )

        try:
            resp = register_text(base_url, content, timeout_s=args.timeout)
            print_text_ok(resp)
        except Exception as e:
            print(f"[ERROR] register_text failed: {e}")

    print("bye.")


if __name__ == "__main__":
    main()

