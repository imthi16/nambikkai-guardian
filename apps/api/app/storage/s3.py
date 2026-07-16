"""S3/MinIO implementation of the object-storage contract."""

from typing import TYPE_CHECKING, Any

import aioboto3

from app.config import Settings

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client


class S3ObjectStorage:
    """Talks to any S3-compatible endpoint (MinIO locally) with private ACLs."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._endpoint = settings.s3_endpoint
        self._session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        )

    def _client(self) -> Any:
        return self._session.client("s3", endpoint_url=self._endpoint)

    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        client: S3Client
        async with self._client() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

    async def get_object(self, key: str) -> bytes:
        client: S3Client
        async with self._client() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            payload: bytes = await response["Body"].read()
            return payload

    async def delete_object(self, key: str) -> None:
        client: S3Client
        async with self._client() as client:
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def presigned_get_url(self, key: str, expires_in_seconds: int) -> str:
        client: S3Client
        async with self._client() as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
            return url

    async def ensure_bucket(self) -> None:
        """Create the bucket when missing; used by local/test bootstrap only."""
        client: S3Client
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self._bucket)
            except client.exceptions.ClientError:
                await client.create_bucket(Bucket=self._bucket)
