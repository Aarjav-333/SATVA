"""Training loop for the dual-head SATVA screening model.

    python -m satva_ml.train --manifest data/manifest.csv --epochs 30

Loss
----
    total = w_a * BCEWithLogits(anomaly) + w_r * SmoothL1(ripeness)

Two heads with different scales and different noise levels, so the weighting is
explicit rather than implicit. The anomaly task carries more weight because it
is the safety-critical one; the ripeness task acts partly as an auxiliary signal
that regularises the shared backbone.

`pos_weight` on the anomaly loss counters class imbalance -- see
`dataset.class_weights` for why that matters here specifically.

Selection
---------
Checkpoints are chosen by validation **recall at high precision**, not by
accuracy. Accuracy on an imbalanced set is close to meaningless, and for a
triage layer the cost of a missed adulterated sample (a false negative sends
someone home with bad fruit) is not symmetric with the cost of a false positive
(the user spends five rupees on a strip test that comes back clean).
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from satva_ml.dataset import (
    ProduceDataset,
    class_weights,
    read_manifest,
    stratified_split,
)
from satva_ml.model import ModelConfig, SatvaScreeningModel, build_model, count_parameters


@dataclass
class TrainConfig:
    manifest: str = "data/manifest.csv"
    output_dir: str = "artifacts"
    epochs: int = 30
    batch_size: int = 32
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    anomaly_loss_weight: float = 1.0
    ripeness_loss_weight: float = 0.35
    warmup_epochs: int = 2
    num_workers: int = 0
    seed: int = 20260904
    device: str = "cpu"
    pretrained: bool = True
    label_smoothing: float = 0.03


def set_seed(seed: int) -> None:
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    # Determinism matters here: a reviewer must be able to reproduce a reported
    # metric exactly, and "it varies between runs" is not an acceptable answer
    # to a question about a safety-screening model.
    torch.use_deterministic_algorithms(True, warn_only=True)


def evaluate(model: nn.Module, loader: DataLoader, device: str) -> dict:
    """Threshold-independent and threshold-dependent validation metrics."""
    model.eval()
    scores: list[float] = []
    labels: list[float] = []
    ripeness_error: list[float] = []

    with torch.no_grad():
        for images, label, ripeness in loader:
            images = images.to(device)
            logit, predicted_ripeness, _ = model(images)
            scores.extend(torch.sigmoid(logit).cpu().numpy().tolist())
            labels.extend(label.numpy().tolist())
            ripeness_error.extend(
                torch.abs(predicted_ripeness.cpu() - ripeness).numpy().tolist()
            )

    scores_array = np.asarray(scores)
    labels_array = np.asarray(labels)

    metrics = {
        "n": int(len(labels_array)),
        "positives": int(labels_array.sum()),
        "ripeness_mae": float(np.mean(ripeness_error)) if ripeness_error else None,
    }

    if 0 < labels_array.sum() < len(labels_array):
        from sklearn.metrics import (
            average_precision_score,
            precision_recall_curve,
            roc_auc_score,
        )

        metrics["roc_auc"] = float(roc_auc_score(labels_array, scores_array))
        metrics["average_precision"] = float(average_precision_score(labels_array, scores_array))

        precision, recall, thresholds = precision_recall_curve(labels_array, scores_array)
        # Highest recall achievable while keeping precision at or above 0.90.
        # This is the operating point a triage layer is judged on.
        usable = precision[:-1] >= 0.90
        if usable.any():
            best = int(np.argmax(np.where(usable, recall[:-1], -1)))
            metrics["recall_at_p90"] = float(recall[best])
            metrics["threshold_at_p90"] = float(thresholds[best])
        else:
            metrics["recall_at_p90"] = 0.0
            metrics["threshold_at_p90"] = 0.5
    else:
        metrics["roc_auc"] = None
        metrics["average_precision"] = None
        metrics["recall_at_p90"] = 0.0
        metrics["threshold_at_p90"] = 0.5

    return metrics


def train(config: TrainConfig) -> dict:
    set_seed(config.seed)
    device = torch.device(config.device)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = read_manifest(Path(config.manifest))
    if not samples:
        raise SystemExit(f"No samples found in {config.manifest}")

    train_samples, val_samples, test_samples = stratified_split(samples)
    print(
        f"dataset: {len(samples)} samples -> "
        f"train {len(train_samples)} / val {len(val_samples)} / test {len(test_samples)}"
    )

    train_loader = DataLoader(
        ProduceDataset(train_samples, train=True),
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=config.num_workers,
        drop_last=len(train_samples) > config.batch_size,
    )
    val_loader = DataLoader(
        ProduceDataset(val_samples or test_samples, train=False),
        batch_size=config.batch_size,
        num_workers=config.num_workers,
    )

    model = build_model(ModelConfig(pretrained=config.pretrained)).to(device)
    total, trainable = count_parameters(model)
    print(f"model: {total:,} parameters ({trainable:,} trainable)")

    pos_weight = class_weights(train_samples).to(device)
    anomaly_loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    ripeness_loss_fn = nn.SmoothL1Loss(beta=0.1)

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer,
        max_lr=config.learning_rate,
        epochs=config.epochs,
        steps_per_epoch=max(1, len(train_loader)),
        pct_start=min(0.3, config.warmup_epochs / max(config.epochs, 1)),
    )

    history: list[dict] = []
    best_metric = -1.0
    best_path = output_dir / "satva_screen_best.pth"
    started = time.perf_counter()

    for epoch in range(1, config.epochs + 1):
        model.train()
        epoch_loss = 0.0
        batches = 0

        for images, label, ripeness in train_loader:
            images = images.to(device)
            label = label.to(device)
            ripeness = ripeness.to(device)

            # Light label smoothing: field labels come from a strip test read in
            # market conditions, so treating them as absolutely certain
            # over-states what is known and encourages over-confidence.
            smoothed = label * (1 - config.label_smoothing) + 0.5 * config.label_smoothing

            optimizer.zero_grad(set_to_none=True)
            logit, predicted_ripeness, _ = model(images)
            loss = (
                config.anomaly_loss_weight * anomaly_loss_fn(logit, smoothed)
                + config.ripeness_loss_weight * ripeness_loss_fn(predicted_ripeness, ripeness)
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            scheduler.step()

            epoch_loss += float(loss.item())
            batches += 1

        metrics = evaluate(model, val_loader, config.device)
        metrics.update({"epoch": epoch, "train_loss": epoch_loss / max(batches, 1)})
        history.append(metrics)

        selection = metrics.get("recall_at_p90") or 0.0
        marker = ""
        if selection > best_metric:
            best_metric = selection
            torch.save(
                {
                    "model_state": model.state_dict(),
                    "config": asdict(config),
                    "metrics": metrics,
                },
                best_path,
            )
            marker = "  <- best"

        print(
            f"  epoch {epoch:3d}/{config.epochs}  loss={metrics['train_loss']:.4f}  "
            f"auc={metrics.get('roc_auc') or float('nan'):.3f}  "
            f"recall@p90={selection:.3f}  ripeness_mae={metrics.get('ripeness_mae') or 0:.3f}"
            f"{marker}"
        )

    elapsed = time.perf_counter() - started
    report = {
        "best_recall_at_p90": best_metric,
        "epochs": config.epochs,
        "elapsed_seconds": round(elapsed, 1),
        "train_size": len(train_samples),
        "val_size": len(val_samples),
        "test_size": len(test_samples),
        "history": history,
        "checkpoint": str(best_path),
    }
    (output_dir / "training_report.json").write_text(json.dumps(report, indent=2))
    print(f"\ntraining complete in {elapsed:.1f}s; best checkpoint at {best_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the SATVA screening model.")
    parser.add_argument("--manifest", default="data/manifest.csv")
    parser.add_argument("--output-dir", default="artifacts")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--no-pretrained", action="store_true")
    args = parser.parse_args()

    train(
        TrainConfig(
            manifest=args.manifest,
            output_dir=args.output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.lr,
            device=args.device,
            pretrained=not args.no_pretrained,
        )
    )


if __name__ == "__main__":
    main()
