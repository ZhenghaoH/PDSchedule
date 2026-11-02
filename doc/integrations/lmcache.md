# PDSchedule + LMCache integration

## Why LMCache?

LMCache can provide KV storage and reuse for independently configured serving engines. PDSchedule's Adapter models a shared-backend relationship; installing the Adapter does not start or configure LMCache, Redis, vLLM or a storage backend.

## What PDSchedule adds

The `lmcache` Adapter checks matching Pool and Instance descriptors and constructs Prefill/Decode payloads. It marks the Connector `shared_backend`, requires no NIXL-style per-request transfer context, uses non-streaming one-token Prefill and forwards the Decode request. `backend`, `namespace` and `config_fingerprint` are PDSchedule compatibility metadata, not settings automatically passed to LMCache.

## Environment and compatibility

The NIXL [environment guide](../../env/README.md) does not install LMCache or Redis. A compatible vLLM/LMCache integration, common model/tokenizer/cache identity, and reachable shared store must be configured for the exact engine versions. This repository does not establish a universally tested LMCache version matrix or a complete, verified LMCache deployment recipe. Native LMCache PD/P2P modes have their own serving configuration and must not be inferred from this shared-backend Adapter.

## Configuration

### Configuration loading

Configure the serving engine and LMCache according to the installed versions; the application does not load the repository's [example YAML](../../lmcache.yaml) into vLLM or rewrite it from Connector metadata. Ensure the serving process actually loads its intended configuration. The example contains a fixed private-network address and is not a universal configuration.

### YAML and environment variables

The repository example uses YAML fields below. Their interpretation and supported values depend on the installed LMCache version. `FLEXPD_REDIS_HOST`/`FLEXPD_REDIS_PORT` declarations do not configure LMCache's `remote_url`; see the [configuration index](../developer-handbook/configuration-and-interfaces.md).

| Group | Example field | Example value | Meaning / boundary |
| --- | --- | --- | --- |
| Chunking | `chunk_size`, `save_unfull_chunk` | `32`, `true` | Serving-side cache chunk settings; confirm version support. |
| Local storage | `local_cpu`, `max_local_cpu_size`, `local_disk`, `max_local_disk_size` | `false`, `16.0`, `null`, `0` | Example local backend options; size semantics are LMCache-version specific. |
| Shared storage | `remote_url`, `remote_serde` | Redis URL, `cachegen` | Backend address and serialization; replace address for your deployment and confirm support. |
| Policy | `save_decode_cache`, `cache_policy`, `numa_mode` | `false`, `LRU`, `null` | Example serving-side behavior; not Connector descriptor fields. |
| Layerwise | `use_layerwise` | commented out | Inactive in the example; confirm version support before enabling. |

### PDSchedule Connector metadata

Pass `--connector-name lmcache --connector-version 1 --connector-metadata '<JSON object>'` to each Gateway and Instance. All three metadata values must be nonempty and equal across compatible endpoints:

| Key | Purpose | Boundary |
| --- | --- | --- |
| `backend` | Declared shared backend identity | Equality check only; does not open a backend. |
| `namespace` | Declared cache namespace identity | Equality check only; does not enforce backend namespace configuration. |
| `config_fingerprint` | Operator-provided compatibility fingerprint | Equality check only; PDSchedule does not derive or verify it against actual YAML. |

Descriptor `version` must also match. Registration only proves that these supplied values agree, not that the model, tokenizer, backend, engine options or actual stored keys agree.

## Deployment and request flow

Start the independently prepared backend and compatible engines, configure all P/D application services with matching descriptors, and verify registration before sending a Controller request. Prefill populates the backend only if the serving stack is correctly configured; Decode can reuse data only if the actual cache identity and backend allow it. For application component order see [quick start](../quickstart_v1.md); its vLLM commands and NIXL settings are not an LMCache recipe.

## Validation

Inspect the loaded serving configuration, backend connectivity and actual cache write/read/hit metrics or logs for the same request and data; verify output correctness. Record exact versions, engine settings and backend. Connector CPU tests and `/connectors` validate metadata logic only; they do not prove a cache write, read or hit.

## Troubleshooting and limitations

On rejected registration, compare all three metadata keys, Connector names and descriptor version at the Pool and Instance. On absent reuse, separately inspect loaded LMCache configuration, shared namespace/key behavior, model/tokenizer identity, actual store connectivity and serving logs. Native PD/P2P mode is a different integration boundary. Do not claim compatibility or cache reuse without testing the exact serving combination.
