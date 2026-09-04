"""Tamper-evident custody hash chain and daily Merkle anchoring.

Integrity model (spec 3.2)
--------------------------
Each custody record stores::

    event_hash = SHA-256(canonical_payload || previous_hash)

Changing any historical row changes its hash, which breaks the ``previous_hash``
link of every row after it. Verification is therefore a single linear pass.

A daily **Merkle root** over the day's leaf hashes is published as an external
anchor. Once a root is published, even SATVA's own operators cannot rewrite a
covered custody record without the recomputed root diverging from the published
one. This is what makes the guarantee meaningful against an insider, which is
the whole reason the spec asks for it.

Deliberately not a blockchain. The requirement is tamper-evidence, not
decentralised consensus, and a hash chain in PostgreSQL is simpler, cheaper,
queryable and auditable.

Canonicalisation
----------------
The single most important detail here is that the payload must serialise to
**exactly** the same bytes every time, on every machine, forever. If
canonicalisation is ambiguous, verification fails on rows nobody tampered with,
and a chain that cries wolf is worse than no chain. `canonical_payload` therefore
sorts keys, fixes separators, forces UTF-8, normalises datetimes to UTC ISO-8601
with an explicit offset, and renders decimals as plain strings rather than
floats.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

HASH_ALGORITHM = "sha256-v1"
MERKLE_ALGORITHM = "sha256-merkle-v1"

# The chain's anchor. The first event of every lot links to this constant, so a
# chain of length one is still verifiable and an attacker cannot pass off a
# truncated chain as a complete one.
GENESIS_HASH = "0" * 64


def normalise_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Public form of `_normalise` for a whole payload.

    Callers store **this** structure, not the raw one. Storing the normalised
    form means the bytes that were hashed and the bytes that come back out of
    JSONB are derived from an identical structure, so verification cannot fail
    merely because a UUID round-tripped as a string or a Decimal as a float.
    """
    return _normalise(payload)


def _normalise(value: Any) -> Any:
    """Convert a value to a JSON-safe form with no representation ambiguity."""
    if isinstance(value, datetime):
        # Naive datetimes are assumed UTC: a hash that depends on the reader's
        # local timezone would be non-reproducible.
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        # Plain string, never float: 0.1 has no exact binary representation and
        # float repr has changed across Python versions.
        if not value.is_finite():
            raise ValueError("custody payloads cannot contain NaN or infinite values")
        return format(value.normalize(), "f")
    if isinstance(value, float):
        # A non-finite value must fail loudly rather than be stringified as
        # "NaN" and hashed: a quantity that is not a number has no business
        # entering a tamper-evident record at all.
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("custody payloads cannot contain NaN or infinite values")
        # Fixed precision keeps platform float formatting out of the hash.
        return format(Decimal(repr(value)).normalize(), "f")
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return value


def canonical_payload(payload: dict[str, Any]) -> str:
    """Deterministic JSON serialisation of a custody payload."""
    return json.dumps(
        _normalise(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_event_hash(payload: dict[str, Any], previous_hash: str) -> str:
    """SHA-256 over the canonical payload concatenated with the previous hash."""
    if not isinstance(previous_hash, str) or len(previous_hash) != 64:
        raise ValueError("previous_hash must be a 64-character hex digest")
    material = f"{canonical_payload(payload)}||{previous_hash}".encode()
    return hashlib.sha256(material).hexdigest()


def build_event_payload(
    *,
    lot_id: Any,
    sequence_no: int,
    event_type: str,
    occurred_at: datetime,
    actor_kind: str,
    actor_ref: str | None = None,
    location_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    quantity_kg: float | Decimal | None = None,
    temperature_c: float | None = None,
    humidity_pct: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the exact dict that gets hashed *and* stored.

    Every field that a verifier will later re-read from the database must appear
    here, and nothing that is not stored may appear. A field stored but not
    hashed could be edited undetected; a field hashed but not stored makes
    verification impossible.

    The result is returned already normalised, so the structure written to JSONB
    is byte-identical in meaning to the one that was hashed. Storing a raw dict
    and hashing a normalised one would work today (both canonicalise to the same
    string) but would leave a trap: any future change to normalisation would
    silently invalidate every stored chain.
    """
    return normalise_payload(
        {
            "lot_id": lot_id,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "actor_kind": actor_kind,
            "actor_ref": actor_ref,
            "location_name": location_name,
            "latitude": latitude,
            "longitude": longitude,
            "quantity_kg": quantity_kg,
            "temperature_c": temperature_c,
            "humidity_pct": humidity_pct,
            "extra": extra or {},
        }
    )


@dataclass
class ChainVerification:
    """Outcome of verifying one lot's chain."""

    valid: bool
    length: int
    head_hash: str | None = None
    broken_at_sequence: int | None = None
    failure_kind: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "length": self.length,
            "head_hash": self.head_hash,
            "broken_at_sequence": self.broken_at_sequence,
            "failure_kind": self.failure_kind,
            "detail": self.detail,
        }


@dataclass
class ChainLink:
    """The minimal view of a custody row that verification needs."""

    sequence_no: int
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


def verify_chain(links: list[ChainLink], *, genesis: str = GENESIS_HASH) -> ChainVerification:
    """Verify a lot's custody chain from genesis to head.

    Detects, in order: a missing or out-of-order sequence, a broken link
    (``previous_hash`` not matching the prior row's hash), and a mutated payload
    (recomputed hash not matching the stored hash). Reporting *which* kind of
    failure occurred matters: a broken link means a row was deleted or
    reordered, while a hash mismatch means a row's contents were edited.
    """
    if not links:
        return ChainVerification(valid=True, length=0, head_hash=genesis)

    ordered = sorted(links, key=lambda link: link.sequence_no)
    expected_previous = genesis

    for index, link in enumerate(ordered):
        if link.sequence_no != index:
            return ChainVerification(
                valid=False,
                length=len(ordered),
                broken_at_sequence=link.sequence_no,
                failure_kind="sequence_gap",
                detail=(
                    f"expected sequence {index} but found {link.sequence_no}; "
                    "a custody record is missing or was reordered"
                ),
            )

        if link.previous_hash != expected_previous:
            return ChainVerification(
                valid=False,
                length=len(ordered),
                broken_at_sequence=link.sequence_no,
                failure_kind="broken_link",
                detail=(
                    f"record {link.sequence_no} points at previous hash "
                    f"{link.previous_hash[:12]}... but the preceding record hashes to "
                    f"{expected_previous[:12]}..."
                ),
            )

        recomputed = compute_event_hash(link.payload, link.previous_hash)
        if recomputed != link.event_hash:
            return ChainVerification(
                valid=False,
                length=len(ordered),
                broken_at_sequence=link.sequence_no,
                failure_kind="payload_mutated",
                detail=(
                    f"record {link.sequence_no} stores hash {link.event_hash[:12]}... but its "
                    f"contents hash to {recomputed[:12]}...; the record was altered after it "
                    "was written"
                ),
            )

        expected_previous = link.event_hash

    return ChainVerification(valid=True, length=len(ordered), head_hash=expected_previous)


# --- Merkle tree -------------------------------------------------------------
def _hash_pair(left: str, right: str) -> str:
    return hashlib.sha256(bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def merkle_root(leaf_hashes: list[str]) -> str:
    """Merkle root over a list of hex leaf hashes.

    An odd node at any level is promoted rather than duplicated. Duplicating the
    last node is the classic CVE-2012-2459 pattern, where two different leaf
    sets can produce the same root; promotion avoids that entirely.
    """
    if not leaf_hashes:
        return GENESIS_HASH

    level = list(leaf_hashes)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(_hash_pair(level[i], level[i + 1]))
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def merkle_proof(leaf_hashes: list[str], index: int) -> list[tuple[str, str]]:
    """Inclusion proof for one leaf, as a list of (side, sibling_hash).

    ``side`` says whether the sibling goes on the "left" or the "right" when
    hashing. A consumer can use this to prove that one custody record is covered
    by a published daily root, without needing the rest of the day's records.
    """
    if not 0 <= index < len(leaf_hashes):
        raise IndexError("leaf index out of range")

    proof: list[tuple[str, str]] = []
    level = list(leaf_hashes)
    position = index

    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level) - 1, 2):
            left, right = level[i], level[i + 1]
            if position == i:
                proof.append(("right", right))
            elif position == i + 1:
                proof.append(("left", left))
            nxt.append(_hash_pair(left, right))

        if len(level) % 2 == 1:
            promoted_index = len(level) - 1
            nxt.append(level[-1])
            if position == promoted_index:
                position = len(nxt) - 1
            else:
                position //= 2
        else:
            position //= 2
        level = nxt

    return proof


def verify_merkle_proof(leaf_hash: str, proof: list[tuple[str, str]], root: str) -> bool:
    """Check an inclusion proof against a published root."""
    current = leaf_hash
    for side, sibling in proof:
        current = _hash_pair(sibling, current) if side == "left" else _hash_pair(current, sibling)
    return current == root
