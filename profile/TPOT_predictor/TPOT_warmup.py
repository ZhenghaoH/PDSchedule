if __package__:
    from .TPOT_predictor import (
        TPOT_WARM_UP_CONFIGS_DEFAULT,
        VLLM_CONFIG_DEFAULT,
        perform_detailed_warmup,
    )
else:
    from TPOT_predictor import (
        TPOT_WARM_UP_CONFIGS_DEFAULT,
        VLLM_CONFIG_DEFAULT,
        perform_detailed_warmup,
    )


async def initialize_and_warm_up_tpot_regressor(
    warm_up_configs=None,
    vllm_config=None,
    learning_rate: float = 1e-6,
    repeats_per_config: int = 2,
):
    """Compatibility wrapper for older TPOT warmup callers."""
    del learning_rate
    if warm_up_configs is not None:
        if __package__:
            from .TPOT_predictor import get_regressor
        else:
            from TPOT_predictor import get_regressor

        regressor = await get_regressor()
        regressor.clear_data()
        await regressor.warm_up_from_tests(
            test_configs=warm_up_configs,
            vllm_config=vllm_config or VLLM_CONFIG_DEFAULT,
            repeats_per_config=repeats_per_config,
        )
        return regressor

    await perform_detailed_warmup(
        vllm_config=vllm_config or VLLM_CONFIG_DEFAULT,
        repeats=repeats_per_config,
    )
    if __package__:
        from .TPOT_predictor import get_regressor
    else:
        from TPOT_predictor import get_regressor

    return await get_regressor()


if __name__ == "__main__":
    import asyncio

    asyncio.run(perform_detailed_warmup())

