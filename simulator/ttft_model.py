from __future__ import annotations

import pickle
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union


DEFAULT_PREFILL_TTFT_MODEL_PATH = Path(__file__).resolve().parent / "prefill_ttft_regressor.pkl"


@dataclass(frozen=True)
class LinearTTFTModel:
    slope_ms_per_token: float
    intercept_ms: float

    def predict_ms(self, total_tokens: int) -> float:
        return max(0.0, self.slope_ms_per_token * max(0, int(total_tokens)) + self.intercept_ms)


def load_prefill_ttft_model(
    path: Optional[Union[Path, str]] = DEFAULT_PREFILL_TTFT_MODEL_PATH,
) -> Optional[LinearTTFTModel]:
    if path is None:
        return None

    model_path = Path(path)
    data = _load_pickle_with_optional_sklearn_stubs(model_path)
    slope_sec, intercept_sec = _extract_seconds_formula(data)
    return LinearTTFTModel(
        slope_ms_per_token=slope_sec * 1000.0,
        intercept_ms=intercept_sec * 1000.0,
    )


def _load_pickle_with_optional_sklearn_stubs(path: Path) -> Any:
    try:
        with path.open("rb") as file:
            return pickle.load(file)
    except ModuleNotFoundError as exc:
        if not str(exc).endswith("'sklearn'"):
            raise

    installed = _install_sklearn_stubs()
    try:
        with path.open("rb") as file:
            return pickle.load(file)
    finally:
        for name, old_module in installed.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


def _install_sklearn_stubs() -> dict[str, Optional[types.ModuleType]]:
    class _SklearnStub:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def __setstate__(self, state: dict) -> None:
            self.__dict__.update(state)

    module_names = [
        "sklearn",
        "sklearn.linear_model",
        "sklearn.linear_model._base",
        "sklearn.preprocessing",
        "sklearn.preprocessing._data",
    ]
    previous = {name: sys.modules.get(name) for name in module_names}
    for name in module_names:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["sklearn.linear_model._base"].LinearRegression = _SklearnStub
    sys.modules["sklearn.preprocessing._data"].StandardScaler = _SklearnStub
    return previous


def _extract_seconds_formula(data: Any) -> tuple[float, float]:
    if isinstance(data, dict):
        coeffs = data.get("coeffs")
        if isinstance(coeffs, dict):
            slope = _first_present(coeffs, ("tokens", "a", "coef", "slope"))
            intercept = _first_present(coeffs, ("d", "b", "intercept"))
            if slope is not None and intercept is not None:
                return float(slope), float(intercept)

        slope = _first_present(data, ("a", "coef", "slope"))
        intercept = _first_present(data, ("b", "intercept"))
        if slope is not None and intercept is not None:
            return float(slope), float(intercept)

        model = data.get("model")
        scaler = data.get("scaler")
        if model is not None:
            return _extract_from_model_and_scaler(model, scaler)

    return _extract_from_model_and_scaler(data, None)


def _extract_from_model_and_scaler(model: Any, scaler: Any) -> tuple[float, float]:
    coef = _as_scalar(getattr(model, "coef_", None))
    intercept = _as_scalar(getattr(model, "intercept_", None))
    if coef is None or intercept is None:
        raise ValueError("TTFT model must contain coeffs or model.coef_/model.intercept_")

    if scaler is None:
        return float(coef), float(intercept)

    mean = _as_scalar(getattr(scaler, "mean_", None))
    scale = _as_scalar(getattr(scaler, "scale_", None))
    if mean is None or scale in (None, 0):
        return float(coef), float(intercept)

    raw_slope = float(coef) / float(scale)
    raw_intercept = float(intercept) - raw_slope * float(mean)
    return raw_slope, raw_intercept


def _first_present(data: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _as_scalar(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if hasattr(value, "ravel"):
            return float(value.ravel()[0])
        if isinstance(value, (list, tuple)):
            return float(value[0])
        return float(value)
    except (TypeError, ValueError, IndexError):
        return None
