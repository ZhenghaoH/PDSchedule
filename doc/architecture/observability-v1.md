# Observability foundation

The filename follows the documentation layout and does not define a versioned trace protocol.

## Schema and lifecycle

Controller exposes `/requests` as a request page. Gateways append JSON records to role-specific `prefill_gateway_schedule.log`, `prefill_gateway_timing.log`, `prefill_gateway_resource.log` and corresponding `decode_...` files. Base fields include `log_type`, `gateway_role`, `logged_at` and `logged_at_epoch_s`; event-specific fields depend on the call site. Logs are separate local files, not a unified trace exporter. See [source](../../utils/gateway_observability.py) and [configuration](../developer-handbook/configuration-and-interfaces.md).

## Timing and correlation

Internal request `Request_ID` and `Task.scheduler_metadata` support correlation. The Decode timing summary can include `prefill_queue_ms`, `prefill_ms`, `decode_gateway_queue_ms`, `observed_network_transfer_ms`, `decode_stream_ms`, `decode_total_ms`, `gateway_queue_total_ms` and `gateway_e2e_ms`. Missing timestamps yield `null`; timing uses elapsed wall-clock differences, clamped at zero. `observed_network_transfer_ms` covers NIXL pull **plus** engine admission and first-token work, not pure network latency. Cross-host clocks and first-chunk boundaries limit interpretation.

## Current limitations

The Controller page, process logs and vLLM metrics have different scopes. Logs can fail to write, and a successful Connector/session validation does not establish a KV transfer or cache hit. There is no unified trace collector, end-to-end exporter, or universally defined cross-process clock. The exact event vocabulary is implementation-level and may differ on error/cancellation. CPU checks do not establish GPU behavior.

## Internal propagation

The internal `BaseRequest` carries `Task.connector_context` and `Task.scheduler_metadata` across selected Gateway paths. NIXL supplies and forwards `X-Request-Id` when a request context ID is present. No assumption is made that all arbitrary client headers or every failure propagate that ID.

## Gateway-observed stages

P-side selection and execution can log candidate/decision data, queueing and Prefill timing. The D Gateway records dispatch, first nonempty stream chunk, completion/failure/cancellation and the timing summary. Gateway scheduling records describe selection, not actual remote memory transfer. Resource records have throttling and idle pause settings independent of heartbeat frequency.

## Instance-observed stages

Instance reporting combines vLLM metrics, NVML and host network samples and sends them to its Gateway; the Gateway stores and may log those observations. They are not individually exported as an end-to-end trace. See the [field audit](../resource_state_metrics.md), [Instance guide](../../instance/README.md) and [real NIXL checks](../quickstart_v1.md).
