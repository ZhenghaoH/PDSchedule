import uvicorn
from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
import threading
import time
import requests
import argparse
import logging
import json
from typing import Dict, Optional, Tuple, List
from concurrent.futures import ThreadPoolExecutor

from prometheus_client.parser import text_string_to_metric_families

# ================= 日志配置 =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("Monitor_Server")

# ================= 单节点监控类 =================
class SingleVLLMMonitor:
    def __init__(self, vllm_url: str):
        self.vllm_url = vllm_url
        self.start_time = 0.0
        self.start_prompt_total = 0.0
        self.start_gen_total = 0.0
        self.is_running = False

    def _fetch_metrics(self) -> Tuple[float, float, bool]:
        try:
            response = requests.get(self.vllm_url, timeout=3)
            response.raise_for_status()
            
            p_val = 0.0
            g_val = 0.0
            
            # 解析 Prometheus
            for family in text_string_to_metric_families(response.text):
                name = family.name
                # 兼容常见命名
                if name in ['vllm:prompt_tokens_total', 'vllm_prompt_tokens_total', 'vllm:prompt_tokens']:
                    if family.samples: p_val = family.samples[0].value
                if name in ['vllm:generation_tokens_total', 'vllm_generation_tokens_total', 'vllm:generation_tokens']:
                    if family.samples: g_val = family.samples[0].value
            
            return p_val, g_val, True
        except Exception as e:
            logger.error(f"❌ Fetch failed {self.vllm_url}: {e}")
            return 0.0, 0.0, False

    def start(self):
        self.is_running = True
        self.start_time = time.time()
        p, g, ok = self._fetch_metrics()
        self.start_prompt_total = p
        self.start_gen_total = g
        if ok:
            logger.info(f"⏱️  Start Snapshot [{self.vllm_url}]: P={p}, G={g}")

    def stop(self) -> Dict:
        if not self.is_running: return {}
        self.is_running = False
        
        end_time = time.time()
        p_end, g_end, ok = self._fetch_metrics()
        
        delta_time = max(0.001, end_time - self.start_time)
        
        if not ok:
            d_p, d_g = 0, 0
        else:
            d_p = max(0, p_end - self.start_prompt_total)
            d_g = max(0, g_end - self.start_gen_total)
            
        p_tps = d_p / delta_time
        g_tps = d_g / delta_time
        
        # 调试日志：打印单个实例的计算结果
        logger.info(f"🛑 Stop Snapshot [{self.vllm_url}]: Old_P={self.start_prompt_total}, New_P={p_end}, Delta={d_p}, TPS={p_tps:.2f}")

        return {
            "url": self.vllm_url,
            "status": "ok" if ok else "error",
            "duration": float(f"{delta_time:.4f}"),
            "metrics": {
                "prompt_tokens_count": int(d_p),
                "generation_tokens_count": int(d_g),
                "prompt_throughput": float(f"{p_tps:.2f}"),
                "generation_throughput": float(f"{g_tps:.2f}")
            }
        }

# ================= 多节点管理器 =================
class MultiVLLMMonitor:
    def __init__(self, vllm_urls: List[str]):
        self.monitors = [SingleVLLMMonitor(url) for url in vllm_urls]
        self.thread_pool = ThreadPoolExecutor(max_workers=32)

    def start_all(self):
        logger.info("🎬 Starting monitoring...")
        list(self.thread_pool.map(lambda m: m.start(), self.monitors))

    def stop_all(self) -> Dict:
        logger.info("🛑 Stopping monitoring...")
        raw_results = list(self.thread_pool.map(lambda m: m.stop(), self.monitors))
        
        instances_map = {}
        for res in raw_results:
            if not res: continue
            url = res.get("url")
            instances_map[url] = res.get("metrics", {})

        response_payload = {
            "status": "ok",
            "instances": instances_map  # 关键：确保这里返回的是 instances 字典
        }
        
        # [DEBUG] 打印最终返回给 Client 的数据结构
        logger.info(f"📤 Sending Response: {json.dumps(response_payload)}")
        return response_payload

# ================= FastAPI =================
manager_instance: Optional[MultiVLLMMonitor] = None
target_urls: List[str] = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager_instance
    if target_urls:
        manager_instance = MultiVLLMMonitor(target_urls)
    yield
    if manager_instance: manager_instance.thread_pool.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/start")
async def start():
    if manager_instance: manager_instance.start_all()
    return {"status": "started"}

@app.post("/stop")
async def stop():
    if not manager_instance: raise HTTPException(500, "Not init")
    data = manager_instance.stop_all()
    return {"status": "stopped", "data": data}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=12345)
    parser.add_argument("--vllm-urls", nargs='+', required=True)
    args = parser.parse_args()
    target_urls = args.vllm_urls
    uvicorn.run(app, host=args.host, port=args.port)