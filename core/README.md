# Core

## Module overview

| Directory | Responsibility |
| --- | --- |
| `schemas/` | Request and console-command models. |
| `routers/` | Controller, P/D Gateway and Instance HTTP route registration. |
| `handlers/` | Inference forwarding, registration, updates, Connector sessions and request page. |
| `lifespans/` | Service initialization and reporting loops. |
| `consoles/`, `commands/` | Controller and Gateway interactive controls. |

## Runtime compatibility boundary

Core coordinates the current service APIs; it does not supply a knowledge-retrieval service, a CacheRoute runtime profile, or a serving engine. Use [runtime compatibility](../doc/runtime_compatibility_v1.md) for deployment boundaries.

## Configuration

Service-specific defaults and address parsing live under `config/`. Environment is read on import for some settings, so a universal CLI/environment precedence rule does not apply. See the [configuration index](../doc/developer-handbook/configuration-and-interfaces.md).

## Request model

| Model | Role |
| --- | --- |
| `Prompt` | Model, normalized prompt, token count, output limit and sampling options. |
| `Service` | P/D and SLO parameters and endpoint type. |
| `Task` | Selected Gateways, Instances, Connector context and scheduler metadata. |
| `BaseRequest` | Wraps these models with `Request_ID` and type for internal forwarding. |

See [request source](schemas/request.py) and the [data-model catalog](../doc/developer-handbook/public-api-and-data-models.md).

## Request building workflow

Controller parses a client request, estimates prompt tokens and service settings, selects a compatible P/D route, and forwards the internal envelope. Tokenizer loading can fall back to character counting. The documented endpoints accept a bounded set of fields; they are not a complete OpenAI proxy.

## Component addresses and connectivity

Controller defaults to port 5000 and P/D Gateways to 8081/8082 when configured; host values must be supplied when empty. Instance and vLLM ports are explicit. Addresses must be reachable from the caller; binding to `0.0.0.0` does not make it a valid advertised remote address. [Quick start](../doc/quickstart_v1.md) gives a complete topology and checks.

## Notes

FastAPI documentation endpoints are not a readiness or physical KV-transfer check. There is no knowledge selection/injection subsystem in this application. [HTTP index](../doc/developer-handbook/configuration-and-interfaces.md#4-http-interfaces) lists actual routes.
