# Forwarding and batching

## Role and directory

`proxy/` is an internal Python library used by service handlers, not an independently launched Gateway. `forward.py` handles HTTP forwarding and streaming; `batcher.py` groups and tracks logical requests; `sync.py` coordinates Prefill scheduling/execution. Gateway HTTP routes and local strategy managers live under `core/` and `scheduler/gateway/`.

## Request workflow

Controller builds the internal request and forwards to P Gateway. P scheduling chooses an Instance; the chosen Connector builds a Prefill engine payload. After Prefill, context and the request go to D Gateway, where a D Instance is selected and the Decode response is forwarded. A logical batch is scheduling/dispatch coordination, not a promise that all requests form one engine-level batch.

## Streaming and error boundaries

Downstream status/error behavior and response streaming depend on the forwarding path. NIXL missing Prefill transfer parameters is an error; descriptor checks alone do not prove data movement. Header forwarding is constrained; in particular `OPENAI_API_KEY` in the helper is not ingress authentication. See [interface index](../doc/developer-handbook/configuration-and-interfaces.md) and [observability](../doc/architecture/observability-v1.md).

## Validation

Run `python -m pytest -q test/test_gateway_scheduler_strategy.py test/test_gateway_observability.py` in the documented [test environment](../test/README.md). Use [real deployment](../doc/quickstart_v1.md) for streaming, backend and KV evidence.
