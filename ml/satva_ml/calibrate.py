"""Probability calibration for the anomaly head.

Why calibration is not optional here
------------------------------------
The specification asks for a **calibrated 0-100 anomaly score**, and the word
matters. A raw sigmoid output from a neural network is not a probability: modern
networks are systematically over-confident, so a raw 0.9 does not mean "nine
times out of ten". Publishing that as "90 out of 100" to a shopper deciding
whether to buy fruit would be misleading in a way that undermines the whole
triage premise.

Temperature scaling (Guo et al., 2017) fits a single scalar T on a held-out set
and divides the logit by it. It cannot change which samples rank above which --
so accuracy, ROC-AUC and the chosen operating point are all unaffected -- but it
makes the resulting number mean what it appears to mean.

The controlled college-lab set is the right calibration set, because its labels
come from a known treatment rather than an inferred one (spec 6.3).

Per-crop calibration
--------------------
Spec 6.4 requires the model to be calibrated per crop. `fit_per_crop` returns a
temperature per crop, and the export writes them into the model card. A crop
with too few calibration samples gets the global temperature and is flagged, so
a thin calibration is visible rather than silently assumed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

MIN_CALIBRATION_SAMPLES = 40


@dataclass
class CalibrationResult:
    temperature: float
    bias: float
    n_samples: int
    ece_before: float
    ece_after: float
    nll_before: float
    nll_after: float
    per_crop: dict[str, dict] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "temperature": round(self.temperature, 6),
            "bias": round(self.bias, 6),
            "n_samples": self.n_samples,
            "ece_before": round(self.ece_before, 5),
            "ece_after": round(self.ece_after, 5),
            "nll_before": round(self.nll_before, 5),
            "nll_after": round(self.nll_after, 5),
            "per_crop": self.per_crop,
            "note": self.note,
        }


def expected_calibration_error(
    probabilities: np.ndarray, labels: np.ndarray, n_bins: int = 12
) -> float:
    """ECE: mean gap between confidence and accuracy, weighted by bin size.

    A perfectly calibrated model scores 0. This is the number that says whether
    "70 out of 100" actually corresponds to a 70% rate.
    """
    probabilities = np.asarray(probabilities, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    error = 0.0

    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        mask = (probabilities > lower) & (probabilities <= upper)
        if not mask.any():
            continue
        bin_confidence = probabilities[mask].mean()
        bin_accuracy = labels[mask].mean()
        error += (mask.mean()) * abs(bin_confidence - bin_accuracy)
    return float(error)


def _nll(logits: np.ndarray, labels: np.ndarray, temperature: float, bias: float) -> float:
    scaled = torch.tensor((logits + bias) / temperature, dtype=torch.float64)
    target = torch.tensor(labels, dtype=torch.float64)
    return float(nn.functional.binary_cross_entropy_with_logits(scaled, target).item())


def fit_temperature(
    logits: np.ndarray,
    labels: np.ndarray,
    *,
    fit_bias: bool = True,
    max_iter: int = 300,
) -> CalibrationResult:
    """Fit temperature (and optionally a bias) by minimising NLL.

    L-BFGS on two scalars over a few hundred samples is instantaneous, and the
    objective is well behaved, so there is no reason to approximate.
    """
    logits = np.asarray(logits, dtype=np.float64).ravel()
    labels = np.asarray(labels, dtype=np.float64).ravel()

    probabilities_before = 1.0 / (1.0 + np.exp(-logits))
    ece_before = expected_calibration_error(probabilities_before, labels)
    nll_before = _nll(logits, labels, 1.0, 0.0)

    if len(np.unique(labels)) < 2:
        return CalibrationResult(
            temperature=1.0,
            bias=0.0,
            n_samples=len(labels),
            ece_before=ece_before,
            ece_after=ece_before,
            nll_before=nll_before,
            nll_after=nll_before,
            note="calibration skipped: the calibration set contains only one class",
        )

    # Perfect separation makes temperature scaling ill-posed. If no positive
    # logit overlaps any negative one, the negative log-likelihood is minimised
    # as T approaches zero -- the optimiser runs off to an infinitely sharp
    # model and returns NaN. Detecting it and declining is the correct
    # behaviour: a separable calibration set carries no information about how
    # over-confident the model is on hard cases, because it contains none.
    positive_logits = logits[labels > 0.5]
    negative_logits = logits[labels <= 0.5]
    separable = (
        len(positive_logits) > 0
        and len(negative_logits) > 0
        and positive_logits.min() > negative_logits.max()
    )
    if separable:
        return CalibrationResult(
            temperature=1.0,
            bias=0.0,
            n_samples=len(labels),
            ece_before=ece_before,
            ece_after=ece_before,
            nll_before=nll_before,
            nll_after=nll_before,
            note=(
                "Calibration NOT fitted: the calibration set is perfectly separable, so "
                "temperature scaling is ill-posed (the likelihood is minimised as T -> 0). "
                "This is itself a warning sign that the calibration data is too easy and "
                "does not represent field conditions. Temperature left at 1.0."
            ),
        )

    logit_tensor = torch.tensor(logits, dtype=torch.float64)
    label_tensor = torch.tensor(labels, dtype=torch.float64)

    # Optimise log(T) so temperature stays strictly positive without a
    # constraint; a negative temperature would invert the ranking. The bounds
    # keep a near-separable set from driving the optimiser to a degenerate
    # solution.
    min_log_t, max_log_t = float(np.log(0.05)), float(np.log(20.0))
    log_temperature = torch.zeros(1, dtype=torch.float64, requires_grad=True)
    bias_parameter = torch.zeros(1, dtype=torch.float64, requires_grad=fit_bias)
    parameters = [log_temperature] + ([bias_parameter] if fit_bias else [])

    optimizer = torch.optim.LBFGS(parameters, lr=0.1, max_iter=max_iter)

    def closure():
        optimizer.zero_grad()
        clamped = torch.clamp(log_temperature, min_log_t, max_log_t)
        scaled = (logit_tensor + bias_parameter) / torch.exp(clamped)
        loss = nn.functional.binary_cross_entropy_with_logits(scaled, label_tensor)
        loss.backward()
        return loss

    optimizer.step(closure)

    temperature = float(torch.exp(torch.clamp(log_temperature, min_log_t, max_log_t)).item())
    bias = float(bias_parameter.item()) if fit_bias else 0.0

    # Final guard: a non-finite result must never reach the exported model,
    # where it would turn every score into NaN.
    if not (np.isfinite(temperature) and np.isfinite(bias) and temperature > 0):
        return CalibrationResult(
            temperature=1.0,
            bias=0.0,
            n_samples=len(labels),
            ece_before=ece_before,
            ece_after=ece_before,
            nll_before=nll_before,
            nll_after=nll_before,
            note=(
                "Calibration did not converge to a finite temperature; falling back to 1.0 "
                "rather than exporting a model that would produce NaN scores."
            ),
        )

    scaled = (logits + bias) / temperature
    probabilities_after = 1.0 / (1.0 + np.exp(-scaled))
    ece_after = expected_calibration_error(probabilities_after, labels)
    nll_after = _nll(logits, labels, temperature, bias)

    return CalibrationResult(
        temperature=temperature,
        bias=bias,
        n_samples=len(labels),
        ece_before=ece_before,
        ece_after=ece_after,
        nll_before=nll_before,
        nll_after=nll_after,
        note=(
            "Temperature scaling preserves ranking, so the operating point and ROC-AUC are "
            "unchanged; only the meaning of the reported number improves."
        ),
    )


def fit_per_crop(
    logits: np.ndarray, labels: np.ndarray, crops: list[str]
) -> CalibrationResult:
    """Global calibration plus a per-crop temperature where data allows.

    Spec 6.4 requires per-crop calibration. A crop with fewer than
    `MIN_CALIBRATION_SAMPLES` calibration points falls back to the global
    temperature and is marked `sufficient_data: false`, so a thin calibration
    shows up in the model card instead of being quietly assumed adequate.
    """
    result = fit_temperature(logits, labels)
    crops_array = np.asarray(crops)

    for crop in sorted(set(crops)):
        mask = crops_array == crop
        n = int(mask.sum())
        if n < MIN_CALIBRATION_SAMPLES or len(np.unique(labels[mask])) < 2:
            result.per_crop[crop] = {
                "temperature": result.temperature,
                "bias": result.bias,
                "n_samples": n,
                "sufficient_data": False,
                "note": (
                    f"fewer than {MIN_CALIBRATION_SAMPLES} calibration samples; "
                    "using the global temperature"
                ),
            }
            continue

        crop_result = fit_temperature(logits[mask], labels[mask])
        result.per_crop[crop] = {
            "temperature": round(crop_result.temperature, 6),
            "bias": round(crop_result.bias, 6),
            "n_samples": n,
            "sufficient_data": True,
            "ece_before": round(crop_result.ece_before, 5),
            "ece_after": round(crop_result.ece_after, 5),
        }

    return result


def collect_logits(model, loader, device: str = "cpu") -> tuple[np.ndarray, np.ndarray]:
    """Run the model over a loader and return (logits, labels)."""
    model.eval()
    logits: list[float] = []
    labels: list[float] = []
    with torch.no_grad():
        for images, label, _ in loader:
            logit, _, _ = model(images.to(device))
            logits.extend(logit.cpu().numpy().ravel().tolist())
            labels.extend(label.numpy().ravel().tolist())
    return np.asarray(logits), np.asarray(labels)


def save(result: CalibrationResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
