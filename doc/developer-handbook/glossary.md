# Glossary

| Term | Definition | Status |
| --- | --- | --- |
| Prefill (P) | Process prompt tokens and prepare KV state. | Current |
| Decode (D) | Generate output tokens using model context. | Current |
| Pool / Gateway | Gateway-managed group of same-role Instances exposed to Controller as a Pool. | Current |
| Instance Proxy | Process that registers with a Gateway, reports metrics and forwards to its local vLLM. | Current |
| TTFT / TPOT | Time to first token / time per output token; SLO values are in milliseconds. | Current |
| SLO | Target service objective used by scheduling and prediction where configured. | Current |
| JSQ | Prefill strategy using locally reserved unfinished prompt tokens. | Current |
| Reservation | Local accounting for selected work before it is released; not GPU memory reservation. | Current |
| Request ID | Controller request identifier carried in internal state; NIXL may use `X-Request-Id`. | Current |
| Connector session | Application-side selected endpoint and descriptor context; not proof of physical data transfer. | Current |
| Side channel | NIXL engine coordination address and port included in Instance metadata. | Current |
| Measured / predicted / configured | Observed sample / model output / operator or default input; do not conflate their provenance. | Current |
| KV reuse / transfer | Reusing cached key/value state / moving it between serving engines or via a shared backend. | Deployment-dependent |
| Resource snapshot | Versioned Controller view of Pools, links and compatible P/D pairs. | Internal |
