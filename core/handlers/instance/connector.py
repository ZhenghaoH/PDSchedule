from connectors.manager import get_connector_manager
from store.instance.store import InstanceStore


async def connector_capabilities():
    descriptor = get_connector_manager().validate(InstanceStore().connector)
    adapter = get_connector_manager().registry.get(descriptor.name)
    return {
        "connector": descriptor.to_dict(),
        "mode": adapter.mode.value,
    }
