# router.py
"""
路由配置模块：定义所有API路由
- 专注路由定义，不包含业务逻辑
- 通过依赖注入关联各处理器
- 保持原setup_routes的路由结构
"""

from fastapi import APIRouter

from core.handlers.gateway.decode.proxy import create_completion, create_chat_completion
from core.handlers.gateway.decode.update import update_instance_metric, unregister_instance
from core.handlers.gateway.decode.pair import handle_pd_pair, handle_pd_close
from core.handlers.gateway.decode.select_instance import select_instance

router = APIRouter()

# 注册路由
router.post("/v1/completions")(create_completion)
router.post("/v1/chat/completions")(create_chat_completion)

router.post("/instance/update")(update_instance_metric)
router.post("/instance/unregister")(unregister_instance)

router.post("/pd/pair")(handle_pd_pair)
router.post("/pd/close")(handle_pd_close)
router.post("/pd/select_instance")(select_instance)
