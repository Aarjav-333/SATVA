"""S3-compatible object storage for evidence images.

Upload safety
-------------
Uploaded bytes are validated by **content**, not by the client-supplied
Content-Type or file extension, both of which an attacker controls. Pillow is
asked to parse and verify the image; anything that does not decode as a
supported raster format is rejected before it reaches storage.

Images are stored under keys that embed the scan id, so the retention job can
find and delete everything belonging to a scan without a database scan.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image

from app.core.config import settings
from app.core.errors import UploadRejected
from app.core.logging import get_logger

log = get_logger("satva.storage")

# Formats Pillow must be able to decode for an upload to be accepted.
_ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP"}
_PIL_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

# A decompression-bomb guard. Pillow has its own limit, but an explicit pixel
# cap keeps a 5 MB file from expanding into gigabytes of RAM on a small host.
MAX_PIXELS = 40_000_000


@dataclass
class StoredObject:
    object_key: str
    sha256: str
    byte_size: int
    content_type: str
    width: int | None = None
    height: int | None = None


def validate_image(data: bytes, *, declared_type: str | None = None) -> tuple[str, int, int]:
    """Validate uploaded bytes as an image. Returns (mime, width, height)."""
    if not data:
        raise UploadRejected("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise UploadRejected(
            f"Images must be {settings.max_upload_bytes // (1024 * 1024)} MB or smaller.",
            details={"byte_size": len(data), "limit": settings.max_upload_bytes},
        )

    try:
        with Image.open(io.BytesIO(data)) as probe:
            image_format = (probe.format or "").upper()
            width, height = probe.size
            # verify() consumes the file object, so it goes last.
            probe.verify()
    except Exception as exc:  # noqa: BLE001 - any parse failure is a rejection
        raise UploadRejected(
            "That file is not a readable image.", details={"reason": str(exc)[:200]}
        ) from exc

    if image_format not in _ALLOWED_PIL_FORMATS:
        raise UploadRejected(
            f"Image format '{image_format or 'unknown'}' is not accepted.",
            details={"allowed": sorted(_ALLOWED_PIL_FORMATS)},
        )

    if width * height > MAX_PIXELS:
        raise UploadRejected(
            "That image has too many pixels to process safely.",
            details={"width": width, "height": height, "max_pixels": MAX_PIXELS},
        )

    mime = _PIL_TO_MIME[image_format]
    if declared_type and declared_type not in settings.allowed_image_type_set:
        raise UploadRejected(
            f"Content type '{declared_type}' is not accepted.",
            details={"allowed": sorted(settings.allowed_image_type_set)},
        )
    return mime, width, height


def evidence_key(scan_id: uuid.UUID, kind: str, extension: str) -> str:
    """Deterministic, non-guessable object key partitioned by date."""
    stamp = datetime.now(UTC).strftime("%Y/%m/%d")
    return f"evidence/{stamp}/{scan_id}/{kind}-{uuid.uuid4().hex[:12]}.{extension}"


class ObjectStore:
    """Thin S3 wrapper.

    Works against MinIO locally and any S3-compatible service (Cloudflare R2,
    AWS S3) in deployment; only the endpoint and credentials change.
    """

    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            )
        return self._client

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=settings.s3_bucket)
        except ClientError:
            try:
                self.client.create_bucket(Bucket=settings.s3_bucket)
                log.info("bucket_created", bucket=settings.s3_bucket)
            except ClientError as exc:
                log.error("bucket_create_failed", bucket=settings.s3_bucket, error=str(exc))
                raise

    def put_image(
        self, scan_id: uuid.UUID, kind: str, data: bytes, *, declared_type: str | None = None
    ) -> StoredObject:
        mime, width, height = validate_image(data, declared_type=declared_type)
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
        key = evidence_key(scan_id, kind, extension)
        digest = hashlib.sha256(data).hexdigest()

        self.client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=mime,
            # Object metadata is not a security control, but recording the
            # digest alongside the object lets integrity be re-checked without
            # the database.
            Metadata={"sha256": digest, "scan-id": str(scan_id), "kind": kind},
        )
        log.info("image_stored", scan_id=str(scan_id), kind=kind, bytes=len(data), key=key)
        return StoredObject(key, digest, len(data), mime, width, height)

    def get(self, object_key: str) -> bytes:
        response = self.client.get_object(Bucket=settings.s3_bucket, Key=object_key)
        return response["Body"].read()

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=settings.s3_bucket, Key=object_key)

    def put_document(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.client.put_object(
            Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
        )
        return StoredObject(key, hashlib.sha256(data).hexdigest(), len(data), content_type)

    def presigned_url(self, object_key: str, *, expires_in: int = 900) -> str:
        """Short-lived download URL.

        Fifteen minutes by default: long enough to click, short enough that a
        URL captured from a log or a shared screen stops working quickly.
        """
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": object_key},
            ExpiresIn=expires_in,
        )
        if settings.s3_public_endpoint_url != settings.s3_endpoint_url:
            url = url.replace(settings.s3_endpoint_url, settings.s3_public_endpoint_url, 1)
        return url


object_store = ObjectStore()
