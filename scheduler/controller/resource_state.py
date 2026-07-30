import copy
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from connectors.manager import get_connector_manager
from store.controller.network import Network, NetworkManager
from store.controller.pool_base import DecodePool, PoolType, PrefillPool
from store.controller.pool_manager import PoolManager


@dataclass(frozen=True)
class ResourceSnapshot:
    version: int
    captured_at: float
    prefill_pools: Tuple[PrefillPool, ...]
    decode_pools: Tuple[DecodePool, ...]
    networks: Tuple[Network, ...]
    network_map: Mapping[Tuple[int, int], Network]
    compatible_pairs: Optional[
        Tuple[Tuple[PrefillPool, DecodePool], ...]
    ] = None

    def __post_init__(self):
        if self.compatible_pairs is None:
            connector_manager = get_connector_manager()
            object.__setattr__(
                self,
                "compatible_pairs",
                tuple(
                    (prefill, decode)
                    for prefill in self.prefill_pools
                    for decode in self.decode_pools
                    if connector_manager.compatible(
                        prefill.connector, decode.connector
                    )
                ),
            )

    @classmethod
    def empty(cls) -> "ResourceSnapshot":
        return cls(
            version=0,
            captured_at=time.time(),
            prefill_pools=(),
            decode_pools=(),
            networks=(),
            network_map=MappingProxyType({}),
            compatible_pairs=(),
        )


class ResourceStateManager:
    """Publish versioned resource snapshots for controller scheduling."""

    def __init__(
        self,
        pool_manager: PoolManager,
        network_manager: NetworkManager,
    ):
        self.pool_manager = pool_manager
        self.network_manager = network_manager
        self._refresh_lock = threading.RLock()
        self._snapshot = ResourceSnapshot.empty()

    def snapshot(self) -> ResourceSnapshot:
        with self._refresh_lock:
            return self._snapshot

    def refresh(self) -> ResourceSnapshot:
        with self._refresh_lock:
            # NetworkManager already uses network -> pool when both stores are
            # needed. Keep the same lock order here to avoid lock inversion.
            with ExitStack() as stack:
                for manager in (self.network_manager, self.pool_manager):
                    lock = getattr(manager, "db_lock", None)
                    if lock is not None and hasattr(lock, "__enter__"):
                        stack.enter_context(lock)
                active_prefills = self.pool_manager.get_active_Pools(
                    PoolType.PREFILL
                )
                active_decodes = self.pool_manager.get_active_Pools(
                    PoolType.DECODE
                )
                active_networks = self.network_manager.get_all_networks()
                copied_prefills, copied_decodes, copied_networks = (
                    copy.deepcopy(
                        (
                            active_prefills,
                            active_decodes,
                            active_networks,
                        )
                    )
                )

            prefill_ids = {pool.pool_id for pool in copied_prefills}
            decode_ids = {pool.pool_id for pool in copied_decodes}
            filtered_networks = tuple(
                network
                for network in copied_networks
                if network.prefill_pool.pool_id in prefill_ids
                and network.decode_pool.pool_id in decode_ids
            )
            network_map = MappingProxyType(
                {
                    (
                        network.prefill_pool.pool_id,
                        network.decode_pool.pool_id,
                    ): network
                    for network in filtered_networks
                }
            )
            connector_manager = get_connector_manager()
            compatible_pairs = tuple(
                (prefill, decode)
                for prefill in copied_prefills
                for decode in copied_decodes
                if connector_manager.compatible(
                    prefill.connector, decode.connector
                )
            )
            self._snapshot = ResourceSnapshot(
                version=self._snapshot.version + 1,
                captured_at=time.time(),
                prefill_pools=tuple(copied_prefills),
                decode_pools=tuple(copied_decodes),
                networks=filtered_networks,
                network_map=network_map,
                compatible_pairs=compatible_pairs,
            )
            return self._snapshot
