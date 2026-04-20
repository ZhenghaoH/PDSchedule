from store.controller.pool_manager import PoolManager, PoolType
from coord.controller.down_pair import send_pairing_command


def do_pair_pd(params: dict):
    """处理配对指令"""
    manager = PoolManager()
    p = str(params.get('p') or params.get('prefill', ""))
    d = str(params.get('d') or (params.get('decode', "")))
    force = bool(params.get('force', False))
    if not (p and d):
        print("Missing Params! Use '-p <p> -d <d>' or '--prefill=<p> --decode=<d>'. '--force=<bool>' is also allowed.")
        return False
    try:
        if ":" not in p:
            pool = manager.get_pool_by_id(PoolType.PREFILL, int(p))
            p = f"{pool.proxy_ip_address}:{pool.proxy_port}"
        else:
            ip, port = p.rsplit(":", 1)
            pool_id = manager.getid(PoolType.PREFILL, ip, int(port))
        if ":" not in d:
            pool = manager.get_pool_by_id(PoolType.DECODE, int(d))
            d = f"{pool.proxy_ip_address}:{pool.proxy_port}"
        else:
            ip, port = d.rsplit(":", 1)
            pool_id = manager.getid(PoolType.DECODE, ip, int(port))
    except Exception as e:
        print(f"Invalid Params! {e}")
        return False
    print("Sending pair command...")
    state = "Success" if send_pairing_command(p, d, force) else "Failed"
    print(f"Pair command send {state}.")
    return True
