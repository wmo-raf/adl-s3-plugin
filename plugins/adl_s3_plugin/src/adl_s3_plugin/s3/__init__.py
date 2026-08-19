"""
S3-compatible storage client for ADL.
Works with AWS S3, MinIO, and other S3-compatible services.
"""
import logging
from io import BytesIO
from typing import Optional, List, Dict, Any

import boto3
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)

S3_CONNECTION_ERRORS = (
    ClientError,
    BotoCoreError,
    ConnectionError,
    TimeoutError,
)


class S3Error(Exception):
    """Base class for S3 errors"""

    def __init__(self, message: str, status: int = 500, code: str = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def __str__(self):
        if self.code:
            return f"{self.message} (Code: {self.code}, HTTP {self.status})"
        return f"{self.message} (HTTP {self.status})"


def map_s3_error(error: Exception) -> tuple:
    """Map boto3/S3 errors to human-readable messages and HTTP status codes."""
    if isinstance(error, ClientError):
        error_code = error.response.get('Error', {}).get('Code', 'Unknown')
        error_message = error.response.get('Error', {}).get('Message', str(error))
        http_status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 500)

        error_map = {
            'NoSuchBucket': (f"Bucket does not exist: {error_message}", 404),
            'NoSuchKey': (f"Object not found: {error_message}", 404),
            'AccessDenied': (f"Access denied: {error_message}", 403),
            'InvalidAccessKeyId': ("Invalid access key ID", 401),
            'SignatureDoesNotMatch': ("Invalid secret access key", 401),
            'ExpiredToken': ("Security token has expired", 401),
            'InvalidBucketName': (f"Invalid bucket name: {error_message}", 400),
            'BucketAlreadyExists': (f"Bucket already exists: {error_message}", 409),
            'BucketNotEmpty': ("Bucket is not empty", 409),
        }

        if error_code in error_map:
            return error_map[error_code][0], error_map[error_code][1], error_code

        return error_message, http_status, error_code

    elif isinstance(error, BotoCoreError):
        return str(error), 500, 'BotoCoreError'

    return str(error), 500, 'Unknown'


class S3Client:
    """
    S3-compatible storage client.

    Provides a unified interface for interacting with S3-compatible storage services,
    including AWS S3, MinIO, and others. The interface mirrors the FTP client pattern
    used elsewhere in ADL for consistency.
    """

    def __init__(
            self,
            bucket_name: str,
            access_key: str,
            secret_key: str,
            endpoint_url: Optional[str] = None,
            region: str = "us-east-1",
            use_ssl: bool = True,
            verify_ssl: bool = True,
            **kwargs
    ):
        """
        Initialize S3 client.

        Args:
            bucket_name: Name of the S3 bucket
            access_key: AWS access key ID or MinIO access key
            secret_key: AWS secret access key or MinIO secret key
            endpoint_url: Custom endpoint URL for MinIO/S3-compatible services.
                         Leave None for AWS S3.
            region: AWS region (default: us-east-1)
            use_ssl: Whether to use SSL/TLS (default: True)
            verify_ssl: Whether to verify SSL certificates (default: True).
                       Set to False for self-signed certs in development.
        """
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url
        self.region = region

        try:
            client_kwargs = {
                "service_name": "s3",
                "aws_access_key_id": access_key,
                "aws_secret_access_key": secret_key,
                "region_name": region,
                "use_ssl": use_ssl,
            }

            if endpoint_url:
                client_kwargs["endpoint_url"] = endpoint_url

            if not verify_ssl:
                client_kwargs["verify"] = False

            self.client = boto3.client(**client_kwargs)

            # Verify connection by checking if bucket exists
            self.client.head_bucket(Bucket=bucket_name)
            logger.debug(f"[S3] Connected to bucket '{bucket_name}'")

        except S3_CONNECTION_ERRORS as e:
            message, status, code = map_s3_error(e)
            raise S3Error(message, status, code)

    def put(self, file_obj: BytesIO, remote_path: str, content_type: str = "text/csv") -> Dict[str, Any]:
        """
        Upload a file to S3.

        Args:
            file_obj: File-like object (BytesIO) to upload
            remote_path: Object key (path) in the bucket. Leading slashes are stripped.
            content_type: MIME type of the file (default: text/csv)

        Returns:
            Dict containing upload response metadata

        Raises:
            S3Error: If upload fails
        """
        # Strip leading slash if present (S3 keys don't need them)
        key = remote_path.lstrip('/')

        try:
            # Ensure we're at the start of the file
            file_obj.seek(0)

            response = self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=file_obj,
                ContentType=content_type,
            )

            logger.debug(f"[S3] Uploaded '{key}' to bucket '{self.bucket_name}'")
            return response

        except S3_CONNECTION_ERRORS as e:
            message, status, code = map_s3_error(e)
            raise S3Error(f"Failed to upload '{key}': {message}", status, code)

    def get(self, remote_path: str) -> bytes:
        """
        Download a file from S3.

        Args:
            remote_path: Object key (path) in the bucket

        Returns:
            File contents as bytes

        Raises:
            S3Error: If download fails or file doesn't exist
        """
        key = remote_path.lstrip('/')

        try:
            response = self.client.get_object(
                Bucket=self.bucket_name,
                Key=key,
            )

            content = response['Body'].read()
            logger.debug(f"[S3] Downloaded '{key}' from bucket '{self.bucket_name}'")
            return content

        except S3_CONNECTION_ERRORS as e:
            message, status, code = map_s3_error(e)
            raise S3Error(f"Failed to download '{key}': {message}", status, code)

    def exists(self, remote_path: str) -> bool:
        """
        Check if an object exists in S3.

        Args:
            remote_path: Object key (path) in the bucket

        Returns:
            True if object exists, False otherwise
        """
        key = remote_path.lstrip('/')

        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise S3Error(*map_s3_error(e))

    def delete(self, remote_path: str) -> Dict[str, Any]:
        """
        Delete an object from S3.

        Args:
            remote_path: Object key (path) in the bucket

        Returns:
            Dict containing delete response metadata

        Raises:
            S3Error: If deletion fails
        """
        key = remote_path.lstrip('/')

        try:
            response = self.client.delete_object(
                Bucket=self.bucket_name,
                Key=key,
            )

            logger.debug(f"[S3] Deleted '{key}' from bucket '{self.bucket_name}'")
            return response

        except S3_CONNECTION_ERRORS as e:
            message, status, code = map_s3_error(e)
            raise S3Error(f"Failed to delete '{key}': {message}", status, code)

    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> List[Dict[str, Any]]:
        """
        List objects in the bucket with optional prefix filter.

        Args:
            prefix: Filter objects by prefix (like a directory path)
            max_keys: Maximum number of objects to return

        Returns:
            List of object metadata dicts with keys: Key, Size, LastModified, ETag

        Raises:
            S3Error: If listing fails
        """
        prefix = prefix.lstrip('/')

        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys,
            )

            objects = []
            for obj in response.get('Contents', []):
                objects.append({
                    'Key': obj['Key'],
                    'Size': obj['Size'],
                    'LastModified': obj['LastModified'],
                    'ETag': obj['ETag'].strip('"'),
                })

            return objects

        except S3_CONNECTION_ERRORS as e:
            message, status, code = map_s3_error(e)
            raise S3Error(f"Failed to list objects: {message}", status, code)

    def ensure_prefix_exists(self, prefix: str) -> None:
        """
        Ensure a 'directory' prefix exists in S3.

        Note: S3 doesn't have real directories, but some tools expect
        zero-byte objects with trailing slashes to represent folders.
        This method creates such a marker if needed.

        Args:
            prefix: The prefix/directory path to ensure exists
        """
        if not prefix:
            return

        # Normalize prefix to end with /
        prefix = prefix.strip('/') + '/'

        # Check if any objects exist with this prefix
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=1,
            )

            if response.get('KeyCount', 0) == 0:
                # Create empty object as directory marker
                self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=prefix,
                    Body=b'',
                )
                logger.debug(f"[S3] Created directory marker '{prefix}'")

        except S3_CONNECTION_ERRORS as e:
            message, status, code = map_s3_error(e)
            raise S3Error(f"Failed to ensure prefix '{prefix}': {message}", status, code)

    def close(self) -> None:
        """
        Close the S3 client connection.

        Note: boto3 clients don't require explicit closing, but this method
        is provided for interface compatibility with FTP/SFTP clients.
        """
        # boto3 clients manage their own connection pooling
        # This is a no-op but maintains interface compatibility
        logger.debug(f"[S3] Closed client for bucket '{self.bucket_name}'")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
