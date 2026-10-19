import ast
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from connectors.manager import ConnectorManager
from connectors.models import ConnectorMode, ConnectorSessionState
from scheduler.controller.resource_state import ResourceStateManager
from scheduler.controller.resource_state import ResourceSnapshot
from scheduler.controller.strategy import GlobalScheduleContext
from scheduler.controller.strategies.demand_aware import DemandAwareStrategy
from scheduler.controller.strategies.least_load import LeastLoadStrategy
from scheduler.controller.strategies.round_robin import RoundRobinStrategy
from store.controller.network import Network
from store.controller.pool_base import DecodePool, PoolType, PrefillPool
from store.gateway.manager import GatewayStoreManager


ROOT = Path(__file__).resolve().parents[1]
NIXL_POOL = {"name": "nixl", "version": "1", "metadata": {}}
LMCACHE = {
    "name": "lmcache",
    "version": "1",
    "metadata": {
        "backend": "redis",
        "namespace": "model-a",
        "config_fingerprint": "abc123",
    },
}

MOONCAKE_POOL = {
    "name": "mooncake_p2p_hccl",
    "version": "1",
    "metadata": {
        "platform": "ascend",
        "transport": "hccl",
        "connector_impl": "MooncakeConnectorV1",
        "handshake_model": "passive_relay",
        "runtime_fingerprint": "runtime-a",
        "model_fingerprint": "model-a",
        "fabric_id": "fabric-a",
        "dp_size": 1,
        "tp_size": 4,
        "pp_size": 1,
    },
}


def mooncake_instance(
    host="10.0.0.1",
    port=28000,
    engine_id="engine-0",
    epoch="boot-1",
):
    return {
        "name": "mooncake_p2p_hccl",
        "version": "1",
        "metadata": {
            **MOONCAKE_POOL["metadata"],
            "engine_id": engine_id,
            "instance_epoch": epoch,
            "side_channel_host": host,
            "side_channel_port": port,
            "device_count": 4,
            "rank_start": 0,
        },
    }


def nixl_instance(host="10.0.0.1"):
    return {
        "name": "nixl",
        "version": "1",
        "metadata": {
            "side_channel_host": host,
            "side_channel_port": 5557,
            "tp_size": 1,
        },
    }


class Pools:
    db_lock = threading.RLock()

    def __init__(self, prefills, decodes):
        self.prefills = prefills
        self.decodes = decodes

    def get_active_Pools(self, pool_type):
        return self.prefills if pool_type == PoolType.PREFILL else self.decodes


class Networks:
    db_lock = threading.RLock()

    @staticmethod
    def get_all_networks():
        return []


def gateway_store(pool_connector):
    storage = object.__new__(GatewayStoreManager)
    storage._data_lock = threading.RLock()
    storage._prefill_objs = []
    storage._decode_objs = []
    storage._instance_table = {}
    storage.connector = pool_connector
    return storage


def test_registry_exposes_supported_connectors():
    manager = ConnectorManager()
    assert manager.registry.names == ("lmcache", "mooncake_p2p_hccl", "nixl")
    assert manager.registry.get("nixl").mode == ConnectorMode.REQUEST_SCOPED
    assert manager.registry.get("lmcache").mode == ConnectorMode.SHARED_BACKEND
    assert (
        manager.registry.get("mooncake_p2p_hccl").mode
        == ConnectorMode.REQUEST_SCOPED
    )
    with pytest.raises(ValueError, match="unsupported connector"):
        manager.validate({"name": "mooncake"})


def test_mooncake_hccl_pool_compatibility_requires_same_runtime_model_and_fabric():
    manager = ConnectorManager()
    assert manager.compatible(MOONCAKE_POOL, MOONCAKE_POOL)
    for key, value in (
        ("runtime_fingerprint", "runtime-b"),
        ("model_fingerprint", "model-b"),
        ("fabric_id", "fabric-b"),
        ("tp_size", 2),
    ):
        changed = {
            **MOONCAKE_POOL,
            "metadata": {**MOONCAKE_POOL["metadata"], key: value},
        }
        assert not manager.compatible(MOONCAKE_POOL, changed)


def test_mooncake_hccl_relay_validates_selected_prefill_endpoint():
    manager = ConnectorManager()
    prefill = mooncake_instance(engine_id="prefill-0", epoch="p-1")
    decode = mooncake_instance(
        host="10.0.0.2", port=29000, engine_id="decode-0", epoch="d-1"
    )
    request = SimpleNamespace(
        Task=SimpleNamespace(
            pd_connector="mooncake_p2p_hccl",
            connector_context={
                "request_id": "req-1",
                "prefill_endpoint": {"role": "prefill", "descriptor": prefill},
            }
        )
    )
    assert manager.compatible_endpoints(
        {"role": "prefill", "descriptor": prefill},
        {"role": "decode", "descriptor": decode},
    )
    with patch(
        "core.schemas.request.BaseRequest.to_vllm_payload",
        return_value={"prompt": "hello"},
    ):
        payload = manager.registry.get("mooncake_p2p_hccl").build_prefill_payload(
            request
        )
    assert payload["kv_transfer_params"]["do_remote_decode"] is True

    params = {
        "do_remote_decode": True,
        "do_remote_prefill": False,
        "remote_engine_id": "prefill-0",
        "remote_block_ids": [1, 2],
        "remote_host": "10.0.0.1",
        "remote_port": 28001,
    }
    class FakeHTTPException(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fake_fastapi = types.ModuleType("fastapi")
    fake_fastapi.HTTPException = FakeHTTPException
    with patch.dict(sys.modules, {"fastapi": fake_fastapi}):
        manager.bind_prefill_result(request, {"kv_transfer_params": params})
    with (
        patch.dict(sys.modules, {"fastapi": fake_fastapi}),
        patch(
            "core.schemas.request.BaseRequest.to_vllm_payload",
            return_value={"prompt": "hello"},
        ),
    ):
        decode_payload = manager.registry.get(
            "mooncake_p2p_hccl"
        ).build_decode_payload(request)
    assert decode_payload["kv_transfer_params"] == params

    with patch.dict(sys.modules, {"fastapi": fake_fastapi}):
        with pytest.raises(Exception, match="does not belong to selected Prefill"):
            manager.bind_prefill_result(
                request,
                {
                    "kv_transfer_params": {
                        **params,
                        "remote_engine_id": "other-prefill",
                    }
                },
            )


def test_lmcache_compatibility_is_shared_configuration_not_fake_rpc():
    manager = ConnectorManager()
    assert manager.compatible(LMCACHE, LMCACHE)
    changed = {
        **LMCACHE,
        "metadata": {**LMCACHE["metadata"], "namespace": "model-b"},
    }
    assert not manager.compatible(LMCACHE, changed)
    session = manager.create_session(
        {"address": "p", "connector": LMCACHE},
        {"address": "d", "connector": LMCACHE},
        LMCACHE,
        session_id="lmcache-test",
    )
    assert session.state == ConnectorSessionState.ACTIVE
    assert session.context == LMCACHE["metadata"]
    assert manager.create_session(
        {"address": "p", "connector": LMCACHE},
        {"address": "d", "connector": LMCACHE},
        LMCACHE,
        session_id="lmcache-test",
    ) is session
    assert manager.close_session("lmcache-test")
    assert session.state == ConnectorSessionState.CLOSED


def test_pool_snapshot_only_publishes_connector_compatible_pairs():
    prefills = [
        PrefillPool(1, "p-nixl", 8001, connector=NIXL_POOL),
        PrefillPool(2, "p-lmcache", 8002, connector=LMCACHE),
    ]
    decodes = [
        DecodePool(201, "d-lmcache", 9001, connector=LMCACHE),
        DecodePool(202, "d-nixl", 9002, connector=NIXL_POOL),
    ]
    snapshot = ResourceStateManager(Pools(prefills, decodes), Networks()).refresh()
    assert {
        (prefill.pool_id, decode.pool_id)
        for prefill, decode in snapshot.compatible_pairs
    } == {(1, 202), (2, 201)}


def test_all_global_strategies_stay_inside_connector_compatible_pairs():
    prefills = (
        PrefillPool(1, "p-nixl", 8001, connector=NIXL_POOL),
        PrefillPool(2, "p-lmcache", 8002, connector=LMCACHE),
    )
    decodes = (
        DecodePool(201, "d-lmcache", 9001, connector=LMCACHE),
        DecodePool(202, "d-nixl", 9002, connector=NIXL_POOL),
    )
    networks = tuple(
        Network(prefill, decode, bandwidth=1000.0, rtt=1.0)
        for prefill in prefills
        for decode in decodes
    )
    resources = ResourceSnapshot(
        1,
        0.0,
        prefills,
        decodes,
        networks,
        {
            (network.prefill_pool.pool_id, network.decode_pool.pool_id): network
            for network in networks
        },
        ((prefills[0], decodes[1]), (prefills[1], decodes[0])),
    )
    context = GlobalScheduleContext(128, 32, 100.0, 20.0, 1.0)
    valid_addresses = {("p-nixl", "d-nixl"), ("p-lmcache", "d-lmcache")}
    for strategy in (
        RoundRobinStrategy(),
        LeastLoadStrategy(),
        DemandAwareStrategy(num_particles=2, max_iterations=1),
    ):
        decision = strategy.schedule(context, resources)
        assert (decision.prefill_address, decision.decode_address) in valid_addresses
        assert decision.connector in {"nixl", "lmcache"}


def test_gateway_rejects_mixed_connector_instances_within_one_pool():
    storage = gateway_store(NIXL_POOL)
    storage.register_instance("p1", "prefill", nixl_instance())
    with pytest.raises(ValueError, match="does not match pool connector"):
        storage.register_instance("p2", "prefill", LMCACHE)


def test_legacy_pair_routes_are_absent():
    for path in (
        ROOT / "core" / "routers" / "gatewayP.py",
        ROOT / "core" / "routers" / "gatewayD.py",
        ROOT / "core" / "routers" / "instance.py",
    ):
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        assert '"/pd/pair"' not in source
        assert '"/pd/close"' not in source
