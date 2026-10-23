# TPOT_prediction_server.py

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel, Field
import uvicorn

# --- 模块 1: 导入您的核心 TPOT 预测接口 ---
try:
    # 导入 TPOT 预测器中的两个核心函数
    if __package__:
        from .TPOT_predictor import predict_tpot, predict_generation_time
    else:
        from TPOT_predictor import predict_tpot, predict_generation_time
except ImportError as e:
    print(f"导入错误: {e}")
    print("请确保此脚本与 TPOT_predictor.py 及其依赖位于同一目录中。")
    exit(1)


# --- 模块 2: FastAPI 的生命周期管理 ---
#
# 在服务器启动时触发 TPOT 模型的预热。
#
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 服务器启动时执行的代码 ---
    print("TPOT 服务器正在启动... 开始 TPOT 模型预热。")
    try:
        # 我们调用一次 predict_tpot 来触发其内部的单例初始化流程。
        # 这里的参数只是为了触发流程，值不重要。
        await predict_tpot(batch_size=1, context_length=1)
        print("TPOT 模型预热成功，服务器已准备就绪。")
    except Exception as e:
        print(f"TPOT 模型预热失败，服务器启动中止！错误: {e}")
        raise
    
    yield
    
    # --- 服务器关闭时执行的代码 ---
    print("TPOT 服务器正在关闭。")


# --- 模块 3: 创建 FastAPI 应用和 API 端点 ---

app = FastAPI(
    title="vLLM TPOT Predictor API",
    description="一个在线服务，用于预测生成性能 (TPOT 和总生成时间)。",
    version="1.0.0",
    lifespan=lifespan
)

# --- 为 /predict_tpot 端点定义数据模型 ---
class TpotPredictionRequest(BaseModel):
    batch_size: int
    context_length: int = Field(..., description="当前的上下文总长度 (prompt_tokens + 已生成的 tokens)")

class TpotPredictionResponse(BaseModel):
    predicted_tpot_seconds: float
    predicted_tpot_ms: float

# --- 为 /predict_generation_time 端点定义数据模型 ---
class GenTimePredictionRequest(BaseModel):
    batch_size: int
    prompt_length: int
    generation_length: int

class GenTimePredictionResponse(BaseModel):
    predicted_generation_time_seconds: float
    predicted_generation_time_ms: float
    predicted_average_tpot_ms: float

@app.get("/", summary="健康检查接口")
async def read_root():
    """返回一个简单的 JSON 消息，表示服务正在运行。"""
    return {"status": "ok", "message": "TPOT Predictor is running."}


@app.post("/predict_tpot", 
          summary="预测单个 Token 的生成时间 (TPOT)", 
          response_model=TpotPredictionResponse)
async def handle_tpot_prediction(request: TpotPredictionRequest):
    """
    接收批处理大小和当前上下文长度，返回下一步的 TPOT 预测。
    """
    prediction_sec = await predict_tpot(
        batch_size=request.batch_size, 
        context_length=request.context_length
    )
    return {
        "predicted_tpot_seconds": prediction_sec,
        "predicted_tpot_ms": prediction_sec * 1000
    }


@app.post("/predict_generation_time",
          summary="预测完整的生成时间",
          response_model=GenTimePredictionResponse)
async def handle_generation_time_prediction(request: GenTimePredictionRequest):
    """
    接收批处理大小、提示长度和生成长度，返回预测的总生成时间。
    """
    prediction_sec = await predict_generation_time(
        batch_size=request.batch_size,
        prompt_length=request.prompt_length,
        generation_length=request.generation_length
    )
    avg_tpot_ms = (prediction_sec / request.generation_length * 1000) if request.generation_length > 0 else 0
    
    return {
        "predicted_generation_time_seconds": prediction_sec,
        "predicted_generation_time_ms": prediction_sec * 1000,
        "predicted_average_tpot_ms": avg_tpot_ms
    }


# --- 模块 4: 运行服务器 ---
if __name__ == "__main__":
    print("正在启动 uvicorn 服务器 for TPOT Predictor...")
    # 为避免端口冲突，使用一个与 TTFT 服务器不同的端口，例如 9001
    uvicorn.run("TPOT_prediction_server:app", host="0.0.0.0", port=9001, reload=True)
