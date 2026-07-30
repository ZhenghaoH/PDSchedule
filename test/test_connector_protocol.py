import ast
import threading
from pathlib import Path

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


def test_registry_only_exposes_first_phase_connectors():
    manager = ConnectorManager()
    assert manager.registry.names == ("lmcache", "nixl")
    assert manager.registry.get("nixl").mode == ConnectorMode.REQUEST_SCOPED
    assert manager.registry.get("lmcache").mode == ConnectorMode.SHARED_BACKEND
    with pytest.raises(ValueError, match="unsupported connector"):
        manager.validate({"name": "mooncake"})


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
