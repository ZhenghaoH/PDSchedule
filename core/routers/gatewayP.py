# router.py
"""
路由配置模块：定义所有API路由
- 专注路由定义，不包含业务逻辑
- 通过依赖注入关联各处理器
- 保持原setup_routes的路由结构
"""

from fastapi import APIRouter

from core.handlers.gateway.prefill.proxy import create_completion, create_chat_completion
from core.handlers.gateway.prefill.update import register_instance, update_instance_metric, unregister_instance
from core.handlers.gateway.prefill.network_analysis import analyze_network_transfer
from core.handlers.gateway.prefill.select_instance import select_instance
from core.handlers.gateway.connector import (
    close_connector_session,
    connector_capabilities,
    create_connector_session,
    get_connector_session,
)

router = APIRouter()

# 注册路由
router.post("/v1/completions")(create_completion)
router.post("/v1/chat/completions")(create_chat_completion)

router.post("/instance/register")(register_instance)
router.post("/instance/update")(update_instance_metric)
router.post("/instance/unregister")(unregister_instance)

router.post("/pd/select_instance")(select_instance)
router.get("/connectors")(connector_capabilities)
router.post("/connector/sessions")(create_connector_session)
router.get("/connector/sessions/{session_id}")(get_connector_session)
router.delete("/connector/sessions/{session_id}")(close_connector_session)

router.post("/analysis/network-transfer")(analyze_network_transfer)
