"""Duplicate and manipulation resistance for SATVA Watch.

Threat model (spec 3.3 and the risk table)
------------------------------------------
The attack SATVA must resist is a trader submitting the same photograph
repeatedly -- or a small group coordinating -- to manufacture a hotspot over a
competitor. Three independent defences apply, and a submission has to defeat all
three to count:

1. **Perceptual hashing.** A pHash survives re-compression, mild crops and
   resizing, so re-uploading the same photo is caught even after it has been
   passed through a messaging app.
2. **Embedding similarity.** Near-duplicates that defeat pHash -- the same fruit
   photographed twice, seconds apart, from a slightly different angle -- are
   caught by cosine similarity on the model's penultimate feature vector.
3. **Independent-device corroboration.** Enforced in `clustering.py`: no cluster
   is publishable unless enough distinct device pseudonyms contributed.

Layers 1 and 2 stop one actor submitting one image many times. Layer 3 stops one
actor submitting many *different* images. Neither is sufficient alone.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.scan import Scan

log = get_logger("satva.watch.dedup")

PHASH_SIZE = 8  # 64-bit hash
PHASH_HIGHFREQ_FACTOR = 4
EMBEDDING_DUPLICATE_COSINE = 0.985


def compute_phash(image_bytes: bytes) -> str:
    """64-bit perceptual hash (DCT-based), returned as 16 hex characters.

    Implemented here rather than pulled from a library at call time so that the
    exact algorithm is pinned: the mobile client computes the same hash offline,
    and a mismatch would let duplicates through.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        grey = img.convert("L").resize(
            (PHASH_SIZE * PHASH_HIGHFREQ_FACTOR, PHASH_SIZE * PHASH_HIGHFREQ_FACTOR),
            Image.Resampling.LANCZOS,
        )
        pixels = np.asarray(grey, dtype=np.float64)

    # 2-D DCT-II via two 1-D transforms.
    coefficients = _dct2(pixels)
    low_frequency = coefficients[:PHASH_SIZE, :PHASH_SIZE]

    # The DC term encodes overall brightness, not structure. Including it would
    # make the hash change whenever exposure changes, which is the opposite of
    # what a perceptual hash is for.
    median = np.median(low_frequency[1:].flatten() if low_frequency.size > 1 else low_frequency)
    bits = (low_frequency > median).flatten()

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _dct2(matrix: np.ndarray) -> np.ndarray:
    return _dct1(_dct1(matrix.T).T)


def _dct1(matrix: np.ndarray) -> np.ndarray:
    n = matrix.shape[0]
    k = np.arange(n).reshape(-1, 1)
    i = np.arange(n).reshape(1, -1)
    basis = np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    return basis @ matrix


def hamming_distance(a: str, b: str) -> int:
    """Bit distance between two hex perceptual hashes."""
    if len(a) != len(b):
        raise ValueError("perceptual hashes must be the same length")
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator < 1e-12:
        return 0.0
    return float(np.dot(a, b) / denominator)


@dataclass
class DuplicateVerdict:
    is_duplicate: bool
    matched_scan_id: uuid.UUID | None = None
    method: str | None = None
    distance: float | None = None
    detail: str | None = None

    def to_dict(self) -> dict:
        return {
            "is_duplicate": self.is_duplicate,
            "matched_scan_id": str(self.matched_scan_id) if self.matched_scan_id else None,
            "method": self.method,
            "distance": self.distance,
            "detail": self.detail,
        }


def find_duplicate(
    db: Session,
    *,
    perceptual_hash: str | None,
    embedding: np.ndarray | None = None,
    device_pseudonym: str | None = None,
    exclude_scan_id: uuid.UUID | None = None,
    lookback_days: int = 90,
    hamming_threshold: int | None = None,
) -> DuplicateVerdict:
    """Look for an earlier scan that is the same photograph.

    A match from a *different* device is treated as more serious than a repeat
    from the same device: the former is the coordinated-reporting attack, while
    the latter is usually a user tapping submit twice. Both collapse to a single
    contribution, but the detail string distinguishes them for the officer view.
    """
    threshold = (
        hamming_threshold
        if hamming_threshold is not None
        else settings.phash_duplicate_hamming_threshold
    )
    since = datetime.now(UTC) - timedelta(days=lookback_days)

    if perceptual_hash:
        statement = select(Scan).where(
            Scan.perceptual_hash.is_not(None),
            Scan.created_at >= since,
            Scan.duplicate_of_scan_id.is_(None),
        )
        if exclude_scan_id is not None:
            statement = statement.where(Scan.id != exclude_scan_id)

        # Exact match first: cheap, indexed, and the overwhelmingly common case.
        exact = db.scalar(statement.where(Scan.perceptual_hash == perceptual_hash).limit(1))
        if exact is not None:
            return DuplicateVerdict(
                True,
                exact.id,
                "phash_exact",
                0.0,
                _describe(exact, device_pseudonym, "an identical photograph"),
            )

        # Near match: scan the recent window. Bounded by the lookback and by the
        # confirmed-scan volume, so a linear pass is acceptable at pilot scale.
        # At national scale this becomes a BK-tree or a pg_bktree index; the
        # interface does not change.
        for candidate in db.scalars(statement.limit(5000)):
            distance = hamming_distance(perceptual_hash, candidate.perceptual_hash)
            if distance <= threshold:
                return DuplicateVerdict(
                    True,
                    candidate.id,
                    "phash_near",
                    float(distance),
                    _describe(candidate, device_pseudonym, "a near-identical photograph"),
                )

    if embedding is not None:
        statement = select(Scan).where(
            Scan.embedding.is_not(None),
            Scan.created_at >= since,
            Scan.duplicate_of_scan_id.is_(None),
        )
        if exclude_scan_id is not None:
            statement = statement.where(Scan.id != exclude_scan_id)
        # pgvector orders by distance in the database; only the nearest few
        # need to be examined.
        nearest = db.scalars(
            statement.order_by(Scan.embedding.cosine_distance(embedding)).limit(5)
        )
        for candidate in nearest:
            similarity = cosine_similarity(embedding, np.asarray(candidate.embedding))
            if similarity >= EMBEDDING_DUPLICATE_COSINE:
                return DuplicateVerdict(
                    True,
                    candidate.id,
                    "embedding_near",
                    round(similarity, 5),
                    _describe(candidate, device_pseudonym, "a visually near-identical image"),
                )

    return DuplicateVerdict(False)


def _describe(candidate: Scan, device_pseudonym: str | None, what: str) -> str:
    same_device = device_pseudonym is not None and candidate.device_pseudonym == device_pseudonym
    origin = "the same device" if same_device else "a different device"
    return f"{what} was already submitted from {origin} on {candidate.created_at:%d %b %Y}"
