"""
S3 Dispatcher for ADL.

Handles preparing observation data as CSV files and uploading them to S3-compatible storage.
Supports both single-file-per-record and append-to-daily-file modes.
Uses ThreadPoolExecutor for parallel uploads.
"""
import csv
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from io import StringIO, BytesIO
from typing import List, Dict, Tuple, Optional

import pandas as pd

from ..s3 import S3Error

logger = logging.getLogger(__name__)

# Default number of concurrent upload threads
DEFAULT_MAX_WORKERS = 10
PARALLEL_THRESHOLD = 3


class S3FileError(Exception):
    """Error during S3 file preparation or upload"""
    pass


def validate_csv_header(columns, expected):
    """Validate that CSV contains expected columns."""
    if not all(col in columns for col in expected):
        raise S3FileError("CSV file does not match the expected header format.")


def make_aware_timestamp(ts, timezone):
    """Convert timestamp to timezone-aware datetime."""
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize(timezone)
        else:
            ts = ts.tz_convert(timezone)
        return ts.to_pydatetime()
    elif isinstance(ts, datetime):
        if ts.tzinfo is None:
            return timezone.localize(ts)
        return ts.astimezone(timezone)
    else:
        raise TypeError("Unsupported timestamp type")


def build_timestamp(date_series, time_series):
    """Build timestamp from separate date and time series."""
    try:
        dt_series = pd.to_datetime(date_series + ' ' + time_series, errors="coerce")
        if dt_series.isnull().any():
            raise ValueError("Invalid or missing datetime")
        return dt_series
    except Exception as e:
        raise S3FileError(f"Error processing timestamp: {e}")


def extract_values(record, keys, from_root=True):
    """Extract values for given keys from a record."""
    source = record if from_root else record.get("values", {})
    return {key: source.get(key) for key in keys}


@dataclass
class ObsRecord:
    """Represents a single observation record."""
    station_id: str
    wigos_id: Optional[str]
    timestamp: pd.Timestamp
    values: dict
    
    def to_row(self, header: List[str], timezone="UTC") -> List[str]:
        """Convert record to a CSV row."""
        dt = self.timestamp.astimezone(timezone)
        base = {
            "station_id": self.station_id,
            **self.values
        }
        
        if "wigos_id" in header:
            base["wigos_id"] = self.wigos_id or ""
        
        if "timestamp" in header:
            base["timestamp"] = dt.strftime("%Y%m%dT%H%M%S")
        else:
            base["date"] = dt.strftime("%Y-%m-%d")
            base["time"] = dt.strftime("%H:%M:%S")
        
        return [base.get(col, "") for col in header]


def csv_to_records(csv_content: bytes, csv_header: List[str], value_columns: List[str],
                   timezone="UTC") -> List[ObsRecord]:
    """Parse CSV content into ObsRecord objects."""
    df = pd.read_csv(BytesIO(csv_content))
    validate_csv_header(df.columns, csv_header)
    df["timestamp"] = build_timestamp(df["date"].astype(str), df["time"].astype(str))
    df = df.sort_values(by=["timestamp"])
    
    return [
        ObsRecord(
            station_id=row.get("station_id"),
            wigos_id=row.get("wigos_id"),
            timestamp=make_aware_timestamp(row.get("timestamp"), timezone),
            values=extract_values(row, value_columns)
        ) for row in df.to_dict(orient="records")
    ]


def group_records_by_station_day(records: List[ObsRecord]) -> Dict:
    """Group records by station ID and day."""
    grouped = {}
    for record in records:
        sid = record.wigos_id or record.station_id
        day = record.timestamp.strftime("%Y-%m-%d")
        grouped.setdefault(sid, {}).setdefault(day, {})[record.timestamp] = record
    return grouped


def create_csv_file(records: List[ObsRecord], header: List[str], timezone,
                    include_header: bool = True) -> BytesIO:
    """Create a CSV file from records."""
    output = StringIO()
    writer = csv.writer(output)
    
    if include_header:
        writer.writerow(header)
    
    for record in records:
        writer.writerow(record.to_row(header, timezone))
    
    return BytesIO(output.getvalue().encode("utf-8"))


def has_valid_data(values: dict) -> bool:
    """Check if the record has at least one non-empty, non-None channel parameter value."""
    return any(
        value is not None and
        str(value).strip() != ""
        for value in values.values()
    )


def build_csv_header(channel_params: List[str], include_wigos_id: bool = True,
                     use_single_timestamp: bool = False) -> List[str]:
    """Build CSV header based on configuration."""
    csv_header = ["station_id"]
    if include_wigos_id:
        csv_header.append("wigos_id")
    
    if use_single_timestamp:
        csv_header.append("timestamp")
    else:
        csv_header.extend(["date", "time"])
    
    csv_header.extend(channel_params)
    return csv_header


def prepare_csv_files_new_mode(
        data_records: List[Dict],
        channel,
        create_station_dir: bool = True,
        include_wigos_id: bool = True,
        use_single_timestamp: bool = False,
        include_header: bool = True
) -> List[Tuple[BytesIO, str]]:
    """
    Prepare CSV files for 'new_file' write mode.
    Each record becomes a separate CSV file.
    """
    timezone = channel.timezone
    channel_params = channel.get_parameter_mapping_values()
    csv_header = build_csv_header(channel_params, include_wigos_id, use_single_timestamp)
    
    csv_files = []
    skipped_records = 0
    
    for data in data_records:
        values = extract_values(data, channel_params, from_root=False)
        
        if not has_valid_data(values):
            skipped_records += 1
            logger.debug(
                f"[S3 Prepare] Skipping record for station {data.get('station_id')} - no valid values")
            continue
        
        record = ObsRecord(
            station_id=data.get("station_id"),
            wigos_id=data.get("wigos_id"),
            timestamp=make_aware_timestamp(data.get("timestamp"), timezone),
            values=values
        )
        
        csv_file = create_csv_file([record], csv_header, timezone, include_header)
        
        id_for_path = record.wigos_id or record.station_id
        filename = f"WIGOS_{id_for_path}_{record.timestamp.strftime('%Y%m%dT%H%M%S')}.csv"
        
        if create_station_dir:
            remote_path = f"{channel.prefix}/{id_for_path}/{filename}"
        else:
            remote_path = f"{channel.prefix}/{filename}"
        
        csv_files.append((csv_file, remote_path))
    
    if skipped_records > 0:
        logger.info(f"[S3 Prepare] Skipped {skipped_records} records with no valid values")
    
    return csv_files


def prepare_csv_files_append_mode(
        data_records: List[Dict],
        channel,
        client,
        create_station_dir: bool = True,
        include_wigos_id: bool = True,
        use_single_timestamp: bool = False,
        include_header: bool = True
) -> List[Tuple[BytesIO, str]]:
    """
    Prepare CSV files for 'append' write mode.
    Groups records by station and day, merging with existing files if they exist.
    """
    timezone = channel.timezone
    channel_params = channel.get_parameter_mapping_values()
    csv_header = build_csv_header(channel_params, include_wigos_id, use_single_timestamp)
    
    # Filter and convert records
    valid_records = []
    skipped_records = 0
    
    for d in data_records:
        values = extract_values(d, channel_params, from_root=False)
        
        if not has_valid_data(values):
            skipped_records += 1
            logger.debug(
                f"[S3 Prepare] Skipping record for station {d.get('station_id')} - no valid values")
            continue
        
        valid_records.append(ObsRecord(
            station_id=d.get("station_id"),
            wigos_id=d.get("wigos_id"),
            timestamp=make_aware_timestamp(d.get("timestamp"), timezone),
            values=values
        ))
    
    if skipped_records > 0:
        logger.info(f"[S3 Prepare] Skipped {skipped_records} records with no valid values")
    
    if not valid_records:
        logger.info("[S3 Prepare] No valid records to process after filtering")
        return []
    
    grouped = group_records_by_station_day(valid_records)
    
    csv_files = []
    
    for station_id, days in grouped.items():
        for day, incoming_records in days.items():
            filename = f"WIGOS_{station_id}_{day.replace('-', '')}.csv"
            if create_station_dir:
                remote_path = f"{channel.prefix}/{station_id}/{filename}"
            else:
                remote_path = f"{channel.prefix}/{filename}"
            
            final_records = {}
            
            try:
                logger.debug(f"[S3 Prepare] Checking for existing file at '{remote_path}'")
                existing_csv = client.get(remote_path)
                
                logger.debug(f"[S3 Prepare] Found existing file at '{remote_path}'")
                
                existing_records = csv_to_records(existing_csv, csv_header, channel_params, timezone)
                existing_timestamps = {r.timestamp for r in existing_records}
                
                logger.debug(f"[S3 Prepare] Checking for new records to append for '{remote_path}'")
                
                new_records = []
                for ts, record in incoming_records.items():
                    if ts not in existing_timestamps:
                        new_records.append(record)
                
                if not new_records:
                    logger.debug(f"[S3 Prepare] No new records to append for '{remote_path}'. Skipping..")
                    continue
                
                logger.debug(f"[S3 Prepare] Found {len(new_records)} new records. Appending...")
                
                final_records = {r.timestamp: r for r in existing_records}
            except S3Error as e:
                logger.debug(
                    f"[S3 Prepare] No existing file for '{remote_path}' or error accessing it: {e}. Creating new.")
            
            final_records.update(incoming_records)
            
            final_records_list = list(final_records.values())
            csv_file = create_csv_file(final_records_list, csv_header, timezone, include_header)
            csv_files.append((csv_file, remote_path))
    
    return csv_files


def upload_single_file(client, csv_file: BytesIO, remote_path: str) -> str:
    """
    Upload a single CSV file to S3.
    
    Returns the remote_path on success for logging purposes.
    Raises S3Error on failure.
    """
    logger.debug(f"[S3 Upload] Uploading file to '{remote_path}'")
    client.put(csv_file, remote_path)
    return remote_path


def dispatch_to_s3(
        channel,
        data_records: List[Dict],
        create_station_dir: bool = True,
        include_wigos_id: bool = True,
        use_single_timestamp: bool = False,
        include_header: bool = True,
        max_workers: int = DEFAULT_MAX_WORKERS
):
    """
    Main dispatch function that coordinates CSV preparation and S3 upload.
    Uses ThreadPoolExecutor for parallel uploads.

    Args:
        channel: Channel configuration object with S3 settings
        data_records: List of data records to process
        create_station_dir: Whether to create station directories (prefixes)
        include_wigos_id: Whether to include wigos_id column in CSV files
        use_single_timestamp: If True, use single 'timestamp' column in UTC ISO format.
                             If False, use separate 'date' and 'time' columns.
        include_header: Whether to include the header row in CSV files
        max_workers: Maximum number of concurrent upload threads (default: 10)

    Returns:
        Tuple of (uploaded_count, last_sent_obs_time)
    """
    client = channel.get_client()
    write_mode = channel.write_mode
    last_sent_obs_time = None
    
    logger.debug(f"[S3 Dispatch] Using S3 connection for {channel.name}")
    logger.debug(f"[S3 Dispatch] Bucket: {channel.bucket_name}, Prefix: {channel.prefix}")
    
    try:
        # Prepare CSV files based on write mode
        if write_mode == "new_file":
            logger.debug(f"[S3 Dispatch] Creating new files for {len(data_records)} records")
            csv_files = prepare_csv_files_new_mode(
                data_records, channel, create_station_dir,
                include_wigos_id, use_single_timestamp, include_header
            )
        elif write_mode == "append":
            logger.debug(f"[S3 Dispatch] Appending records to existing files")
            csv_files = prepare_csv_files_append_mode(
                data_records, channel, client, create_station_dir,
                include_wigos_id, use_single_timestamp, include_header
            )
        else:
            raise ValueError(f"Unknown write mode: {write_mode}")
        
        if not csv_files:
            logger.info("[S3 Dispatch] No files to upload")
            return 0, None
        
        # Upload files in parallel using ThreadPoolExecutor
        logger.info(f"[S3 Dispatch] Uploading {len(csv_files)} files with {max_workers} workers")
        
        if len(csv_files) <= PARALLEL_THRESHOLD:
            uploaded, failed = _upload_sequential(client, csv_files)
        else:
            uploaded, failed = _upload_parallel(client, csv_files, max_workers)
        
        # Get the last timestamp from the original records
        if data_records:
            timestamps = [make_aware_timestamp(d.get("timestamp"), channel.timezone) for d in data_records]
            last_sent_obs_time = max(timestamps)
    
    finally:
        client.close()
    
    logger.info(f"[S3 Dispatch] Uploaded {uploaded} files to {channel.name} (failed: {failed})")
    return uploaded, last_sent_obs_time


def _upload_sequential(client, csv_files):
    """Upload files one at a time."""
    uploaded, failed = 0, 0
    for csv_file, remote_path in csv_files:
        try:
            upload_single_file(client, csv_file, remote_path)
            uploaded += 1
        except S3Error as e:
            failed += 1
            logger.error(f"[S3 Dispatch] Failed: {remote_path}: {e}")
    return uploaded, failed


def _upload_parallel(client, csv_files, max_workers):
    """Upload files in parallel using threads."""
    uploaded, failed = 0, 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(upload_single_file, client, f, p): p
            for f, p in csv_files
        }
        for future in as_completed(futures):
            try:
                future.result()
                uploaded += 1
            except S3Error as e:
                failed += 1
                logger.error(f"[S3 Dispatch] Failed: {futures[future]}: {e}")
    return uploaded, failed
