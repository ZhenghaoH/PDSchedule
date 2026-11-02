# Connectors

## Role and directory

`models.py` defines descriptors and sessions; `protocol.py` defines the Adapter interface; `registry.py` and `manager.py` validate, match and manage sessions; `adapters/nixl.py` and `adapters/lmcache.py` implement the two registered adapters. This is application control and request preparation, not the transfer engine itself.

## Protocol and models

A descriptor has `name`, string `version` and `metadata`. A session records its ID, descriptor, P/D endpoint descriptions, context, state and timestamps. The Adapter validates descriptors/Instances, compares endpoint compatibility and builds Prefill/Decode payloads. See [data-model catalog](../doc/developer-handbook/public-api-and-data-models.md).

## Registry, pairing and sessions

The default registry contains only `nixl` and `lmcache`. Controller filters incompatible Pool pairs; each Instance must match its role and Pool Connector. Gateway session endpoints manage in-memory session records. Descriptor version is a compatibility value, not a library release. A session marked active is not a transfer receipt.

## Adapters

| Adapter | Mode | Matching rule | Data-plane boundary |
| --- | --- | --- | --- |
| NIXL | `request_scoped` | Name/version agree; Instances require side-channel host/port and TP metadata. | Prefill `kv_transfer_params` must be propagated; real vLLM/NIXL must move KV. [Guide](../doc/integrations/nixl.md) |
| LMCache | `shared_backend` | Name/version and `backend`, `namespace`, `config_fingerprint` agree and are nonempty. | The engine/backend must be configured independently and actual write/read/reuse measured. [Guide](../doc/integrations/lmcache.md) |

## Extensions, tests and limitations

A new Adapter must implement the protocol and be registered deliberately; it also needs route, compatibility, serving and data-plane verification. No other Adapter is currently registered. `python -m pytest -q test/test_connector_protocol.py test/test_runtime_instance_registration.py` validates local logic, not a real engine or backend. See [configuration](../doc/developer-handbook/configuration-and-interfaces.md#6-connector-descriptor-boundary) for exact descriptor fields.
