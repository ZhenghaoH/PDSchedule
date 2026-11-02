# Browser client

## Directory structure and request path

`client.py` serves the client and `templates/index.html` provides its interface. The client sends supported completion requests to a configured Controller URL; the Controller then routes to Gateways and Instances. This browser helper does not start model services.

## Supported endpoints

The browser helper uses `/`, `/api/send_command`, `/api/get_history` and `/api/clear_history`; these are distinct from Controller's `/v1/chat/completions` and `/v1/completions`. Command-mode field support is limited; see the [HTTP and request index](../../doc/developer-handbook/configuration-and-interfaces.md#instance-proxies-and-optional-client-ui).

## Interactive client

After the serving chain is ready, from the repository root run:

```bash
python -m examples.clientUI.client
```

The app binds to `127.0.0.1:5050`; open `http://127.0.0.1:5050/` from the same network namespace. The installed equivalent is `pdschedule-client-ui`. Configure/use the actual Controller URL in the interface. No authentication or public exposure is implied.

## Direct curl examples

The [real quick start](../../doc/quickstart_v1.md) owns the complete Controller curl/request examples and expected responses. Use those commands after verifying model and registrations; do not send user requests to internal management endpoints.

## Metadata and validation

The helper retains its own in-memory history; Gateway scheduling and timing logs have different scopes. A response alone does not prove KV reuse/transfer. Use the [NIXL transfer checks](../../doc/quickstart_v1.md), and consult [observability](../../doc/architecture/observability-v1.md). This repository does not provide a public workload replay client as part of this browser helper.
