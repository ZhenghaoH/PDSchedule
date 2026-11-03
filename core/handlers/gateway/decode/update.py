from fastapi import HTTPException, Request
from fastapi.responses import Response
import logging

from store.gateway.manager import GatewayStoreManager
from utils.gateway_observability import get_gateway_observer


observer = get_gateway_observer("decode")


async def register_instance(raw_request: Request):
    try:
        data = await raw_request.json()
        if data.get("role") != "decode":
            raise HTTPException(status_code=400, detail="Decode gateway only accepts decode instances")
        instance = GatewayStoreManager().register_instance(
            instance_ip=data.get("report_label", ""),
            role=data.get("role", ""),
            connector=data.get("connector"),
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
        if not isinstance(request_json, dict):
            raise HTTPException(status_code=400, detail="Invalid heartbeat JSON object")
        report_label = request_json.get("report_label", "")
        if not isinstance(report_label, str) or not report_label.strip():
            raise HTTPException(status_code=400, detail="Invalid json format, missing report_label")
        report_label = report_label.strip()
        storage = GatewayStoreManager()
        result, instance, was_schedulable, had_heartbeat = storage.update_instance_heartbeat(
            report_label, "decode", request_json
        )
        if result == "not_found":
            raise HTTPException(status_code=404, detail="Invalid report_label")
        if result == "role_conflict":
            raise HTTPException(status_code=409, detail="Instance role does not match this Decode gateway")
        if not was_schedulable:
            if had_heartbeat:
                logging.info(
                    "[Gateway] Instance heartbeat restored role=decode instance=%s",
                    report_label,
                )
            else:
                logging.info(
                    "[Gateway] Instance became schedulable role=decode instance=%s",
                    report_label,
                )
        observer.record_instance_resources(
            report_label,
            gpu_devices=instance.gpu_devices,
            network=instance.network_metric,
        )
        return Response(status_code=200)
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
