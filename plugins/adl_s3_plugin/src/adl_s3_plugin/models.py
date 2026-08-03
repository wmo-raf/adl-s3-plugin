"""
ADL S3 Plugin Models.

Provides S3Upload dispatch channel for pushing observation data to S3-compatible storage.
"""
import logging
import time

from adl.core.models import DispatchChannel
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from timezone_field import TimeZoneField
from wagtail.admin.panels import MultiFieldPanel, FieldPanel

from .dispatchers.s3 import dispatch_to_s3
from .s3 import S3Client, S3Error

logger = logging.getLogger(__name__)


class BaseS3Upload(models.Model):
    """
    Base model for S3-compatible storage upload configuration.
    
    Works with AWS S3, MinIO, and other S3-compatible services.
    """
    
    WRITE_MODES = (
        ("append", _("Append record to single daily file")),
        ("new_file", _("Create a new file for each record")),
    )
    
    # Connection settings
    endpoint_url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Endpoint URL"),
        help_text=_("Custom endpoint URL for MinIO or S3-compatible services. "
                    "Leave blank for AWS S3.")
    )
    bucket_name = models.CharField(
        max_length=255,
        verbose_name=_("Bucket Name"),
        help_text=_("Name of the S3 bucket to upload files to")
    )
    access_key = models.CharField(
        max_length=255,
        verbose_name=_("Access Key"),
        help_text=_("AWS Access Key ID or MinIO access key")
    )
    secret_key = models.CharField(
        max_length=255,
        verbose_name=_("Secret Key"),
        help_text=_("AWS Secret Access Key or MinIO secret key")
    )
    region = models.CharField(
        max_length=50,
        default="us-east-1",
        verbose_name=_("Region"),
        help_text=_("AWS region (e.g., us-east-1, eu-west-1). "
                    "For MinIO, this can usually be left as default.")
    )
    
    # SSL settings
    use_ssl = models.BooleanField(
        default=True,
        verbose_name=_("Use SSL/TLS"),
        help_text=_("Whether to use SSL/TLS for connections")
    )
    verify_ssl = models.BooleanField(
        default=True,
        verbose_name=_("Verify SSL Certificate"),
        help_text=_("Whether to verify SSL certificates. "
                    "Disable for self-signed certificates in development.")
    )
    
    # Storage path settings
    prefix = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_("Object Prefix"),
        help_text=_("Prefix (folder path) for uploaded objects. "
                    "E.g., 'observations/weather' will create objects like "
                    "'observations/weather/station_id/file.csv'")
    )
    
    # Output settings
    timezone = TimeZoneField(
        default='UTC',
        verbose_name=_("Timezone for output dates"),
        help_text=_("Timezone to use for file dates. UTC highly recommended")
    )
    write_mode = models.CharField(
        max_length=20,
        choices=WRITE_MODES,
        default="append",
        verbose_name=_("Write Mode"),
        help_text=_("'Append' merges records into daily files. "
                    "'New file' creates a separate file for each observation.")
    )
    use_single_timestamp = models.BooleanField(
        default=True,
        verbose_name=_("Use Single Timestamp column"),
        help_text=_(
            "Whether to use a single timestamp column for observation times in the CSV output.If disabled, "
            "separate date and time columns will be used."
        )
    )
    
    # Performance settings
    max_upload_workers = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Max Upload Workers"),
        help_text=_("Maximum number of concurrent upload threads. "
                    "Higher values = faster uploads but more resource usage.")
    )
    
    class Meta:
        abstract = True
    
    def clean(self):
        """Validate S3 connection settings."""
        super().clean()
        
        if not self.bucket_name:
            raise ValidationError({
                'bucket_name': _("Bucket name is required")
            })
        
        if not self.access_key:
            raise ValidationError({
                'access_key': _("Access key is required")
            })
        
        if not self.secret_key:
            raise ValidationError({
                'secret_key': _("Secret key is required")
            })
        
        # Validate endpoint URL format if provided
        if self.endpoint_url:
            if not (self.endpoint_url.startswith('http://') or
                    self.endpoint_url.startswith('https://')):
                raise ValidationError({
                    'endpoint_url': _("Endpoint URL must start with http:// or https://")
                })
    
    @property
    def connection_details(self):
        """Get connection details for the S3 client."""
        details = {
            "bucket_name": self.bucket_name,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "region": self.region,
            "use_ssl": self.use_ssl,
            "verify_ssl": self.verify_ssl,
        }
        
        if self.endpoint_url:
            details["endpoint_url"] = self.endpoint_url
        
        return details
    
    def get_client(self):
        """Get S3 client instance."""
        return S3Client(**self.connection_details)
    
    def test_connection(self):
        """
        Probe the bucket: reachability, authentication, and permission to
        list under the configured prefix.

        Returns the shape ``DispatchChannel.test_connection`` documents —
        a dict with ``ok``, ``supported``, ``message`` and ``latency_ms``.
        """
        start = time.monotonic()

        def latency_ms():
            return int((time.monotonic() - start) * 1000)

        try:
            client = self.get_client()
            # Try listing objects to verify access
            client.list_objects(prefix=self.prefix, max_keys=1)
            client.close()
        except S3Error as e:
            return {
                "ok": False,
                "supported": True,
                "message": str(e),
                "latency_ms": latency_ms(),
            }
        except Exception as e:
            return {
                "ok": False,
                "supported": True,
                "message": _("Unexpected error: %(error)s") % {'error': str(e)},
                "latency_ms": latency_ms(),
            }

        return {
            "ok": True,
            "supported": True,
            "message": _("Connection successful"),
            "latency_ms": latency_ms(),
        }


class S3Upload(BaseS3Upload, DispatchChannel):
    """
    S3-compatible storage dispatch channel.
    
    Uploads observation data as CSV files to AWS S3, MinIO,
    or other S3-compatible storage services.
    """
    
    panels = DispatchChannel.base_panels + [
        FieldPanel("timezone"),
        MultiFieldPanel([
            FieldPanel("endpoint_url"),
            FieldPanel("bucket_name"),
            FieldPanel("access_key"),
            FieldPanel("secret_key"),
            FieldPanel("region"),
        ], heading=_("S3 Connection")),
        MultiFieldPanel([
            FieldPanel("use_ssl"),
            FieldPanel("verify_ssl"),
        ], heading=_("Security Settings")),
        MultiFieldPanel([
            FieldPanel("prefix"),
            FieldPanel("write_mode"),
            FieldPanel("use_single_timestamp"),
            FieldPanel("max_upload_workers"),
        ], heading=_("Storage Settings")),
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("S3 Upload")
        verbose_name_plural = _("S3 Uploads")
    
    def send_station_data(self, station_link, station_data_records):
        """
        Send station data to S3.
        
        Args:
            station_link: StationLink instance
            station_data_records: List of observation records to upload
            
        Returns:
            Tuple of (uploaded_count, last_sent_obs_time)
        """
        return dispatch_to_s3(
            self,
            station_data_records,
            max_workers=self.max_upload_workers,
            use_single_timestamp=self.use_single_timestamp
        )
    
    class MinIOUpload(BaseS3Upload, DispatchChannel):
        """
        MinIO-specific dispatch channel with sensible defaults for MinIO deployments.
        
        This is essentially the same as S3Upload but with defaults better suited
        for typical MinIO deployments (e.g., SSL verification disabled by default
        for development environments).
        """
        
        # Override defaults for MinIO
        use_ssl = models.BooleanField(
            default=False,
            verbose_name=_("Use SSL/TLS"),
            help_text=_("Whether to use SSL/TLS for connections. "
                        "Often disabled for local MinIO deployments.")
        )
        verify_ssl = models.BooleanField(
            default=False,
            verbose_name=_("Verify SSL Certificate"),
            help_text=_("Whether to verify SSL certificates. "
                        "Usually disabled for MinIO with self-signed certs.")
        )
        region = models.CharField(
            max_length=50,
            default="us-east-1",
            blank=True,
            verbose_name=_("Region"),
            help_text=_("MinIO region. Can usually be left as default.")
        )
        
        panels = DispatchChannel.base_panels + [
            FieldPanel("timezone"),
            MultiFieldPanel([
                FieldPanel("endpoint_url"),
                FieldPanel("bucket_name"),
                FieldPanel("access_key"),
                FieldPanel("secret_key"),
            ], heading=_("MinIO Connection")),
            MultiFieldPanel([
                FieldPanel("use_ssl"),
                FieldPanel("verify_ssl"),
                FieldPanel("region"),
            ], heading=_("Advanced Settings")),
            MultiFieldPanel([
                FieldPanel("prefix"),
                FieldPanel("write_mode"),
                FieldPanel("use_single_timestamp"),
                FieldPanel("max_upload_workers"),
            ], heading=_("Storage Settings")),
        ] + DispatchChannel.parameter_panels
        
        class Meta:
            verbose_name = _("MinIO Upload")
            verbose_name_plural = _("MinIO Uploads")
        
        def clean(self):
            """Validate MinIO connection settings."""
            super().clean()
            
            # MinIO requires an endpoint URL
            if not self.endpoint_url:
                raise ValidationError({
                    'endpoint_url': _("Endpoint URL is required for MinIO. "
                                      "E.g., http://localhost:9000 or https://minio.example.com")
                })
        
        def send_station_data(self, station_link, station_data_records):
            """Send station data to MinIO."""
            return dispatch_to_s3(
                self,
                station_data_records,
                max_workers=self.max_upload_workers,
                use_single_timestamp=self.use_single_timestamp
            )
