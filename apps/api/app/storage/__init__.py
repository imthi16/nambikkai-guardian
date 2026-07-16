"""Object storage behind an interface so providers stay swappable."""

from app.storage.base import ObjectStorage
from app.storage.s3 import S3ObjectStorage

__all__ = ["ObjectStorage", "S3ObjectStorage"]
