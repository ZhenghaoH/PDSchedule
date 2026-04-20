from fastapi import HTTPException, Request
from fastapi.responses import Response

from store.gateway.manager import GatewayStoreManager


async def update_instance_metric(raw_request: Request):
    try:
        request_json = await raw_request.json()
        report_label = request_json.get('report_label', "")
        running_reqs = request_json.get('running_reqs', 0)
        waiting_reqs = request_json.get('waiting_reqs', 0)
        prompt_throughput = request_json.get('prompt_throughput', 0.0)
        generate_throughput = request_json.get('generate_throughput', 0.0)

        if report_label:
            storage = GatewayStoreManager()
            if report_label in storage.instance_table.keys():
                instance = storage.instance_table[report_label]
                instance.running_reqs = running_reqs
                instance.waiting_reqs = waiting_reqs
                instance.prompt_throughput = prompt_throughput
                instance.generate_throughput = generate_throughput
                storage.set_instance_table(report_label, instance)
                return Response(status_code=200)
            else:
                raise HTTPException(status_code=404, detail="Invalid report_label")
        else:
            raise HTTPException(status_code=400, detail="Invalid json format, missing report_label")
    except Exception as e:
        print(f"Error occur in update_instance_metric, {e}")
        raise HTTPException(status_code=500, detail=str(e))
