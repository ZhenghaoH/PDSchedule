# TPOT_warmup.py

import asyncio
import numpy as np
from typing import List, Dict
from transformers import AutoTokenizer
import aiohttp

# --- 模块 1: 导入核心依赖 ---
# 导入 TPOT 回归器和我们之前修改过的、用于测量生成性能的函数
from TPOT_regressor import OnlineTPOTRegressor, measure_generation_performance

# 默认配置可以保留在此处
# (确保 VLLM_CONFIG_DEFAULT 与 TPOT_regressor.py 中可能使用的配置一致)
VLLM_CONFIG_DEFAULT = {
    "host": "172.18.0.250",
    "port": 8000,
    "tokenizer_path": "/home/dell/vLLM/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B/",
    "model_id": "../workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
}

# --- 模块 2: 定义 TPOT 的预热配置 ---
#
# TPOT 的预热需要三个维度: batch_size, prompt_length, 和 generation_length
# 一个好的策略是覆盖不同的组合来捕捉模型在不同上下文长度下的行为

# 1. 定义每个维度的测试范围
BATCH_SIZES_TO_TEST = range(1, 9)
PROMPT_LENGTHS_TO_TEST = [64, 256, 512, 1024] # 覆盖短、中、长 prompt
GENERATION_LENGTHS_TO_TEST = [32, 64, 128, 256, 512] # 覆盖短、中、长生成

# 2. 使用循环和条件逻辑来创建有意义的组合
#    这将生成一个比手动指定多得多，但又比完全笛卡尔积更合理的配置列表。
TPOT_WARM_UP_CONFIGS_DEFAULT = []

for bs in BATCH_SIZES_TO_TEST:
    for pl in PROMPT_LENGTHS_TO_TEST:
        for gen in GENERATION_LENGTHS_TO_TEST:
            TPOT_WARM_UP_CONFIGS_DEFAULT.append({
                "bs": bs,
                "pl": pl,
                "gen": gen
            })


# --- 模块 3: 核心的预热接口函数 ---

async def initialize_and_warm_up_tpot_regressor(
    warm_up_configs: List[Dict],
    vllm_config: Dict,
    learning_rate: float = 1e-6,
    repeats_per_config: int = 2
) -> OnlineTPOTRegressor:
    """
    初始化并使用真实的生成任务来预热 TPOT 回归模型。
    """
    print("--- 正在初始化并预热 TPOT 回归模型 ---")
    print(f"将要执行 {len(warm_up_configs)} 个独特的测试配置, 每个重复 {repeats_per_config} 次。")
    
    regressor = OnlineTPOTRegressor(learning_rate=learning_rate)

    await regressor.warm_up_from_tests(
        test_configs=warm_up_configs, 
        vllm_config=vllm_config,
        repeats_per_config=repeats_per_config
    )

    print("\n--- ✅ TPOT 模型预热完成 ---")
    initial_coeffs = regressor.get_coefficients()
    if initial_coeffs:
        print("预热后的初始系数为:")
        print(f"  d1={initial_coeffs['d1']:.2e}, d2={initial_coeffs['d2']:.2e}, "
              f"d3={initial_coeffs['d3']:.2e}, d4={initial_coeffs['d4']:.3e}")
    
    return regressor


# --- 模块 4: 独立运行时的测试和验证 ---

if __name__ == "__main__":

    async def run_test_and_validate():
        """
        一个演示函数，用于预热 TPOT 模型并验证其对一个生成任务的总时间预测。
        """
        print("="*60)
        print("正在以独立脚本模式运行 TPOT 预热和验证流程...")
        print("="*60)
        
        # 步骤 1: 调用核心函数获取预热好的模型
        warmed_up_model = await initialize_and_warm_up_tpot_regressor(
            warm_up_configs=TPOT_WARM_UP_CONFIGS_DEFAULT,
            vllm_config=VLLM_CONFIG_DEFAULT,
            repeats_per_config=1 # 本地测试时减少重复次数
        )
        
        print("\n函数调用完成。返回的 TPOT 回归器模型已准备就绪。")
        
        if not (warmed_up_model and warmed_up_model._is_fitted):
            print("\n[ERROR] 模型预热失败，无法继续进行验证。")
            return

        # 步骤 2: 执行真实的在线验证
        print("\n--- 使用返回的模型进行预测和真实验证 ---")
        test_bs, test_pl, test_gen = 4, 300, 150 # 用于验证的配置
        
        # 步骤 A: 使用模型进行预测
        # 我们通过对每个生成步骤的 TPOT 求和来预测总生成时间
        predicted_generation_time = 0.0
        for i in range(test_gen):
            current_context_len = test_pl + i
            predicted_tpot_step = warmed_up_model.predict(test_bs, current_context_len)
            predicted_generation_time += predicted_tpot_step
        
        print(f"对于 (bs={test_bs}, pl={test_pl}, gen={test_gen}) 的请求:")
        print(f"  -> 🧠 预测的总生成时间: {predicted_generation_time*1000:.2f} ms")
        print(f"  -> 🧠 预测的平均 TPOT: {(predicted_generation_time/test_gen)*1000:.2f} ms/token")

        # 步骤 B: 执行真实测试以获取实际值
        print("[INFO] 正在执行实时验证测试以获取真实生成时间...")
        actual_generation_time = None
        actual_avg_tpot = None
        
        try:
            tokenizer = AutoTokenizer.from_pretrained(VLLM_CONFIG_DEFAULT["tokenizer_path"])
            prompt = " ".join(["token"] * test_pl)
            
            async with aiohttp.ClientSession() as session:
                tasks = [
                    measure_generation_performance(session, prompt, VLLM_CONFIG_DEFAULT, test_gen) 
                    for _ in range(test_bs)
                ]
                results = await asyncio.gather(*tasks)
                successful_results = [r for r in results if r and r['output_tokens'] > 0]

                if successful_results:
                    avg_e2e_time = np.mean([r['total_time'] for r in successful_results])
                    avg_ttft = np.mean([r['ttft'] for r in successful_results]) # 使用测量到的 TTFT
                    avg_output_tokens = np.mean([r['output_tokens'] for r in successful_results])
                    
                    actual_generation_time = avg_e2e_time - avg_ttft
                    actual_avg_tpot = actual_generation_time / avg_output_tokens

        except Exception as e:
            print(f"[ERROR] 执行实时验证测试时出错: {e}")

        # 步骤 C: 比较并报告结果
        if actual_generation_time is not None:
            print(f"  -> 📊 实际测量的总生成时间: {actual_generation_time*1000:.2f} ms")
            print(f"  -> 📊 实际测量的平均 TPOT: {actual_avg_tpot*1000:.2f} ms/token")
            
            error = predicted_generation_time - actual_generation_time
            error_percent = (error / actual_generation_time) * 100 if actual_generation_time > 0 else float('inf')
            print(f"  -> 🎯 预测误差 (总时间): {error*1000:.2f} ms ({error_percent:+.2f}%)")
        else:
            print("\n[WARNING] 未能获取到真实的生成时间进行验证。")

    # 运行所有测试
    asyncio.run(run_test_and_validate())