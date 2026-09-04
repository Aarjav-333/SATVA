"""Model registry.

Importing this package registers every table on `Base.metadata`, which is what
Alembic autogenerate and the test bootstrap rely on.
"""

from app.db.base import ANALYTICS_SCHEMA, IDENTITY_SCHEMA, Base
from app.models.identity import (
    AuditLog,
    Device,
    Merchant,
    OtpChallenge,
    ScanOwnership,
    User,
)
from app.models.node import DeviceRecord, NodeTelemetry
from app.models.retail import (
    BuyerRating,
    InventoryItem,
    MarketplaceListing,
    RetailOutlet,
    ShelfPrediction,
    SpoilageEvent,
    TrustScore,
)
from app.models.scan import EMBEDDING_DIM, ChemicalReading, Scan, ScanImage
from app.models.trace import CustodyEvent, Farm, ProduceLot, TemperatureTagReading
from app.models.watch import (
    ClusterMember,
    ClusterRun,
    Complaint,
    HotspotCluster,
    MerkleRoot,
)

__all__ = [
    "ANALYTICS_SCHEMA",
    "IDENTITY_SCHEMA",
    "EMBEDDING_DIM",
    "AuditLog",
    "Base",
    "BuyerRating",
    "ChemicalReading",
    "ClusterMember",
    "ClusterRun",
    "Complaint",
    "CustodyEvent",
    "Device",
    "DeviceRecord",
    "Farm",
    "HotspotCluster",
    "InventoryItem",
    "MarketplaceListing",
    "Merchant",
    "MerkleRoot",
    "NodeTelemetry",
    "OtpChallenge",
    "ProduceLot",
    "RetailOutlet",
    "Scan",
    "ScanImage",
    "ScanOwnership",
    "ShelfPrediction",
    "SpoilageEvent",
    "TemperatureTagReading",
    "TrustScore",
    "User",
]
