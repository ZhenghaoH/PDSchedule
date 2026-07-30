from typing import List, Dict, Any, Optional
import threading
from copy import deepcopy
import os


class InstanceStore:
    """存储管理"""
    _instance = None
    _lock = threading.RLock()
    _initialized = False

    def __new__(cls, *args, **kwargs):
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化存储实例，防止重复初始化"""
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.host = "127.0.0.1"  # 临时存储 主机host
                    self.port = 7001  # 临时存储 主机监听端口
                    self.vllm_port = 8001  # vllm 端口
                    self.gateway = ""  # 临时变量 控制器监听host
                    self.rank = 0  # 身份 prefill=0 decode=1

                    self.USE_MOCK = False  # 使用模拟数据
                    self.role = ""
                    self.connector = {"name": "nixl", "version": "1", "metadata": {}}
                    self.gpu_indices = [0]
                    self.network_metric = {}
                    self.network_metric_sampled_at = 0.0

                    self.LOOP_TASK_TIME = 0.02  # 轮询上报任务时间

                    self._initialized = True  # 确保不重复初始化

    def vllm_addr(self):
        return f"{self.host}:{self.vllm_port}"

    def report_label(self):
        return f"{self.host}:{self.port}"

