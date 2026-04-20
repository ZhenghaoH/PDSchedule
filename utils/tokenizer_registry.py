"""
    Unified tokenizer registry for Qwen / LLaMA / DeepSeek / GPT.

    Usage:
        from tokenizer_registry import estimate_tokens
        n = estimate_tokens("你好，世界", tokenizer_type="qwen1.5b")

    Workflow
        名字中包含 "qwen" → 走 Qwen
        包含 "llama" → 走 LLaMA
        包含 "deepseek" → 走 DeepSeek
        以 "gpt" 开头或含 "gpt-3"/"gpt-4" → 走 GPT（tiktoken）

        没有 transformers：直接跳过 HF tokenizer
        没有 tiktoken：直接跳过 GPT 专用逻辑
        全都失败：len(text) 兜底，不会报错
"""

from functools import lru_cache
from typing import Optional
import os
import json

# 这些库可能不存在，所以用 try/except 处理
try:
    from transformers import AutoTokenizer
except ImportError:
    AutoTokenizer = None  # type: ignore

try:
    import tiktoken  # for GPT-family models
except ImportError:
    tiktoken = None  # type: ignore


class TokenizerRegistry:
    """
    统一管理不同模型族的 tokenizer，并提供按模型名自动路由的能力。
    FAST_MODE: 是否启用快分词加速处理，TRUE启用，False执行正常的tokenizer分词。
    """
    FAST_MODE = False

    # ================= [新增] 本地模型路径配置 =================
    # 逻辑：优先在这里查找路径。如果没有找到，再使用 HuggingFace 的 Repo ID。
    # 你可以根据实际情况添加更多的映射关系。
    LOCAL_PATH_MAP = {
        # 对应 framework_local_test.py 中的路径
        "deepseek": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B/",
        "qwen": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B/",

        # 示例：如果有其他的本地模型
        # "llama": "/path/to/your/local/llama-3-8b/",
    }
    # ========================================================

    # key 是“模型族”，value 是默认的 HF 模型名 (作为本地路径不存在时的兜底)
    HF_FAMILY_DEFAULTS = {
        "qwen": "Qwen/Qwen2-1.5B",
        "llama": "meta-llama/Meta-Llama-3-70B",
        "deepseek": "deepseek-ai/deepseek-moe-16b",
    }

    @classmethod
    def _is_local_model_dir(cls, name: str) -> bool:
        """
        判断 name 是否是本地模型目录（而不是 Hugging Face repo id）。
        经验上：本地目录应当存在，且包含 tokenizer 文件之一。
        """
        if not name:
            return False
        if not os.path.isdir(name):
            return False

        # 常见 tokenizer 文件：tokenizer.json / tokenizer.model / vocab.json / merges.txt / special_tokens_map.json 等
        markers = [
            "tokenizer.json",
            "tokenizer.model",
            "vocab.json",
            "merges.txt",
            "special_tokens_map.json",
            "tokenizer_config.json",
        ]
        return any(os.path.exists(os.path.join(name, m)) for m in markers)

    @classmethod
    def detect_family(cls, tokenizer_type: str) -> str:
        """
        根据模型名/类型推断属于哪个族：qwen / llama / deepseek / gpt / other
        """
        name = tokenizer_type.lower()

        if "qwen" in name:
            return "qwen"
        if "llama" in name or "lLaMA".lower() in name:
            return "llama"
        if "deepseek" in name:
            return "deepseek"
        if name.startswith("gpt") or "gpt-3" in name or "gpt-4" in name:
            return "gpt"

        # 兜底归为 other
        return "other"

    @classmethod
    def _resolve_tokenizer_source(cls, model_name: str) -> str:
        """
        把 served-model-name / repo id / 本地路径 统一解析成 tokenizer 的加载源：
        - 如果是本地目录：返回本地目录
        - 如果在映射表里：返回映射到的本地目录
        - 否则原样返回（可能是 repo id）
        """
        # 1) 本地目录直接返回
        if cls._is_local_model_dir(model_name):
            return model_name

        # 2) served-name 映射
        raw = os.getenv("SCHEDULER_TOKENIZER_MAP", "").strip()
        if raw:
            try:
                mp = json.loads(raw)
                if isinstance(mp, dict) and model_name in mp:
                    return mp[model_name]
            except Exception:
                pass

        # 3) fallback：原样
        return model_name

    # ---------------- GPT 系列（tiktoken） ----------------

    @classmethod
    @lru_cache(maxsize=None)
    def _get_gpt_encoding(cls, model_name: str):
        if tiktoken is None:
            return None

        try:
            return tiktoken.encoding_for_model(model_name)
        except Exception:
            # 如果具体模型不识别，就用通用的 cl100k_base
            try:
                return tiktoken.get_encoding("cl100k_base")
            except Exception:
                return None

    @classmethod
    def _gpt_count_tokens(cls, text: str, model_name: str) -> Optional[int]:
        enc = cls._get_gpt_encoding(model_name)
        if enc is None:
            return None
        # tiktoken 返回的是 list[int]
        return len(enc.encode(text))

    # ---------------- HF 系列（Qwen / LLaMA / DeepSeek） ----------------

    @classmethod
    @lru_cache(maxsize=None)
    def _get_hf_tokenizer(cls, model_path_or_name: str):
        """
        加载 Tokenizer，支持本地路径或 HF Repo ID。
        """
        if AutoTokenizer is None:
            print("[TokenizerRegistry] transformers.AutoTokenizer not available.")
            return None

        try:
            print(f"[TokenizerRegistry] Loading tokenizer from: {model_path_or_name}")
            is_local = cls._is_local_model_dir(model_path_or_name)
            # trust_remote_code=True 方便适配 Qwen/DeepSeek 这类自定义 tokenizer
            # use_fast=True 通常能加速，且对于本地加载也是支持的
            return AutoTokenizer.from_pretrained(
                model_path_or_name,
                trust_remote_code=True,
                use_fast=True,
                local_files_only=is_local
            )
        except Exception as e:
            print(f"[TokenizerRegistry] Load failed: {e}")
            return None

    @classmethod
    def _hf_count_tokens(cls, text: str, family: str, model_name: str) -> Optional[int]:
        """
        对 Qwen/LLaMA/DeepSeek 这类 HF 模型计数。
        优先逻辑：本地配置路径 -> 传入的绝对路径 -> HF Hub ID
        """
        model_name = cls._resolve_tokenizer_source(model_name)

        # 1) 本地目录优先
        if cls._is_local_model_dir(model_name):
            hf_name = model_name
        else:
            # 2) 如果像 repo id（包含一个 / 但不是本地目录），直接用它
            if "/" in model_name:
                hf_name = model_name
            else:
                # 3) 否则用 family 默认
                hf_name = cls.HF_FAMILY_DEFAULTS.get(family)

        if hf_name is None:
            return None

        tok = cls._get_hf_tokenizer(hf_name)
        if tok is None:
            return None

        try:
            # 对大部分 HF tokenizer，encode 返回 token id list
            # add_special_tokens=False 避免再加 BOS/EOS 之类的
            return len(tok.encode(text, add_special_tokens=False))
        except Exception as e:
            print(f"[TokenizerRegistry] Encode failed: {e}")
            return None

    # ---------------- 对外统一接口 ----------------

    @classmethod
    def estimate_tokens(cls, text: str, model_name: str) -> int:
        """
        按模型名自动选择 tokenizer，返回 token 数。
        如果环境里缺少 transformers/tiktoken 或解析失败，则退化为 len(text)。

        :param text: 输入文本
        :param model_name: 模型名或模型类型，如
                           'qwen1.5b' / 'Qwen/Qwen2-1.5B'
                           'llama3-8b'
                           'deepseek-v2'
                           'gpt-4o-mini'
        """
        family = cls.detect_family(model_name)

        # --- 快速近似模式：完全跳过慢 tokenizer ---
        if cls.FAST_MODE:
            # print("use Fast tokenizer")
            # 可以按族给一个缩放系数，便于之后细调
            rough_ratio = {
                "deepseek": 1.0,
                "qwen": 1.0,
                "llama": 0.8,
                "gpt": 0.75,
            }.get(family, 1.0)
            return int(len(text) * rough_ratio)

        # 1) GPT 系列 → tiktoken
        if family == "gpt":
            # print(f"[TokenizerRegistry] user tokenizer {model_name} ")
            n = cls._gpt_count_tokens(text, model_name)
            if n is not None:
                return n

        # 2) Qwen / LLaMA / DeepSeek → HF tokenizer (Local or Remote)
        if family in {"qwen", "llama", "deepseek", "other"}:
            # print(f"[TokenizerRegistry] using tokenizer for {model_name} ({family})")
            n = cls._hf_count_tokens(text, family, model_name)
            if n is not None:
                return n

        # 3) 其他 / 都失败 → 退化为按字符数估算
        print(f"[TokenizerRegistry] Warning: No tokenizer found for {model_name} (family: {family}). Fallback to char count.")
        return len(text)

    @classmethod
    def warmup_tokenizers(cls, model_name: str):
        """
        预加载某个模型对应的 tokenizer，使后续 estimate_tokens 不再触发冷启动。
        本地目录：直接 warmup 本地 tokenizer，并强制 local_files_only。
        """
        model_name = cls._resolve_tokenizer_source(model_name)

        # 本地目录优先 warmup
        if cls._is_local_model_dir(model_name):
            cls._get_hf_tokenizer(model_name)
            return

        family = cls.detect_family(model_name)

        if family == "gpt":
            cls._get_gpt_encoding(model_name)
            return

        if family in {"qwen", "llama", "deepseek"}:
            if "/" in model_name:
                hf_name = model_name
            else:
                hf_name = cls.HF_FAMILY_DEFAULTS.get(family)

            if hf_name is not None:
                cls._get_hf_tokenizer(hf_name)


# 为了用起来更方便，暴露一个顶层函数
def estimate_tokens(text: str, model_name: str) -> int:
    """
    统一入口：估计某模型下文本的 token 数。
    """
    return TokenizerRegistry.estimate_tokens(text, model_name)