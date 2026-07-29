from store.controller.pool_manager import PoolManager


def do_show_pool_id():
    """FIXME: 以后用更好的方式展示 池id 和 池IP地址 的关系"""
    manager = PoolManager()
    manager.show_pool_id()
