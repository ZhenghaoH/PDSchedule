from fastapi import HTTPException, Request
from fastapi.responses import Response
import time

from store.gateway.manager import GatewayStoreManager


async def update_instance_metric(raw_request: Request):
    try:
        request_json = await raw_request.json()
        report_label = request_json.get('report_label', "")
        running_reqs = request_json.get('running_reqs', 0)
        waiting_reqs = request_json.get('waiting_reqs', 0)
        prompt_throughput = request_json.get('prompt_throughput', 0.0)
        generate_throughput = request_json.get('generate_throughput', 0.0)
        kv_cache_usage_percent = request_json.get('kv_cache_usage_percent', 0.0)
        gpu_util_percent = request_json.get('gpu_util_percent', 0.0)
        gpu_memory_util_percent = request_json.get('gpu_memory_util_percent', 0.0)

        if report_label:
            storage = GatewayStoreManager()
            if not storage.has_instance(report_label):
                raise HTTPException(status_code=404, detail="Invalid report_label")
            instance = storage.instance_table[report_label]
            instance.running_reqs = running_reqs
            instance.waiting_reqs = waiting_reqs
            instance.prompt_throughput = prompt_throughput
            instance.generate_throughput = generate_throughput
            instance.kv_cache_usage_percent = kv_cache_usage_percent
            instance.gpu_util_percent = gpu_util_percent
            instance.gpu_memory_util_percent = gpu_memory_util_percent
            instance.online = True
            instance.last_updated = time.time()
            storage.set_instance_table(report_label, instance)
            return Response(status_code=200)
        else:
            raise HTTPException(status_code=400, detail="Invalid json format, missing report_label")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error occur in update_instance_metric, {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def unregister_instance(raw_request: Request):
    try:
        request_json = await raw_request.json()
        report_label = request_json.get("report_label", "")
        if not report_label:
            raise HTTPException(status_code=400, detail="Invalid json format, missing report_label")

        storage = GatewayStoreManager()
        if not storage.mark_instance_offline(report_label):
            raise HTTPException(status_code=404, detail="Invalid report_label")
        return Response(status_code=200)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error occur in unregister_instance, {e}")
        raise HTTPException(status_code=500, detail=str(e))
