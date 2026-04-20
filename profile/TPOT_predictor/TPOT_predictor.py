# TPOT_predictor.py

import asyncio
from typing import Optional

# --- 模块 1: 导入核心依赖 ---
# 导入 TPOT 回归器和其预热函数
from TPOT_regressor import OnlineTPOTRegressor
from TPOT_warmup import (
    initialize_and_warm_up_tpot_regressor,
    VLLM_CONFIG_DEFAULT,
    TPOT_WARM_UP_CONFIGS_DEFAULT
)

# --- 模块 2: 单例模式实现 ---
#
# _tpot_model 将在第一次调用时被初始化，并在后续调用中被重用。
# _tpot_model_lock 用于防止并发初始化。
#
_tpot_model: Optional[OnlineTPOTRegressor] = None
_tpot_model_lock = asyncio.Lock()


async def _get_warmed_up_model() -> OnlineTPOTRegressor:
    """
    一个内部函数，用于安全地获取已预热的单例模型实例。
    如果模型未初始化，它将触发预热流程。
    """
    global _tpot_model
    
    # 检查是否需要加锁，以避免在模型已存在时不必要的性能开销
    if _tpot_model is None:
        async with _tpot_model_lock:
            # 双重检查，确保在等待锁的过程中模型没有被其他协程初始化
            if _tpot_model is None:
                print("[TPOT Predictor] TPOT 模型尚未初始化。正在开始首次预热流程...")
                
                model_instance = await initialize_and_warm_up_tpot_regressor(
                    warm_up_configs=TPOT_WARM_UP_CONFIGS_DEFAULT,
                    vllm_config=VLLM_CONFIG_DEFAULT
                )
                
                if not model_instance:
                    raise RuntimeError("TPOT 预测模型初始化失败！")
                
                _tpot_model = model_instance
                print("[TPOT Predictor] TPOT 模型已成功预热并准备就绪。")
    
    return _tpot_model


# --- 模块 3: 对外暴露的核心接口函数 ---

async def predict_tpot(batch_size: int, context_length: int) -> float:
    """
    预测在给定批处理大小和当前上下文长度下，生成下一个 token 所需的时间 (TPOT)。

    参数:
        batch_size (int): 请求的批处理大小。
        context_length (int): 当前的上下文总长度 (prompt_tokens + 已生成的 tokens)。

    返回:
        float: 预测的单个 token 生成时间 (秒)。
    """
    model = await _get_warmed_up_model()
    
    if not model._is_fitted:
        print("[WARNING] TPOT 模型尚未拟合，预测将不准确。")
        return 0.0

    return model.predict_tpot(batch_size, context_length)


async def predict_generation_time(
    batch_size: int,
    prompt_length: int,
    generation_length: int
) -> float:
    """
    预测完成一个完整生成序列（不含TTFT）所需的总时间。

    它通过对序列中每一步的 TPOT 进行求和来计算总时间。

    参数:
        batch_size (int): 请求的批处理大小。
        prompt_length (int): 初始 prompt 的 token 长度。
        generation_length (int): 期望生成的 token 数量。

    返回:
        float: 预测的总生成时间（秒）。
    """
    model = await _get_warmed_up_model()

    if not model._is_fitted:
        print("[WARNING] TPOT 模型尚未拟合，预测将不准确。")
        return 0.0

    # total_time = 0.0
    # for i in range(generation_length):
    #     # 上下文长度在每一步都会增加
    #     current_context_len = prompt_length + i
    #     # 调用模型预测这一步的 TPOT
    #     step_tpot = model.predict(batch_size, current_context_len)
    #     total_time += step_tpot
        
    return model.predict_generation_time(batch_size, prompt_length, generation_length)


# --- 模块 4: 演示如何使用此接口 ---

if __name__ == "__main__":

    async def demo():
        print("="*60)
        print("--- 演示如何使用 TPOT_predictor.py ---")
        print("="*60)

        # --- 示例 1: 预测单个 TPOT ---
        print("\n--- 示例 1: 预测单个 TPOT ---")
        # 第一次调用任一预测函数都会触发模型预热
        print("(第一次调用将触发模型预热，会比较慢...)")
        bs, pl, generated = 4, 256, 50
        ctx_len = pl + generated
        
        tpot_prediction = await predict_tpot(batch_size=bs, context_length=ctx_len)
        print(f"\n---> 对于 (bs={bs}, 已有上下文={ctx_len})，预测下一步的 TPOT 为: "
              f"{tpot_prediction * 1000:.2f} ms")
        
        # --- 示例 2: 预测总生成时间 ---
        print("\n\n--- 示例 2: 预测总生成时间 ---")
        # 第二次调用会很快
        bs, pl, gen_len = 8, 128, 100
        
        total_time_prediction = await predict_generation_time(
            batch_size=bs, prompt_length=pl, generation_length=gen_len
        )
        avg_tpot = total_time_prediction / gen_len if gen_len > 0 else 0
        
        print(f"---> 对于 (bs={bs}, pl={pl}, gen_len={gen_len}):")
        print(f"     预测的总生成时间为: {total_time_prediction * 1000:.2f} ms")
        print(f"     预测的平均 TPOT 为: {avg_tpot * 1000:.2f} ms/token")

        # --- 示例 3: 并发调用 ---
        print("\n\n--- 示例 3: 并发调用总时间预测 ---")
        tasks = [
            predict_generation_time(batch_size=2, prompt_length=512, generation_length=50),
            predict_generation_time(batch_size=4, prompt_length=1024, generation_length=30),
        ]
        concurrent_results = await asyncio.gather(*tasks)
        print("并发请求的预测结果:")
        for i, res in enumerate(concurrent_results):
            print(f"  -> 请求 {i+1}: 预测总时间 = {res * 1000:.2f} ms")

    # 运行演示
    asyncio.run(demo())