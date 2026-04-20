from dataclasses import fields
from typing import Any, Dict, List


def dataclass_from_dict(dc_cls, data: Dict[str, Any]):
    """
    安全地从 dict 构造 dataclass：
      - 只取 dataclass 中定义过的字段，避免因为多余字段报错
      - 必填字段如果缺失，会抛 TypeError，说明上游传的结构不对
    """
    if data is None:
        data = {}
    field_names = {f.name for f in fields(dc_cls)}
    filtered = {k: v for k, v in data.items() if k in field_names}
    return dc_cls(**filtered)


