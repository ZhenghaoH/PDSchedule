# router.py
"""
路由配置模块：定义所有API路由
- 专注路由定义，不包含业务逻辑
- 通过依赖注入关联各处理器
- 保持原setup_routes的路由结构
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from core.handlers.controller.proxy import create_chat_completions, create_completions
from core.handlers.controller.update import update_decode_pool_status, update_prefill_pool_status, register_decode_pool, register_prefill_pool, unregister_decode_pool, unregister_prefill_pool, register_network_link, update_network_status

from core.handlers.controller.req_display import show_requests

from core.handlers.controller.knowledge import debug_status, debug_peek_knowledge, admin_refresh_knowledge

router = APIRouter()

# 注册路由
router.post("/v1/chat/completions")(create_chat_completions)
router.post("/v1/completions")(create_completions)

router.post("/decode_pool/update")(update_decode_pool_status)
router.post("/prefill_pool/update")(update_prefill_pool_status)
router.post("/decode_pool/register")(register_decode_pool)
router.post("/prefill_pool/register")(register_prefill_pool)
router.post("/decode_pool/unregister")(unregister_decode_pool)
router.post("/prefill_pool/unregister")(unregister_prefill_pool)


router.post("/network/update")(update_network_status)
router.post("/network/register")(register_network_link)

# Web页面路由 - 新增
router.get("/requests", response_class=HTMLResponse)(show_requests)
router.get("/", response_class=HTMLResponse)(show_requests)  # 根路径也可访问

# 知识
router.get("/debug/status")(debug_status)
router.post("/debug/knowledge/peek")(debug_peek_knowledge)
router.post("/admin/refresh_knowledge")(admin_refresh_knowledge)
