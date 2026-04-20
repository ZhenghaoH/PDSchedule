from dataclasses import dataclass


@dataclass
class ModelConfig:
    model_layers: int = 32
    model_dimension: int = 4096
    model_precision_bytes: int = 2
