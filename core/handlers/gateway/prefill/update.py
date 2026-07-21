from fastapi import HTTPException, Request
from fastapi.responses import Response
import time

from store.gateway.manager import GatewayStoreManager
from utils.gateway_observability import get_gateway_observer


observer = get_gateway_observer("prefill")


async def register_instance(raw_request: Request):
    try:
        data = await raw_request.json()
        if data.get("role") != "prefill":
            raise HTTPException(status_code=400, detail="Prefill gateway only accepts prefill instances")
        instance = GatewayStoreManager().register_instance(
            instance_ip=data.get("report_label", ""),
            role=data.get("role", ""),
            connector=data.get("connector", ""),
            side_channel_host=data.get("side_channel_host", ""),
            side_channel_port=data.get("side_channel_port", 0),
            tp_size=data.get("tp_size", 0),
            vllm_port=data.get("vllm_port", 0),
            gpu_indices=data.get("gpu_indices"),
        )
        return {"report_label": instance.instance_ip, "role": instance.role}
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        gpu_devices = request_json.get("gpu_devices", [])
        network_metric = request_json.get("network_metric", {})

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
            instance.gpu_devices = gpu_devices if isinstance(gpu_devices, list) else []
            instance.network_metric = network_metric if isinstance(network_metric, dict) else {}
            if instance.gpu_devices:
                reported_gpu_indices = []
                for item in instance.gpu_devices:
                    if not isinstance(item, dict) or item.get("gpu_index") is None:
                        continue
                    try:
                        gpu_index = int(item["gpu_index"])
                    except (TypeError, ValueError):
                        continue
                    if gpu_index >= 0 and gpu_index not in reported_gpu_indices:
                        reported_gpu_indices.append(gpu_index)
                if reported_gpu_indices:
                    instance.gpu_indices = reported_gpu_indices
            instance.online = True
            instance.last_updated = time.time()
            storage.set_instance_table(report_label, instance)
            storage.confirm_distserve_heartbeat(
                "prefill",
                report_label,
                int(running_reqs) + int(waiting_reqs),
            )
            observer.record_instance_resources(
                report_label,
                gpu_devices=instance.gpu_devices,
                network=instance.network_metric,
            )
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
