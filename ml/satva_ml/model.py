"""The SATVA screening model: one MobileNetV3-Small backbone, two heads.

    MobileNetV3-Small backbone (576-d features)
    +-- adulteration anomaly head  -> logit  -> 0-100 anomaly score
    +-- ripeness regression head   -> scalar -> 0-1 ripeness index

Why one backbone (spec 3.5 and 9.4)
-----------------------------------
The same photograph serves two economies: consumer safety screening and
retailer shelf-life prediction. Sharing the backbone means one ~4 MB INT8 model
on the handset instead of two, one forward pass instead of two, and features
learned from both tasks reinforcing each other.

What the anomaly head is and is not
-----------------------------------
It does **not** detect calcium carbide. It cannot: a camera measures reflected
light, and carbide residue has no reliable visual signature of its own. The head
is trained on the *morphological consequences* the specification lists --
unusually uniform skin colour while the stem and calyx remain green, speckled
surface burns from direct carbide contact, unnatural sheen, and texture lagging
behind apparent colour.

That is why its output only ever decides whether a five-rupee strip test is
worth performing. Everything downstream of that decision runs on chemistry.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

# The penultimate feature width of MobileNetV3-Small. Also the embedding
# dimension stored in analytics.scans.embedding, so the two must not drift.
FEATURE_DIM = 576
INPUT_SIZE = 224


@dataclass
class ModelConfig:
    pretrained: bool = True
    dropout: float = 0.2
    freeze_backbone_layers: int = 0
    head_hidden: int = 128


class SatvaScreeningModel(nn.Module):
    """Shared-backbone, dual-head screening model."""

    def __init__(self, config: ModelConfig | None = None):
        super().__init__()
        self.config = config or ModelConfig()

        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if self.config.pretrained else None
        backbone = mobilenet_v3_small(weights=weights)

        # Keep the convolutional trunk and the pooling; drop the classifier.
        self.features = backbone.features
        self.avgpool = backbone.avgpool

        if self.config.freeze_backbone_layers > 0:
            for index, block in enumerate(self.features):
                if index < self.config.freeze_backbone_layers:
                    for parameter in block.parameters():
                        parameter.requires_grad = False

        # Two small heads rather than one multi-output layer, so that a task can
        # be re-trained or re-calibrated without disturbing the other.
        self.anomaly_head = nn.Sequential(
            nn.Linear(FEATURE_DIM, self.config.head_hidden),
            nn.Hardswish(inplace=True),
            nn.Dropout(self.config.dropout),
            nn.Linear(self.config.head_hidden, 1),
        )
        self.ripeness_head = nn.Sequential(
            nn.Linear(FEATURE_DIM, self.config.head_hidden),
            nn.Hardswish(inplace=True),
            nn.Dropout(self.config.dropout),
            nn.Linear(self.config.head_hidden, 1),
        )

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.avgpool(x)
        return torch.flatten(x, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Returns (anomaly_logit, ripeness, features).

        Features come out alongside the predictions because the handset stores a
        normalised embedding for near-duplicate detection (spec 3.3). Computing
        it in the same pass avoids a second inference on the device.
        """
        features = self.forward_features(x)
        anomaly_logit = self.anomaly_head(features).squeeze(-1)
        # Ripeness is a proportion, so it is squashed to [0, 1] in the model
        # rather than clamped afterwards -- clamping would give zero gradient
        # outside the range and stall training on the extremes.
        ripeness = torch.sigmoid(self.ripeness_head(features)).squeeze(-1)
        return anomaly_logit, ripeness, features


class ExportWrapper(nn.Module):
    """Inference-time wrapper with fixed, ONNX-friendly outputs.

    The training model returns a raw logit; the deployed model returns a
    calibrated 0-100 anomaly score, a ripeness index and an L2-normalised
    embedding. Doing the conversion inside the graph means the Dart and Python
    clients cannot disagree about it.
    """

    def __init__(self, model: SatvaScreeningModel, temperature: float = 1.0, bias: float = 0.0):
        super().__init__()
        self.model = model
        # Temperature scaling from calibration; see calibrate.py.
        self.register_buffer("temperature", torch.tensor(float(temperature)))
        self.register_buffer("bias", torch.tensor(float(bias)))

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logit, ripeness, features = self.model(x)
        probability = torch.sigmoid((logit + self.bias) / self.temperature)
        anomaly_score = probability * 100.0
        embedding = features / (features.norm(dim=1, keepdim=True) + 1e-8)
        return anomaly_score, ripeness, embedding


def count_parameters(model: nn.Module) -> tuple[int, int]:
    total = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return total, trainable


def estimate_size_mb(model: nn.Module, *, bytes_per_param: int = 1) -> float:
    """Approximate deployed size. INT8 is one byte per parameter."""
    total, _ = count_parameters(model)
    return total * bytes_per_param / (1024 * 1024)


def build_model(config: ModelConfig | None = None) -> SatvaScreeningModel:
    return SatvaScreeningModel(config)


if __name__ == "__main__":
    model = build_model(ModelConfig(pretrained=False))
    total, trainable = count_parameters(model)
    dummy = torch.randn(2, 3, INPUT_SIZE, INPUT_SIZE)
    anomaly, ripeness, features = model(dummy)
    print(f"parameters      : {total:,} ({trainable:,} trainable)")
    print(f"FP32 size       : {estimate_size_mb(model, bytes_per_param=4):.2f} MB")
    print(f"INT8 size (est) : {estimate_size_mb(model, bytes_per_param=1):.2f} MB")
    print(f"anomaly logit   : {tuple(anomaly.shape)}")
    print(f"ripeness        : {tuple(ripeness.shape)}")
    print(f"features        : {tuple(features.shape)}")
