"""Shared Controller registration and Instance expiry workers for Gateways."""

import asyncio
import logging
import threading
from typing import Callable, Optional

from coord.gateway.up_store import (
    decode_compute_register,
    decode_compute_update,
    network_update,
    prefill_compute_register,
    prefill_compute_update,
)
from store.gateway.manager import GatewayStoreManager


def _backoff_seconds(attempt: int, maximum: float) -> float:
    return min(float(2 ** min(max(0, attempt), 8)), max(1.0, float(maximum)))


def run_gateway_management_loop(
    role: str,
    stop_event: threading.Event,
    registered_event: threading.Event,
    heartbeat_interval: float,
    retry_max_interval: float,
    storage: Optional[GatewayStoreManager] = None,
) -> None:
    """Keep one P/D Gateway registered and publish its current resource state."""
    storage = storage or GatewayStoreManager()
    if role == "prefill":
        register: Callable[[], str] = prefill_compute_register
        update: Callable[[], str] = prefill_compute_update
        report_network = True
    elif role == "decode":
        register = decode_compute_register
        update = decode_compute_update
        report_network = False
    else:
        raise ValueError(f"Unsupported Gateway role: {role}")

    attempt = 0
    ever_registered = False
    while not stop_event.is_set():
        pool_id = storage.get_pool_id()
        if pool_id is None:
            result = register()
            if result != "success":
                delay = _backoff_seconds(attempt, retry_max_interval)
                attempt += 1
                stop_event.wait(delay)
                continue

            if not ever_registered:
                registered_event.set()
                ever_registered = True
            attempt = 0

        if stop_event.is_set():
            break

        result = update()
        if result == "registration_lost":
            # The update helper clears only the ID it just sent. Re-register
            # immediately so load and network state are restored together.
            attempt = 0
            continue

        if result == "success" and not ever_registered:
            # An in-process restart may retain a valid ID; the first accepted
            # identity-bound update proves that it is still bound correctly.
            registered_event.set()
            ever_registered = True

        if report_network and not stop_event.is_set():
            network_update()

        if result == "success":
            attempt = 0
            delay = max(0.01, float(heartbeat_interval))
        else:
            delay = _backoff_seconds(attempt, retry_max_interval)
            attempt += 1
        stop_event.wait(delay)


def run_instance_expiry_loop(
    stop_event: threading.Event,
    storage: Optional[GatewayStoreManager] = None,
) -> None:
    """Expire stale Instance heartbeats without touching in-flight request state."""
    storage = storage or GatewayStoreManager()
    interval = max(
        0.01,
        float(getattr(storage, "INSTANCE_EXPIRY_SCAN_INTERVAL_SEC", 1.0)),
    )
    while not stop_event.is_set():
        for role, instance_ip, age in storage.expire_stale_instances():
            logging.warning(
                "[Gateway] Instance heartbeat expired role=%s instance=%s age=%.3fs",
                role,
                instance_ip,
                age,
            )
        stop_event.wait(interval)


async def wait_for_first_registration(
    registered_event: threading.Event,
    stop_event: threading.Event,
    management_thread: threading.Thread,
) -> bool:
    """Wait for a valid pool ID without blocking the lifespan event loop."""
    while not registered_event.is_set():
        if stop_event.is_set() or not management_thread.is_alive():
            return False
        await asyncio.sleep(0.05)
    return True
