"""Datasets and augmentation for the SATVA screening model.

Data sources (spec 6.3)
-----------------------
1. **Public produce-quality datasets** -- bootstrap the backbone.
2. **Field dataset** -- Palakkad and Thrissur markets: real ambient lighting,
   multiple cultivars, multiple times of day, with labels confirmed by strip
   testing *at the moment of capture*.
3. **Controlled college-lab dataset** -- identical fruit from one lot ripened
   three ways: naturally, with permitted ethylene, and with calcium carbide.

The third source is the one that answers "how do you know your labels are
correct?", because the treatment is known directly rather than inferred. It
should be held out as the calibration and evaluation set for exactly that
reason, which `stratified_split` supports through the `source` field.

Expected on-disk layout
-----------------------
    ml/data/
      manifest.csv          image_path,crop,cultivar,label,ripeness,source,captured_at
      images/...

`label` is 1 for forced/carbide-ripened and 0 for naturally or permissibly
ripened. `ripeness` is a float in [0, 1]. `source` is one of
`public`, `field`, `controlled`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision import transforms

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
INPUT_SIZE = 224

VALID_SOURCES = {"public", "field", "controlled", "synthetic_dev"}


@dataclass
class Sample:
    image_path: Path
    crop: str
    cultivar: str
    label: int
    ripeness: float
    source: str


def read_manifest(manifest_path: Path) -> list[Sample]:
    samples: list[Sample] = []
    root = manifest_path.parent
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = Path(row["image_path"])
            samples.append(
                Sample(
                    image_path=path if path.is_absolute() else root / path,
                    crop=row["crop"].strip().lower(),
                    cultivar=(row.get("cultivar") or "unspecified").strip().lower(),
                    label=int(row["label"]),
                    ripeness=float(row.get("ripeness") or 0.5),
                    source=(row.get("source") or "field").strip().lower(),
                )
            )
    return samples


def build_transforms(train: bool) -> transforms.Compose:
    """Augmentation pipeline.

    Spec 6.2 is explicit that the deployment environment is a poorly lit market
    stall, not a photography studio, so the photometric augmentation is
    aggressive on purpose:

    * brightness, contrast, saturation and hue jitter -- market lighting ranges
      from direct sun to a single tungsten bulb;
    * random gamma -- different handsets apply very different tone curves;
    * motion blur -- people do not hold a phone still while shopping;
    * JPEG compression -- every Android camera pipeline compresses, and
      compression artefacts sit exactly in the high-frequency band where
      speckled carbide burns live.

    Geometric augmentation stays mild. Produce has a canonical orientation
    (a stem end and a blossom end) and the calyx-versus-body colour comparison
    is one of the morphological cues the model relies on, so aggressive rotation
    would destroy signal rather than add invariance.
    """
    if not train:
        return transforms.Compose(
            [
                transforms.Resize(int(INPUT_SIZE * 1.14)),
                transforms.CenterCrop(INPUT_SIZE),
                transforms.ToTensor(),
                transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
            ]
        )

    return transforms.Compose(
        [
            transforms.RandomResizedCrop(INPUT_SIZE, scale=(0.65, 1.0), ratio=(0.8, 1.25)),
            transforms.RandomHorizontalFlip(0.5),
            transforms.RandomApply([transforms.RandomRotation(12)], p=0.3),
            transforms.ColorJitter(brightness=0.45, contrast=0.35, saturation=0.35, hue=0.06),
            transforms.RandomApply([RandomGamma(0.6, 1.6)], p=0.4),
            transforms.RandomApply(
                [transforms.GaussianBlur(kernel_size=5, sigma=(0.3, 2.2))], p=0.3
            ),
            transforms.RandomApply([JpegArtefacts(quality_range=(35, 90))], p=0.4),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
            transforms.RandomErasing(p=0.2, scale=(0.02, 0.10)),
        ]
    )


class RandomGamma:
    """Random gamma adjustment, modelling handset tone-curve variation."""

    def __init__(self, low: float = 0.6, high: float = 1.6):
        self.low = low
        self.high = high

    def __call__(self, image: Image.Image) -> Image.Image:
        gamma = float(np.random.uniform(self.low, self.high))
        table = [min(255, int((i / 255.0) ** gamma * 255.0 + 0.5)) for i in range(256)]
        return image.point(table * len(image.getbands()))


class JpegArtefacts:
    """Re-encode as JPEG at a random quality.

    Training on pristine PNGs and deploying on compressed camera output is a
    classic domain gap: the model learns high-frequency detail that simply is
    not present at inference time.
    """

    def __init__(self, quality_range: tuple[int, int] = (35, 90)):
        self.quality_range = quality_range

    def __call__(self, image: Image.Image) -> Image.Image:
        import io

        quality = int(np.random.randint(*self.quality_range))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality)
        buffer.seek(0)
        return Image.open(buffer).convert("RGB")


class ProduceDataset(Dataset):
    """Image dataset yielding (tensor, anomaly_label, ripeness_target)."""

    def __init__(self, samples: list[Sample], train: bool = True):
        self.samples = samples
        self.transform = build_transforms(train)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int):
        sample = self.samples[index]
        with Image.open(sample.image_path) as raw:
            image = raw.convert("RGB")
        tensor = self.transform(image)
        return (
            tensor,
            torch.tensor(float(sample.label), dtype=torch.float32),
            torch.tensor(float(sample.ripeness), dtype=torch.float32),
        )


def stratified_split(
    samples: list[Sample],
    *,
    val_fraction: float = 0.15,
    test_fraction: float = 0.15,
    seed: int = 20260904,
    hold_out_controlled_for_test: bool = True,
) -> tuple[list[Sample], list[Sample], list[Sample]]:
    """Split by crop and label, keeping the controlled set for evaluation.

    Holding the controlled-ripening set out of training is deliberate. It is the
    only data whose labels come from a known treatment rather than an inferred
    one, so it is the only honest basis for a sensitivity/specificity claim.
    Training on it would leave nothing trustworthy to measure against.
    """
    rng = np.random.default_rng(seed)

    controlled = [s for s in samples if s.source == "controlled"]
    others = [s for s in samples if s.source != "controlled"]

    buckets: dict[tuple[str, int], list[Sample]] = {}
    for sample in others:
        buckets.setdefault((sample.crop, sample.label), []).append(sample)

    train: list[Sample] = []
    val: list[Sample] = []
    test: list[Sample] = []

    for group in buckets.values():
        indices = rng.permutation(len(group))
        n_val = max(1, int(len(group) * val_fraction)) if len(group) > 4 else 0
        n_test = max(1, int(len(group) * test_fraction)) if len(group) > 4 else 0
        for position, index in enumerate(indices):
            if position < n_val:
                val.append(group[index])
            elif position < n_val + n_test:
                test.append(group[index])
            else:
                train.append(group[index])

    if hold_out_controlled_for_test:
        test.extend(controlled)
    else:
        train.extend(controlled)

    return train, val, test


def class_weights(samples: list[Sample]) -> torch.Tensor:
    """Positive-class weight for BCEWithLogitsLoss.

    Adulterated samples are the minority in any realistic collection. Without
    reweighting, the model can score well by predicting "clean" for everything --
    which is precisely the failure that would make SATVA useless.
    """
    positives = sum(1 for s in samples if s.label == 1)
    negatives = len(samples) - positives
    if positives == 0:
        return torch.tensor(1.0)
    return torch.tensor(negatives / positives, dtype=torch.float32)
