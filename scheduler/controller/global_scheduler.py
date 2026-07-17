import sys
import os
from urllib.parse import urlsplit

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass


# --- 导入算网状态 ---
from store.controller.pool_base import PrefillPool, DecodePool, PoolType
from store.controller.pool_manager import PoolManager
from store.controller.network import NetworkManager, Network

# --- 导入调度算法 ---
from .scheduler_PSO_1 import PSO_Scheduler
from .scheduler_Random import Random_Scheduler
from .scheduler_RoundRobin import PairedRoundRobin_Scheduler, RoundRobin_Scheduler
from .scheduler_LeastLoad import LeastLoad_Scheduler
from .scheduler_AdaptiveVariance import AdaptiveVarianceScheduler

# --- 导入请求 ---
from core.schemas.request import Prompt, Service 
# from request import Prompt, Service 

# ====================================================================================
#                         Global Scheduler (全局调度器)
# ====================================================================================


def parse_gateway_endpoint(value: str) -> Tuple[str, int]:
    """Parse host:port (including bracketed IPv6) without guessing a port."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Gateway address is empty")

    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid gateway address {raw!r}: {exc}") from exc

    if not host or port is None:
        raise ValueError(f"Gateway address must be formatted as host:port: {raw!r}")
    if not 1 <= port <= 65535:
        raise ValueError(f"Gateway port is out of range: {port}")
    return host, port


class GlobalScheduler:
    ROUTING_MODES = frozenset({"global", "fixed_gateway"})

    def __init__(
        self,
        pool_manager: PoolManager,
        network_manager: NetworkManager,
        routing_mode: str = "global",
        fixed_prefill_gateway: str = "",
        fixed_decode_gateway: str = "",
    ):
        """
        初始化全局调度器。
        """
        self.pool_manager = pool_manager
        self.network_manager = network_manager

        normalized_routing_mode = str(routing_mode or "global").strip().lower()
        if normalized_routing_mode not in self.ROUTING_MODES:
            raise ValueError(
                f"Unsupported Controller routing mode: {routing_mode!r}. "
                f"Available: {sorted(self.ROUTING_MODES)}"
            )
        self.routing_mode = normalized_routing_mode
        self.fixed_prefill_gateway: Optional[Tuple[str, int]] = None
        self.fixed_decode_gateway: Optional[Tuple[str, int]] = None
        if self.routing_mode == "fixed_gateway":
            self.fixed_prefill_gateway = parse_gateway_endpoint(fixed_prefill_gateway)
            self.fixed_decode_gateway = parse_gateway_endpoint(fixed_decode_gateway)

        # 1. 资源容器
        self.prefill_pools: List[PrefillPool] = []
        self.decode_pools: List[DecodePool] = []
        self.networks: List[Network] = []
        self.network_map: Dict[Tuple[int, int], Network] = {}
        
        # 2. 策略管理
        self.strategies = {
            "pso": PSO_Scheduler(num_particles=20, max_iterations=15),
            "random": Random_Scheduler(),
            "round_robin": RoundRobin_Scheduler(),
            "paired_round_robin": PairedRoundRobin_Scheduler(),
            "least_load": LeastLoad_Scheduler(),
            "adaptive_variance": AdaptiveVarianceScheduler(num_particles=20, max_iterations=15),
        }
        self.current_mode: str = "pso" # 默认策略
        self.is_ready: bool = False

        print(
            "[GlobalScheduler] Initializing... "
            f"routing={self.routing_status()}"
        )

    @staticmethod
    def _format_endpoint(endpoint: Optional[Tuple[str, int]]) -> str:
        if endpoint is None:
            return ""
        host, port = endpoint
        display_host = f"[{host}]" if ":" in host else host
        return f"{display_host}:{port}"

    def routing_status(self) -> str:
        if self.routing_mode == "fixed_gateway":
            return (
                "mode=fixed_gateway, "
                f"prefill={self._format_endpoint(self.fixed_prefill_gateway)}, "
                f"decode={self._format_endpoint(self.fixed_decode_gateway)}"
            )
        return f"mode=global, scheduler={self.current_mode}"

    def sync_state(self):
        """
        同步状态：
        从 ResourceManager 拉取最新数据，更新本地快照。
        这个方法通常由后台线程定期调用，或者在 schedule 之前按需调用（带节流）。
        """
        # 1. 从 Manager 拉取活跃节点 (这一步会获取 Manager 的锁)
        active_p = self.pool_manager.get_active_Pools(PoolType.PREFILL)
        active_d = self.pool_manager.get_active_Pools(PoolType.DECODE)
        active_nets = self.network_manager.get_all_networks()
        
        # 2. 更新本地列表 (切换引用是原子操作，通常不需要加锁，除非有多线程调度)
        self.prefill_pools = active_p
        self.decode_pools = active_d
        
        # 3. 计算网络查找表
        # 这一步开销较大，只在同步时做一次，调度时直接查表
        self.network_map = {
            (net.prefill_pool.pool_id, net.decode_pool.pool_id): net 
            for net in active_nets
        }
                

    
    # 可以在此处添加更多策略，例如 RoundRobin, Greedy
    def switch_mode(self, mode: str):
        """切换调度策略 (e.g., 'pso', 'random')"""
        if self.routing_mode == "fixed_gateway":
            print(
                "[GlobalScheduler] Controller uses fixed_gateway routing; "
                "global scheduler switching is disabled."
            )
            return False
        mode = mode.lower()
        if mode in self.strategies:
            self.current_mode = mode
            print(f"[GlobalScheduler] Switched strategy to: {self.current_mode.upper()}")
            return True
        else:
            valid_modes = list(self.strategies.keys())
            print(f"[GlobalScheduler] Error: Mode '{mode}' invalid. Available: {valid_modes}")
            return False

    def schedule(self, prompt_obj: Prompt, service_obj: Service, request_id: int) -> Dict[str, Any]:
        """
        调度接口：根据输入的Prompt和服务需求，使用当前策略选择最优的Prefill和Decode节点
        
        参数说明:
            prompt_obj (Prompt): 包含提示信息的对象，用于获取token长度等信息
            service_obj (Service): 服务对象，包含服务质量要求(SLO)和压缩因子等参数
            request_id (int): 请求唯一标识符，可用于日志追踪和调试
            
        返回值:
            Dict[str, Any]: 包含选定的Prefill和Decode节点地址信息的字典
                - P_proxy_addr: 选定的Prefill节点IP地址
                - P_proxy_port: 选定的Prefill节点端口号
                - D_proxy_addr: 选定的Decode节点IP地址
                - D_proxy_port: 选定的Decode节点端口号
                
        异常:
            RuntimeError: 当资源未初始化或为空时抛出异常，提示需要先调用update_state()
        """
        if self.routing_mode == "fixed_gateway":
            if self.fixed_prefill_gateway is None or self.fixed_decode_gateway is None:
                raise RuntimeError("Fixed gateway routing is not configured")
            p_ip, p_port = self.fixed_prefill_gateway
            d_ip, d_port = self.fixed_decode_gateway
            return {
                "P_proxy_addr": p_ip,
                "P_proxy_port": p_port,
                "D_proxy_addr": d_ip,
                "D_proxy_port": d_port,
            }

        # 1. 基础检查 - 确保调度器已加载必要的资源
        if not self.prefill_pools or not self.decode_pools:
            raise RuntimeError("Resources empty. Call update_state first.")

        # 2. 准备参数 - 从Prompt和服务对象中提取调度所需的关键参数
        token_len = getattr(prompt_obj, 'token_length', 0)     # 获取输入提示的token长度
        max_tokens = getattr(prompt_obj, 'max_tokens', None)   # 获取Decode侧输出token上限
        max_tokens_fallback = max_tokens is None
        if max_tokens_fallback:
            max_tokens = token_len
        slo_ttft = getattr(service_obj, 'SLO_TTFT', 0.0)       # 获取首Token时延目标(SLO)
        slo_tpot = getattr(service_obj, 'SLO_TPOT', 0.0)       # 获取后续Token生成时延目标(SLO)
        comp_fac = getattr(service_obj, 'Compress_factor', 1.0) # 获取KV缓存压缩因子

        # 获取当前激活的调度策略，如果未找到则回退到随机策略
        strategy = self.strategies.get(self.current_mode, self.strategies["random"])

        # 3. 执行调度
        # 根据不同策略模式传递相应的参数
        if self.current_mode == "pso":
            # PSO策略需要详细的系统状态信息来进行优化计算
            p_ip, p_port, d_ip, d_port = strategy.schedule(
                token_length=token_len,           # 输入token长度
                slo_ttft=slo_ttft,                # 首Token时延目标
                slo_tpot=slo_tpot,                # 后续Token时延目标
                compress_factor=comp_fac,         # KV缓存压缩因子
                # --- 注入当前系统状态 ---
                prefill_pools=self.prefill_pools, # Prefill节点池状态
                decode_pools=self.decode_pools,   # Decode节点池状态
                network_map=self.network_map      # 网络连接状态映射
            )
        elif self.current_mode == "adaptive_variance":
            p_ip, p_port, d_ip, d_port = strategy.schedule(
                token_length=token_len,
                max_tokens=max_tokens,
                slo_ttft=slo_ttft,
                slo_tpot=slo_tpot,
                compress_factor=comp_fac,
                prefill_pools=self.prefill_pools,
                decode_pools=self.decode_pools,
                network_map=self.network_map,
                request_id=request_id,
                max_tokens_fallback=max_tokens_fallback,
            )
        else:
            # Random等简单策略只需要基本的节点池信息
            p_ip, p_port, d_ip, d_port = strategy.schedule(
                prefill_pools=self.prefill_pools,  # Prefill节点池
                decode_pools=self.decode_pools     # Decode节点池
            )

        # 返回调度结果，包含选定节点的网络地址信息
        return {
            "P_proxy_addr": p_ip, "P_proxy_port": p_port,
            "D_proxy_addr": d_ip, "D_proxy_port": d_port,
        }

# --- 测试代码 (可以直接运行此文件) ---
if __name__ == "__main__":
    # 1. 创建测试资源
    p_nodes = [PrefillPool(i, f"10.0.1.{i}", 8000+i, 100.0) for i in range(2)]
    d_nodes = [DecodePool(i, f"10.0.2.{i}", 9000+i, 500.0) for i in range(2)]
    
    # 2. 初始化调度器 (故意不传 network，测试自动生成功能)
    # 修复这里的错误：应该调用update_state而不是直接传参给构造函数
    scheduler = GlobalScheduler()
    # 添加网络连接
    networks = []
    for p_node in p_nodes:
        for d_node in d_nodes:
            networks.append(Network(p_node, d_node, 1000.0, 10.0))
    
    scheduler.update_state(p_nodes, d_nodes, networks)

    # 3. 构造 Mock 数据
    class MockData: pass
    prompt = MockData()
    prompt.token_length = 1024
    service = MockData()
    service.SLO_TTFT = 500
    service.SLO_TPOT = 50
    service.Compress_factor = 0.8

    # 4. 测试
    print("\n--- PSO Schedule ---")
    res = scheduler.schedule(prompt, service, 1)  # 修改为schedule方法
    print(res)
    
    print("\n--- Random Schedule ---")
    scheduler.switch_mode("random")
    res = scheduler.schedule(prompt, service, 2)  # 修改为schedule方法
    print(res)
    
    print("\n--- Round Robin Schedule ---")
    scheduler.switch_mode("round_robin")
    res = scheduler.schedule(prompt, service, 3)  # 修改为schedule方法
    print(res)
    
    # 测试轮询效果
    print("\n--- Round Robin Multiple Schedules ---")
    for i in range(4):
        res = scheduler.schedule(prompt, service, 3 + i)
        print(f"Schedule {i+1}: {res}")
    
    print("\n--- Least Load Schedule ---")
    scheduler.switch_mode("least_load")
    res = scheduler.schedule(prompt, service, 7)  # 修改为schedule方法
    print(res)
