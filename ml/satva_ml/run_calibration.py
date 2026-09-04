"""Fit and save probability calibration for a trained checkpoint.

    python -m satva_ml.run_calibration --checkpoint artifacts/satva_screen_best.pth

Calibration is fitted on the held-out split, which for a real deployment is the
controlled college-lab set (spec 6.3): the only data whose labels come from a
known ripening treatment rather than an inferred one.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from satva_ml.calibrate import expected_calibration_error, fit_per_crop, save
from satva_ml.dataset import ProduceDataset, read_manifest, stratified_split
from satva_ml.export import load_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the SATVA anomaly head.")
    parser.add_argument("--checkpoint", default="artifacts/satva_screen_best.pth", type=Path)
    parser.add_argument("--manifest", default="data/manifest.csv", type=Path)
    parser.add_argument("--out", default="artifacts/calibration.json", type=Path)
    args = parser.parse_args()

    model, _ = load_checkpoint(args.checkpoint)
    samples = read_manifest(args.manifest)
    _, val_samples, test_samples = stratified_split(samples)

    # Calibrate on the held-out set, never on training data: fitting a
    # temperature on data the model has memorised produces a temperature near 1
    # and no actual correction.
    calibration_samples = test_samples or val_samples
    loader = DataLoader(ProduceDataset(calibration_samples, train=False), batch_size=32)

    logits: list[float] = []
    labels: list[float] = []
    model.eval()
    with torch.no_grad():
        for images, label, _ in loader:
            logit, _, _ = model(images)
            logits.extend(logit.numpy().ravel().tolist())
            labels.extend(label.numpy().ravel().tolist())

    crops = [s.crop for s in calibration_samples]
    result = fit_per_crop(np.asarray(logits), np.asarray(labels), crops)

    print(f"calibration set: {result.n_samples} samples")
    print(f"  temperature : {result.temperature:.4f}   bias: {result.bias:.4f}")
    print(f"  ECE  before : {result.ece_before:.4f}  ->  after: {result.ece_after:.4f}")
    print(f"  NLL  before : {result.nll_before:.4f}  ->  after: {result.nll_after:.4f}")
    for crop, entry in sorted(result.per_crop.items()):
        flag = "" if entry["sufficient_data"] else "  (global fallback)"
        print(f"    {crop:10s} T={entry['temperature']:.4f}  n={entry['n_samples']}{flag}")

    save(result, args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
