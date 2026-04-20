# TPOT_regressor.py (Summation-based)

import numpy as np
import asyncio
import aiohttp
from transformers import AutoTokenizer
from sklearn.linear_model import LinearRegression, SGDRegressor
from sklearn.preprocessing import StandardScaler
from typing import List, Tuple, Dict

# Assumes TPOT_measure.py contains the measure_generation_performance function
from TPOT_measure import measure_generation_performance, generate_prompt_with_tokens

class OnlineTPOTRegressor:
    """
    一个更精确的在线回归器，基于对每个生成步骤TPOT的求和来进行拟合。
    """
    def __init__(self, learning_rate: float = 1e-9): # Learning rate may need tuning
        self.online_model = SGDRegressor(
            loss='squared_error', learning_rate='constant', eta0=learning_rate,
            fit_intercept=False, # The intercept d4 is now part of our features
            max_iter=1, tol=None
        )
        self.scaler = StandardScaler()
        self._is_fitted = False
        # The coefficients are now stored directly, as they are the model
        self.coeffs = {'d1': 0, 'd2': 0, 'd3': 0, 'd4': 0}

    def _transform_full_generation_to_features(
        self, batchsize: int, prompt_length: int, gen_length: int
    ) -> np.ndarray:
        """
        将一次完整的生成任务转换为线性回归的特征向量。
        """
        N = batchsize
        P = prompt_length
        G = gen_length

        if G <= 0:
            return np.array([[0, 0, 0, 0]])

        # Calculate Σ(P+i) from i=0 to G-1
        # This is G*P + G*(G-1)/2
        sum_of_lengths = G * P + G * (G - 1) / 2.0

        # These are the four features for our linear regression
        feature1 = N * sum_of_lengths # for d1
        feature2 = N * G             # for d2
        feature3 = sum_of_lengths    # for d3
        feature4 = G                 # for d4

        return np.array([[feature1, feature2, feature3, feature4]])

    def _initialize_model_from_data(self, initial_data: List[Dict]):
        """
        使用批量回归精确初始化模型。
        Data format: [{"bs": N, "pl": P, "gen": G, "gen_time": T}, ...]
        """
        if not initial_data:
            print("[WARNING] 没有数据可用于初始化TPOT模型。"); return

        X_raw_list = []
        y_list = []
        for point in initial_data:
            features = self._transform_full_generation_to_features(
                point["bs"], point["pl"], point["gen"]
            )[0]
            X_raw_list.append(features)
            y_list.append(point["gen_time"])

        X_raw = np.array(X_raw_list)
        y = np.array(y_list)

        print("\n--- 步骤 1: 执行基于求和的TPOT模型直接批量回归 ---")
        # We don't need an intercept because d4 is captured by feature4
        batch_model = LinearRegression(fit_intercept=False).fit(X_raw, y)
        
        d1, d2, d3, d4 = batch_model.coef_
        self.coeffs = {'d1': d1, 'd2': d2, 'd3': d3, 'd4': d4}

        print("✅ TPOT模型直接拟合完成。计算出的最优初始系数为:")
        print(f"   d1={d1:.2e}, d2={d2:.2e}, d3={d3:.2e}, d4={d4:.3e}")

        # Initialize the online components (scaler and model) for future updates
        self.scaler.fit(X_raw)
        X_scaled = self.scaler.transform(X_raw)
        
        # We can't directly "plant" coefficients for SGD easily in this formulation.
        # Instead, we train it on the initial data to get it to a good starting point.
        online_init_model = SGDRegressor(
            fit_intercept=False, eta0=self.online_model.eta0, learning_rate='constant'
        )
        online_init_model.fit(X_scaled, y)
        self.online_model = online_init_model

        self._is_fitted = True
        print("\n--- 步驟 2: 在线TPOT模型已初始化。")

    async def warm_up_from_tests(self, test_configs: List[Dict], vllm_config: Dict, repeats_per_config: int = 2):
        print("--- 🚀 开始TPOT回归器预热测试 (基于求和) ---")
        collected_data = []

        async with aiohttp.ClientSession() as session:
            for config in test_configs:
                bs, pl, gen = config["bs"], config["pl"], config["gen"]
                print(f"\n[INFO] 正在测试配置: bs={bs}, pl={pl}, gen_len={gen}")
                
                try: tokenizer = AutoTokenizer.from_pretrained(vllm_config["tokenizer_path"])
                except Exception as e: print(f"[ERROR] 无法加载 Tokenizer: {e}"); return

                # Use a simple prompt for performance measurement
                prompt = generate_prompt_with_tokens(tokenizer , pl)

                for i in range(repeats_per_config):
                    tasks = [measure_generation_performance(session, prompt, vllm_config, gen) for _ in range(bs)]
                    results = await asyncio.gather(*tasks)
                    successful_results = [r for r in results if r]

                    if successful_results:
                        avg_e2e = np.mean([r['total_time'] for r in successful_results])
                        avg_ttft = np.mean([r['ttft'] for r in successful_results])
                        avg_gen_tokens = np.mean([r['output_tokens'] for r in successful_results])

                        if avg_gen_tokens > 1:
                            generation_time = avg_e2e - avg_ttft
                            collected_data.append({
                                "bs": bs, "pl": pl, "gen": int(round(avg_gen_tokens)), "gen_time": generation_time
                            })
                            print(f"  -> 第 {i+1}/{repeats_per_config} 次重复: 成功, Gen Time: {generation_time*1000:.2f} ms, Gen tokens: {avg_gen_tokens}")
                    else:
                        print(f"  -> 第 {i+1}/{repeats_per_config} 次重复: 失败")
                    await asyncio.sleep(0.5)

        if collected_data:
            self._initialize_model_from_data(collected_data)
        else:
            print("[ERROR] 未能收集到任何有效的TPOT测试数据。")

    def predict_tpot(self, batchsize: int, context_length: int) -> float:
        """预测单个步骤的TPOT。"""
        if not self._is_fitted: return 0.0
        d1, d2, d3, d4 = self.coeffs['d1'], self.coeffs['d2'], self.coeffs['d3'], self.coeffs['d4']
        return d1 * batchsize * context_length + d2 * batchsize + d3 * context_length + d4

    def predict_generation_time(self, batchsize: int, prompt_length: int, generation_length: int) -> float:
        """通过对每一步的TPOT求和来预测总生成时间。"""
        if not self._is_fitted: return 0.0
        
        # This uses the feature transformation, which is equivalent to summing
        features = self._transform_full_generation_to_features(batchsize, prompt_length, generation_length)[0]
        coeffs = np.array([self.coeffs['d1'], self.coeffs['d2'], self.coeffs['d3'], self.coeffs['d4']])
        
        return np.dot(features, coeffs)

    def update(self, batchsize: int, prompt_length: int, generated_tokens: int, generation_time: float):
        """根据一次完整的生成结果，在线更新模型。"""
        if not self._is_fitted: raise RuntimeError("模型尚未初始化。")
        if generated_tokens <= 0: return

        X = self._transform_full_generation_to_features(batchsize, prompt_length, generated_tokens)
        y = np.array([generation_time])
        
        X_scaled = self.scaler.partial_fit(X).transform(X)
        self.online_model.partial_fit(X_scaled, y)

        # After updating the SGD model, we need to convert its scaled coefficients
        # back to our d1, d2, d3, d4 for the direct prediction methods to stay up to date.
        self._update_coeffs_from_online_model()

    def _update_coeffs_from_online_model(self):
        """Internal function to sync self.coeffs with the state of the online_model."""
        if not self._is_fitted: return
        w_scaled = self.online_model.coef_
        mean, scale = self.scaler.mean_, self.scaler.scale_
        scale[scale == 0] = 1
        
        # The inverse transform: w = w_scaled / scale (since no intercept in SGD)
        d1, d2, d3, d4 = w_scaled / scale
        self.coeffs = {'d1': d1, 'd2': d2, 'd3': d3, 'd4': d4}
        
    def get_coefficients(self) -> dict:
        """获取当前模型的系数 d1, d2, d3, d4。"""
        return self.coeffs

# --- 演示 ---
if __name__ == "__main__":
    # (The demo part would need to be adapted to the new data format and methods)
    async def demo():
        tpot_regressor = OnlineTPOTRegressor()
        
        print("--- 为了快速演示，使用假数据初始化模型 ---")
        fake_data = [
            {"bs": 1, "pl": 128, "gen": 100, "gen_time": 5.0}, # Total time is 5.0s
            {"bs": 4, "pl": 128, "gen": 50, "gen_time": 3.0},
            {"bs": 8, "pl": 64, "gen": 30, "gen_time": 2.25},
        ]
        tpot_regressor._initialize_model_from_data(fake_data)
        
        if tpot_regressor._is_fitted:
            print("\n--- 进行预测 ---")
            bs, pl, gen = 2, 256, 100
            
            # Predict total time
            total_time = tpot_regressor.predict_generation_time(bs, pl, gen)
            print(f"预测 (bs={bs}, pl={pl}, gen={gen}) 的总生成时间: {total_time*1000:.2f} ms")
            
            # Predict TPOT at a specific step
            step_tpot = tpot_regressor.predict_tpot(bs, context_length=pl+50)
            print(f"预测在第 51 步的 TPOT: {step_tpot*1000:.2f} ms")

            # Update the model
            tpot_regressor.update(batchsize=2, prompt_length=256, generated_tokens=100, generation_time=11.0)
            print("\n模型已更新。")
            print("更新后的系数:", tpot_regressor.get_coefficients())
            
    asyncio.run(demo())