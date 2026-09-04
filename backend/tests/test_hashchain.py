"""Hash-chain and Merkle tests.

These are pure-function tests: they exercise the integrity primitives without a
database, which is what lets them cover tampering scenarios exhaustively and
run in milliseconds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.services.trace.hashchain import (
    GENESIS_HASH,
    ChainLink,
    build_event_payload,
    canonical_payload,
    compute_event_hash,
    merkle_proof,
    merkle_root,
    verify_chain,
    verify_merkle_proof,
)

LOT = UUID("11111111-2222-3333-4444-555555555555")
T0 = datetime(2026, 9, 1, 6, 30, tzinfo=UTC)


def make_chain(length: int) -> list[ChainLink]:
    links: list[ChainLink] = []
    previous = GENESIS_HASH
    for i in range(length):
        payload = build_event_payload(
            lot_id=LOT,
            sequence_no=i,
            event_type=f"event_{i}",
            occurred_at=T0 + timedelta(hours=i),
            actor_kind="farmer",
            actor_ref=f"actor-{i}",
            quantity_kg=Decimal("120.50"),
        )
        digest = compute_event_hash(payload, previous)
        links.append(ChainLink(i, payload, previous, digest))
        previous = digest
    return links


class TestCanonicalisation:
    def test_key_order_does_not_change_the_serialisation(self):
        a = {"z": 1, "a": 2, "m": {"y": 3, "b": 4}}
        b = {"a": 2, "m": {"b": 4, "y": 3}, "z": 1}
        assert canonical_payload(a) == canonical_payload(b)

    def test_naive_datetimes_are_treated_as_utc(self):
        aware = {"t": datetime(2026, 9, 1, 6, 30, tzinfo=UTC)}
        naive = {"t": datetime(2026, 9, 1, 6, 30)}
        assert canonical_payload(aware) == canonical_payload(naive)

    def test_equivalent_instants_in_other_zones_serialise_identically(self):
        from datetime import timezone

        ist = timezone(timedelta(hours=5, minutes=30))
        a = {"t": datetime(2026, 9, 1, 12, 0, tzinfo=ist)}
        b = {"t": datetime(2026, 9, 1, 6, 30, tzinfo=UTC)}
        assert canonical_payload(a) == canonical_payload(b)

    def test_decimal_and_float_render_without_binary_artefacts(self):
        assert '"q":"0.1"' in canonical_payload({"q": Decimal("0.1")})
        assert '"q":"0.1"' in canonical_payload({"q": 0.1})

    def test_decimal_trailing_zeros_are_normalised(self):
        assert canonical_payload({"q": Decimal("120.50")}) == canonical_payload(
            {"q": Decimal("120.5")}
        )

    def test_uuid_renders_as_its_canonical_string(self):
        assert str(LOT) in canonical_payload({"lot": LOT})

    def test_nan_is_rejected_rather_than_silently_serialised(self):
        with pytest.raises(ValueError):
            canonical_payload({"q": float("nan")})


class TestChainConstruction:
    def test_first_event_links_to_genesis(self):
        chain = make_chain(1)
        assert chain[0].previous_hash == GENESIS_HASH

    def test_hash_is_deterministic(self):
        payload = build_event_payload(
            lot_id=LOT,
            sequence_no=0,
            event_type="lot_registered",
            occurred_at=T0,
            actor_kind="farmer",
        )
        assert compute_event_hash(payload, GENESIS_HASH) == compute_event_hash(payload, GENESIS_HASH)

    def test_hash_depends_on_the_previous_hash(self):
        payload = build_event_payload(
            lot_id=LOT, sequence_no=0, event_type="x", occurred_at=T0, actor_kind="farmer"
        )
        assert compute_event_hash(payload, GENESIS_HASH) != compute_event_hash(payload, "a" * 64)

    def test_malformed_previous_hash_is_rejected(self):
        payload = build_event_payload(
            lot_id=LOT, sequence_no=0, event_type="x", occurred_at=T0, actor_kind="farmer"
        )
        for bad in ("", "abc", "z" * 63):
            with pytest.raises(ValueError):
                compute_event_hash(payload, bad)


class TestChainVerification:
    def test_intact_chain_verifies(self):
        result = verify_chain(make_chain(6))
        assert result.valid is True
        assert result.length == 6
        assert result.head_hash == make_chain(6)[-1].event_hash

    def test_empty_chain_is_valid_and_sits_at_genesis(self):
        result = verify_chain([])
        assert result.valid is True
        assert result.head_hash == GENESIS_HASH

    def test_verification_is_order_independent(self):
        chain = make_chain(5)
        assert verify_chain(list(reversed(chain))).valid is True

    def test_detects_a_mutated_payload_in_the_middle(self):
        chain = make_chain(6)
        chain[3].payload["quantity_kg"] = "999.00"
        result = verify_chain(chain)
        assert result.valid is False
        assert result.failure_kind == "payload_mutated"
        assert result.broken_at_sequence == 3

    def test_detects_a_mutated_payload_at_the_head(self):
        chain = make_chain(4)
        chain[-1].payload["actor_ref"] = "someone-else"
        result = verify_chain(chain)
        assert result.valid is False
        assert result.broken_at_sequence == 3

    def test_detects_a_backdated_timestamp(self):
        """The classic tamper: making a handover look earlier than it was."""
        chain = make_chain(5)
        chain[2].payload["occurred_at"] = (T0 - timedelta(days=3)).isoformat(
            timespec="microseconds"
        )
        result = verify_chain(chain)
        assert result.valid is False
        assert result.failure_kind == "payload_mutated"

    def test_detects_a_deleted_record(self):
        chain = make_chain(6)
        del chain[3]
        result = verify_chain(chain)
        assert result.valid is False
        assert result.failure_kind == "sequence_gap"

    def test_detects_a_rewritten_link(self):
        chain = make_chain(5)
        chain[2] = ChainLink(2, chain[2].payload, "f" * 64, chain[2].event_hash)
        result = verify_chain(chain)
        assert result.valid is False
        assert result.failure_kind == "broken_link"

    def test_detects_a_wholesale_forged_suffix(self):
        """An attacker who rewrites a row and re-hashes everything after it still
        fails, because the chain no longer starts from genesis consistently."""
        chain = make_chain(6)
        chain[2].payload["quantity_kg"] = "1.00"
        rehashed = chain[:2]
        previous = chain[1].event_hash
        for link in chain[2:]:
            digest = compute_event_hash(link.payload, previous)
            rehashed.append(ChainLink(link.sequence_no, link.payload, previous, digest))
            previous = digest
        # The forged chain is internally consistent...
        assert verify_chain(rehashed).valid is True
        # ...but its head hash differs from the genuine one, which is what the
        # published daily Merkle root pins down.
        assert rehashed[-1].event_hash != chain[-1].event_hash

    def test_appending_does_not_invalidate_history(self):
        chain = make_chain(4)
        extra = build_event_payload(
            lot_id=LOT,
            sequence_no=4,
            event_type="retailer_received",
            occurred_at=T0 + timedelta(hours=4),
            actor_kind="retailer",
        )
        digest = compute_event_hash(extra, chain[-1].event_hash)
        chain.append(ChainLink(4, extra, chain[-1].event_hash, digest))
        assert verify_chain(chain).valid is True


class TestMerkle:
    def test_empty_tree_returns_genesis(self):
        assert merkle_root([]) == GENESIS_HASH

    def test_single_leaf_is_its_own_root(self):
        leaf = compute_event_hash({"a": 1}, GENESIS_HASH)
        assert merkle_root([leaf]) == leaf

    def test_root_is_deterministic(self):
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(9)]
        assert merkle_root(leaves) == merkle_root(list(leaves))

    def test_root_changes_when_any_leaf_changes(self):
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(9)]
        original = merkle_root(leaves)
        for index in range(len(leaves)):
            mutated = list(leaves)
            mutated[index] = compute_event_hash({"i": index, "tampered": True}, GENESIS_HASH)
            assert merkle_root(mutated) != original

    def test_root_is_order_sensitive(self):
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(4)]
        swapped = [leaves[1], leaves[0], leaves[2], leaves[3]]
        assert merkle_root(swapped) != merkle_root(leaves)

    @pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 33])
    def test_inclusion_proofs_verify_for_every_leaf(self, size):
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(size)]
        root = merkle_root(leaves)
        for index, leaf in enumerate(leaves):
            proof = merkle_proof(leaves, index)
            assert verify_merkle_proof(leaf, proof, root), f"size={size} index={index}"

    def test_a_proof_does_not_validate_a_different_leaf(self):
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(8)]
        root = merkle_root(leaves)
        proof = merkle_proof(leaves, 3)
        forged = compute_event_hash({"i": 3, "tampered": True}, GENESIS_HASH)
        assert verify_merkle_proof(forged, proof, root) is False

    def test_duplicate_last_node_attack_is_not_possible(self):
        """CVE-2012-2459: duplicating an odd trailing node lets two different
        leaf sets share a root. Promotion instead of duplication avoids it."""
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(3)]
        duplicated = [*leaves, leaves[-1]]
        assert merkle_root(leaves) != merkle_root(duplicated)

    def test_out_of_range_proof_index_raises(self):
        leaves = [compute_event_hash({"i": i}, GENESIS_HASH) for i in range(4)]
        with pytest.raises(IndexError):
            merkle_proof(leaves, 4)


class TestTamperScenarios:
    """End-to-end narratives from the risk table, expressed as tests."""

    def test_an_operator_cannot_silently_backdate_a_published_day(self):
        chain = make_chain(5)
        leaves = [link.event_hash for link in chain]
        published_root = merkle_root(leaves)

        # Operator edits a record and re-hashes the whole chain to hide it.
        chain[1].payload["occurred_at"] = (T0 - timedelta(days=1)).isoformat(
            timespec="microseconds"
        )
        previous = chain[0].event_hash
        forged = [chain[0]]
        for link in chain[1:]:
            digest = compute_event_hash(link.payload, previous)
            forged.append(ChainLink(link.sequence_no, link.payload, previous, digest))
            previous = digest

        assert verify_chain(forged).valid is True  # internally consistent
        assert merkle_root([link.event_hash for link in forged]) != published_root

    def test_an_inserted_record_is_detected(self):
        chain = make_chain(4)
        injected = build_event_payload(
            lot_id=uuid4(),
            sequence_no=2,
            event_type="transport_arrive",
            occurred_at=T0,
            actor_kind="transporter",
        )
        chain.insert(2, ChainLink(2, injected, chain[1].event_hash, "d" * 64))
        result = verify_chain(chain)
        assert result.valid is False
