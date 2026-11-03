import math

from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse

from store.controller import PoolManager, PoolType
from store.controller import NetworkManager


def _refresh_scheduler_resources(request: FastAPIRequest) -> None:
    scheduler = getattr(request.app.state, "global_scheduler", None)
    if scheduler is not None:
        scheduler.refresh_resources()


def _is_same_machine(prefill_pool, decode_pool) -> bool:
    """测试版同机判定：仅依据 proxy_ip_address 是否相同。"""
    return (
        prefill_pool is not None
        and decode_pool is not None
        and prefill_pool.proxy_ip_address == decode_pool.proxy_ip_address
    )


async def _unregister_pool(request: FastAPIRequest, pool_type: PoolType):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    pool_id = body.get("pool_id")
    proxy_ip = body.get("proxy_ip_address")
    proxy_port = body.get("proxy_port")
    if pool_id is None or not proxy_ip or proxy_port is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Required fields: 'pool_id', 'proxy_ip_address' and 'proxy_port'"},
        )

    try:
        pool_id = int(pool_id)
        proxy_ip = str(proxy_ip).strip()
        proxy_port = int(proxy_port)
        if not proxy_ip or not 1 <= proxy_port <= 65535:
            raise ValueError("invalid Gateway address")
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "'pool_id' and 'proxy_port' must be valid integers and 'proxy_ip_address' must be non-empty"},
        )

    pool_mgr = request.app.state.pool_manager
    net_mgr = request.app.state.network_manager
    result = pool_mgr.unregister_bound_pool(
        pool_type, pool_id, proxy_ip, proxy_port
    )
    if result == "not_found":
        return JSONResponse(
            status_code=404,
            content={"detail": f"{pool_type.value} Pool {pool_id} not registered."}
        )
    if result == "binding_conflict":
        return JSONResponse(
            status_code=409,
            content={"detail": f"{pool_type.value} Pool {pool_id} is registered to a different Gateway address."},
        )
    net_mgr.remove_connections_for_pool(pool_id, pool_type)
    _refresh_scheduler_resources(request)

    return {"status": "success"}


async def unregister_decode_pool(request: FastAPIRequest):
    return await _unregister_pool(request, PoolType.DECODE)


async def unregister_prefill_pool(request: FastAPIRequest):
    return await _unregister_pool(request, PoolType.PREFILL)


async def update_decode_pool_status(request: FastAPIRequest):
    """
    接收 Decode 池的定期上报。
    使用 Request 对象解析数据。

    发送方请求格式:
    POST /decode_pool/update
    Content-Type: application/json

    {
        "pool_id": 1,
        "current_load": 5
    }
    """
    # 1. 解析与校验数据
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    pool_id = body.get("pool_id")
    current_load = body.get("current_load")
    running_reqs = body.get("running_reqs")
    max_decode_req_num = body.get("max_decode_req_num")
    proxy_ip = body.get("proxy_ip_address")
    proxy_port = body.get("proxy_port")

    if (
        pool_id is None
        or not proxy_ip
        or proxy_port is None
        or (current_load is None and running_reqs is None)
    ):
        return JSONResponse(
            status_code=422,
            content={"detail": "Required fields: 'pool_id', 'proxy_ip_address', 'proxy_port' and one of 'running_reqs' or 'current_load'"}
        )

    try:
        pool_id = int(pool_id)
        current_load = float(current_load) if current_load is not None else None
        running_reqs = int(running_reqs) if running_reqs is not None else None
        max_decode_req_num = int(max_decode_req_num) if max_decode_req_num is not None else None
        proxy_ip = str(proxy_ip).strip()
        proxy_port = int(proxy_port)
        if not proxy_ip or not 1 <= proxy_port <= 65535:
            raise ValueError("invalid Gateway address")
        if current_load is not None and not math.isfinite(current_load):
            raise ValueError("current_load must be finite")
        if running_reqs is not None and running_reqs < 0:
            raise ValueError("running_reqs must be non-negative")
        if max_decode_req_num is not None and max_decode_req_num <= 0:
            raise ValueError("max_decode_req_num must be positive")
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Data type error: 'pool_id' must be int, "
                          "'current_load' must be float/int, 'running_reqs' and "
                          "'max_decode_req_num' must be int"
            }
        )

    # 2. 进行资源更新
    pool_mgr = request.app.state.pool_manager
    result = pool_mgr.update_bound_pool_status(
        PoolType.DECODE,
        pool_id,
        proxy_ip,
        proxy_port,
        current_load=current_load,
        running_reqs=running_reqs,
        max_decode_req_num=max_decode_req_num,
    )

    # 3. 处理结果
    if result == "not_found":
        return JSONResponse(
            status_code=404,
            content={"detail": f"Decode Pool {pool_id} not registered. Please call /register first."}
        )
    if result == "binding_conflict":
        return JSONResponse(
            status_code=409,
            content={"detail": f"Decode Pool {pool_id} is registered to a different Gateway address."},
        )
    if result != "updated":
        return JSONResponse(status_code=422, content={"detail": "Invalid Decode Pool load"})

    _refresh_scheduler_resources(request)

    # 成功返回
    return {
        "status": "success"
    }


async def register_decode_pool(request: FastAPIRequest):
    """
    接收 Decode 池的注册请求，由调度器分配唯一 ool_id 并返回。

    发送方请求格式:
    POST /decode_pool/register
    Content-Type: application/json

    {
        "proxy_ip_address": "192.168.1.10",
        "proxy_port": 9090,
        "mem_bandwidth": 800.0,
        "GPU_type": "A100-80G"
    }

    返回格式:
    {
        "status": "success",
        "pool_id": 101,  <-- 服务端分配的ID
        "message": "..."
    }
    """
    # 1. 解析与校验数据
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    # 获取字段
    proxy_ip = body.get("proxy_ip_address")
    proxy_port = body.get("proxy_port")
    mem_bandwidth = body.get("mem_bandwidth")
    gpu_type = body.get("GPU_type", "")
    max_decode_req_num = body.get("max_decode_req_num", 128)
    connector = body.get("connector")
    try:
        from connectors.manager import get_connector_manager
        connector = get_connector_manager().validate(connector).to_dict()
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # 自动获取 IP (如果客户端未传)
    if proxy_ip is None and request.client:
        proxy_ip = request.client.host

    # 必填项检查
    if any(item is None for item in [proxy_ip, proxy_port, mem_bandwidth]):
        return JSONResponse(
            status_code=422,
            content={"detail": "Missing required fields: 'proxy_ip_address', 'proxy_port' or 'mem_bandwidth'"}
        )

    # 类型转换
    try:
        proxy_port = int(proxy_port)
        mem_bandwidth = float(mem_bandwidth)
        proxy_ip = str(proxy_ip)
        max_decode_req_num = max(1, int(max_decode_req_num))
        if not 1 <= proxy_port <= 65535:
            raise ValueError("proxy_port must be between 1 and 65535")
        if not math.isfinite(mem_bandwidth):
            raise ValueError("mem_bandwidth must be finite")
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"detail": "Data type error: port and max_decode_req_num must be int, bandwidth must be float"}
        )

    # 2. 注册并获取分配的 ID
    pool_mgr = request.app.state.pool_manager

    # 调用 register_Pool 函数，由它负责分配 ID
    new_pool_id = pool_mgr.register_Pool(
        Pool_type=PoolType.DECODE,
        ip=proxy_ip,
        port=proxy_port,
        capacity=mem_bandwidth,
        max_decode_req_num=max_decode_req_num,
        connector=connector,
        gpu_type=str(gpu_type),
    )

    if new_pool_id == -1:
        return JSONResponse(
            status_code=500,
            content={"detail": "Registration failed due to internal error."}
        )

    _refresh_scheduler_resources(request)

    # 3. 返回分配的 ID
    return {
        "status": "success",
        "pool_id": new_pool_id,  # 返回给客户端，客户端后续心跳需带上此 ID
        "message": f"Registered successfully. Assigned ID: {new_pool_id}"
    }


async def update_prefill_pool_status(request: FastAPIRequest):
    """
    接收 Prefill 节点的定期上报。
    使用 Request 对象解析数据。

    发送方请求格式:
    POST /prefill_pool/update
    Content-Type: application/json

    {
        "pool_id": 1,
        "current_load": 5
    }
    """
    # 1. 解析与校验数据
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    pool_id = body.get("pool_id")
    current_load = body.get("current_load")
    proxy_ip = body.get("proxy_ip_address")
    proxy_port = body.get("proxy_port")

    if pool_id is None or current_load is None or not proxy_ip or proxy_port is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Required fields: 'pool_id', 'proxy_ip_address', 'proxy_port' and 'current_load'"}
        )

    try:
        pool_id = int(pool_id)
        current_load = float(current_load)
        proxy_ip = str(proxy_ip).strip()
        proxy_port = int(proxy_port)
        if not proxy_ip or not 1 <= proxy_port <= 65535:
            raise ValueError("invalid Gateway address")
        if not math.isfinite(current_load):
            raise ValueError("current_load must be finite")
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Data type error: 'pool_id' must be int, 'current_load' must be float/int"}
        )

    # 2. Check the address binding and update while holding the Pool lock.
    pool_mgr = request.app.state.pool_manager
    result = pool_mgr.update_bound_pool_status(
        PoolType.PREFILL,
        pool_id,
        proxy_ip,
        proxy_port,
        current_load=current_load,
    )

    # 3. 处理结果
    if result == "not_found":
        return JSONResponse(
            status_code=404,
            content={"detail": f"Prefill Pool {pool_id} not registered. Please call /register first."}
        )
    if result == "binding_conflict":
        return JSONResponse(
            status_code=409,
            content={"detail": f"Prefill Pool {pool_id} is registered to a different Gateway address."},
        )
    if result != "updated":
        return JSONResponse(status_code=422, content={"detail": "Invalid Prefill Pool load"})

    _refresh_scheduler_resources(request)

    # 成功返回
    return {
        "status": "success"
    }


async def register_prefill_pool(request: FastAPIRequest):
    """
    接收 Prefill 节点的注册请求，由调度器分配唯一 pool_id 并返回。

    发送方请求格式:
    POST /prefill_pool/register
    Content-Type: application/json

    {
        "proxy_ip_address": "172.18.0.166",
        "proxy_port": 9090,
        "flops": 209.0,
        "GPU_type": "A100-80G"
    }

    返回格式:
    {
        "status": "success",
        "pool_id": 101,  <-- 服务端分配的ID
        "message": "..."
    }
    """
    # 1. 解析与校验数据 (不包含 pool_id)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    # 获取字段 (无需获取 pool_id)
    proxy_ip = body.get("proxy_ip_address")
    proxy_port = body.get("proxy_port")
    flops = body.get("flops")
    gpu_type = body.get("GPU_type", "")
    max_prompt_throughput = body.get("max_prompt_throughput", 0.0)
    connector = body.get("connector")
    try:
        from connectors.manager import get_connector_manager
        connector = get_connector_manager().validate(connector).to_dict()
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # 自动获取 IP (如果客户端未传)
    if proxy_ip is None and request.client:
        proxy_ip = request.client.host

    # 必填项检查
    if any(item is None for item in [proxy_ip, proxy_port, flops]):
        return JSONResponse(
            status_code=422,
            content={"detail": "Missing required fields: 'proxy_ip_address', 'proxy_port' or 'flops'"}
        )

    # 类型转换
    try:
        proxy_port = int(proxy_port)
        flops = float(flops)
        proxy_ip = str(proxy_ip)
        max_prompt_throughput = max(0.0, float(max_prompt_throughput))
        if not 1 <= proxy_port <= 65535:
            raise ValueError("proxy_port must be between 1 and 65535")
        if not math.isfinite(flops) or not math.isfinite(max_prompt_throughput):
            raise ValueError("Pool capacities must be finite")
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Data type error: port must be int, flops must be float"}
        )

    # 2. 注册并获取分配的 ID
    pool_mgr = request.app.state.pool_manager

    # 调用 register_Pool 函数，由它负责分配 ID
    new_pool_id = pool_mgr.register_Pool(
        Pool_type=PoolType.PREFILL,
        ip=proxy_ip,
        port=proxy_port,
        capacity=flops,
        max_prompt_throughput=max_prompt_throughput,
        connector=connector,
        gpu_type=str(gpu_type),
    )

    if new_pool_id == -1:
        return JSONResponse(
            status_code=500,
            content={"detail": "Registration failed due to internal error."}
        )

    _refresh_scheduler_resources(request)

    # 3. 返回分配的 ID
    return {
        "status": "success",
        "pool_id": new_pool_id,  # 返回给客户端，客户端后续心跳需带上此 ID
        "message": f"Registered successfully. Assigned ID: {new_pool_id}"
    }


async def register_network_link(request: FastAPIRequest):
    """
    注册网络链路。
    仅接收网关自身的 IP 地址和端口，调度器自动处理链路建立。

    发送方请求格式:
    POST /network/register
    {
        "gateway_ip": "192.168.1.10",
        "gateway_port": 9090,
        "bandwidth": 1000.0
    }
    """
    # 1. 解析与校验数据
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})

    gateway_ip = body.get("gateway_ip")
    gateway_port = body.get("gateway_port")
    bandwidth = body.get("bandwidth")

    # 必填项检查
    if any(item is None for item in [gateway_ip, gateway_port, bandwidth]):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Missing required fields: 'gateway_ip', 'gateway_port', or 'bandwidth'"}
        )

    # 类型转换与检查
    try:
        gateway_ip = str(gateway_ip).strip()
        gateway_port = int(gateway_port)
        bandwidth = float(bandwidth)
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"detail": "Data type error: port must be int, bandwidth must be float"}
        )

    # 2. IP -> ID 转换
    pool_mgr = request.app.state.pool_manager
    # 查找网关对应的节点 ID
    # 先尝试查找 Prefill 节点
    node_id = pool_mgr.get_id_by_ip_port(PoolType.PREFILL, gateway_ip, gateway_port)
    # 如果不是 Prefill 节点，尝试查找 Decode 节点
    if node_id is None:
        node_id = pool_mgr.get_id_by_ip_port(PoolType.DECODE, gateway_ip, gateway_port)
    
    if node_id is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"Node with IP {gateway_ip} and Port {gateway_port} not found. Please register the node first."}
        )

    # 3. 调用管理器进行注册
    net_mgr = request.app.state.network_manager

    # 这里简化处理，仅记录网关的带宽信息
    # 实际的网络链路管理逻辑可能需要根据具体架构调整
    success = True

    # 4. 处理结果
    if not success:
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal error registering network."}
        )

    return {
        "status": "success",
        "message": f"Network registered for gateway {gateway_ip}:{gateway_port}"
    }


async def update_network_status(request: FastAPIRequest):
    """
    更新网络链路状态。
    仅接收网关自身的 IP 地址、端口和带宽信息。

    发送方请求格式:
    POST /network/update
    {
        "gateway_ip": "192.168.1.10",
        "gateway_port": 9090,
        "bandwidth": 850.0
    }
    """
    # 1. 解析与校验数据
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON format"})

    gateway_ip = body.get("gateway_ip")
    gateway_port = body.get("gateway_port")
    bandwidth = body.get("bandwidth")
    rtt = body.get("rtt", 10.0)
    pcie_bandwidth = body.get("pcie_bandwidth")
    pcie_rtt = body.get("pcie_rtt", 0.1)

    if any(item is None for item in [gateway_ip, gateway_port, bandwidth]):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Missing required fields: 'gateway_ip', 'gateway_port', or 'bandwidth'"}
        )

    try:
        gateway_ip = str(gateway_ip).strip()
        gateway_port = int(gateway_port)
        bandwidth = float(bandwidth)
        rtt = float(rtt)
        pcie_bandwidth = float(pcie_bandwidth) if pcie_bandwidth is not None else bandwidth
        pcie_rtt = float(pcie_rtt)
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"detail": "Data type error: port must be int, bandwidth must be float"}
        )

    # 2. IP -> ID 转换
    pool_mgr = request.app.state.pool_manager

    # 查找网关对应的节点 ID
    # 先尝试查找 Prefill 节点
    node_id = pool_mgr.get_id_by_ip_port(PoolType.PREFILL, gateway_ip, gateway_port)
    # 如果不是 Prefill 节点，尝试查找 Decode 节点
    if node_id is None:
        node_id = pool_mgr.get_id_by_ip_port(PoolType.DECODE, gateway_ip, gateway_port)
    
    if node_id is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Node with IP {gateway_ip} and Port {gateway_port} not found. Please register the node first."}
        )

    # 3. 调用管理器进行更新
    net_mgr = request.app.state.network_manager
    pool_mgr = request.app.state.pool_manager

    # 确定网关的池类型
    gateway_pool = None
    gateway_pool_type = None
    
    # 查找网关对应的池
    for pool_type in [PoolType.PREFILL, PoolType.DECODE]:
        gateway_pool = pool_mgr.get_pool_by_id(pool_type, node_id)
        if gateway_pool:
            gateway_pool_type = pool_type
            break
    
    # 更新与所有对应类型池的连接
    success = True
    if gateway_pool_type == PoolType.PREFILL:
        # 网关是 Prefill 池，更新与所有 Decode 池的连接
        decode_pools = list(pool_mgr._pools.get(PoolType.DECODE.value, {}).values())
        for decode_pool in decode_pools:
            decode_id = decode_pool.pool_id
            link_bandwidth = pcie_bandwidth if _is_same_machine(gateway_pool, decode_pool) else bandwidth
            link_rtt = pcie_rtt if _is_same_machine(gateway_pool, decode_pool) else rtt
            # 调用 NetworkManager 的 update_network_status 方法
            if not net_mgr.update_network_status(node_id, decode_id, link_bandwidth, link_rtt):
                success = False
    elif gateway_pool_type == PoolType.DECODE:
        # 网关是 Decode 池，更新与所有 Prefill 池的连接
        prefill_pools = list(pool_mgr._pools.get(PoolType.PREFILL.value, {}).values())
        for prefill_pool in prefill_pools:
            prefill_id = prefill_pool.pool_id
            link_bandwidth = pcie_bandwidth if _is_same_machine(prefill_pool, gateway_pool) else bandwidth
            link_rtt = pcie_rtt if _is_same_machine(prefill_pool, gateway_pool) else rtt
            # 调用 NetworkManager 的 update_network_status 方法
            if not net_mgr.update_network_status(prefill_id, node_id, link_bandwidth, link_rtt):
                success = False

    # 4. 处理结果
    if not success:
        return JSONResponse(
            status_code=500,
            content={"detail": "Network update failed."}
        )

    _refresh_scheduler_resources(request)

    return {
        "status": "success",
        "updated_gateway": f"{gateway_ip}:{gateway_port}",
        "bandwidth": bandwidth,
        "pcie_bandwidth": pcie_bandwidth
    }
