# router.py
"""
路由配置模块：定义所有API路由
- 专注路由定义，不包含业务逻辑
- 通过依赖注入关联各处理器
- 保持原setup_routes的路由结构
"""

from fastapi import APIRouter

from core.handlers.instance.proxy import create_completion, create_chat_completion
from core.handlers.instance.metadata import handle_metadata
from core.handlers.instance.pair import handle_pd_pair, handle_pd_close

router = APIRouter()

# 注册路由
router.post("/v1/completions")(create_completion)
router.post("/v1/chat/completions")(create_chat_completion)

router.get("/metadata")(handle_metadata)
router.put("/metadata")(handle_metadata)
router.delete("/metadata")(handle_metadata)

router.post("/pd/pair")(handle_pd_pair)
router.post("/pd/close")(handle_pd_close)
