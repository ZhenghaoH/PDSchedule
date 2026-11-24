"""Ascend Mooncake P2P P->D signaling adapter.

Registered as ``mooncake_p2p_ascend`` for deployments whose KV data plane is
vLLM-Ascend's ``MooncakeConnectorV1``. This adapter carries only the HTTP
signaling contract: it shapes the Prefill request, extracts the transfer
parameters from the Prefill response, and replays them on the Decode request.

It does **not** move KV cache data. The Decode worker pulls the cache through
its own TransferEngine and notifies the Prefill worker when it is done. Nothing
here allocates blocks, rewrites ``remote_host`` / ``remote_port`` /
``remote_request_id``, or treats the first response chunk as a completion
signal.

Behavior is inherited verbatim from :class:`connectors.adapters.nixl.NixlAdapter`.
Only the registration name differs, so ``mooncake_p2p_ascend`` and ``nixl``
never pair with each other, and diagnostics report whichever name is active.
"""

from __future__ import annotations

from connectors.adapters.nixl import NixlAdapter


class MooncakeP2PAdapter(NixlAdapter):
    """Mooncake P2P signaling under its own registration name."""

    name = "mooncake_p2p_ascend"
