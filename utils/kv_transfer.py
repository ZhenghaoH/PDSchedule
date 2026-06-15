"""KV Cache transfer size helpers for Qwen2.5-7B."""

QWEN25_7B_NUM_HIDDEN_LAYERS = 28
QWEN25_7B_NUM_KEY_VALUE_HEADS = 4
QWEN25_7B_HEAD_DIM = 128
QWEN25_7B_DTYPE_BYTES = 2

QWEN25_7B_KV_BYTES_PER_TOKEN = (
    QWEN25_7B_NUM_HIDDEN_LAYERS
    * 2
    * QWEN25_7B_NUM_KEY_VALUE_HEADS
    * QWEN25_7B_HEAD_DIM
    * QWEN25_7B_DTYPE_BYTES
)


def estimate_qwen25_7b_kv_transfer_bytes(
    token_len: int | float,
    compress_factor: int | float,
) -> float:
    """Estimate transferred KV bytes after compression."""
    safe_token_len = max(0.0, float(token_len))
    safe_compress_factor = max(0.0, float(compress_factor))
    return safe_token_len * QWEN25_7B_KV_BYTES_PER_TOKEN * safe_compress_factor


def estimate_transfer_time_ms(
    token_len: int | float,
    compress_factor: int | float,
    bandwidth_mbps: int | float,
) -> float:
    """Estimate transfer time in ms when bandwidth is measured in Mbps."""
    safe_bandwidth_mbps = max(0.0, float(bandwidth_mbps))
    if safe_bandwidth_mbps <= 0:
        return 0.0

    transfer_bytes = estimate_qwen25_7b_kv_transfer_bytes(token_len, compress_factor)
    return (transfer_bytes * 8.0) / (safe_bandwidth_mbps * 1000.0)
