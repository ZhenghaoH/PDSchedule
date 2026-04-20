from fastapi import FastAPI
from store.controller.pool_manager import PoolManager


def summarize_knowledge_base(app: FastAPI) -> None:
    """
        展示知识库概况：
          - 当前知识条目数量
          - 前三条的基本信息（id, length, text_abstract）
    """
    table = getattr(app.state, "knowledge_table", None)  # type: ignore
    if table is None:
        print("[CLI] Knowledge base not initialized (app.state.knowledge_table is None).")
        return

    # KnowledgeTable 内部是 _units: Dict[int, KnowledgeUnit]
    units = getattr(table, "_units", {})
    total = len(units)
    print(f"[CLI] Knowledge base entries: {total}")
    if total == 0:
        return

    print("[CLI] First 3 entries:")
    for kid in sorted(units.keys())[:3]:
        unit = units[kid]
        text_preview = unit.text_abstract or ""
        if len(text_preview) > 60:
            text_preview = text_preview[:57] + "..."
        print(f"  - ID={kid}, length={unit.length}, text='{text_preview}'")


def do_show_pool_id():
    """FIXME: 以后用更好的方式展示 池id 和 池IP地址 的关系"""
    manager = PoolManager()
    manager.show_pool_id()
