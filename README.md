# ADL S3 Plugin

Plugin for dispatching observation data from ADL to S3-compatible storage services, including:

- **AWS S3** - Amazon's Simple Storage Service
- **MinIO** - Self-hosted S3-compatible object storage
- **Other S3-compatible services** - DigitalOcean Spaces, Backblaze B2, Wasabi, etc.

## Features

- **Parallel uploads** using ThreadPoolExecutor for fast bulk transfers
- **Two write modes**:
    - `append` - Merge records into daily files per station
    - `new_file` - Create a separate file for each observation
- **Automatic directory structure** - Organizes files by station ID
- **Configurable CSV output** - Control headers, timestamps, and columns
- **SSL/TLS support** with optional certificate verification for development environments

## Installation

### Prerequisites

- Docker and Docker Compose installed on your machine
- Git installed on your machine
- ADL core system built and available

### Install the ADL S3 Plugin

1. Clone the plugin repository:

```bash
git clone https://github.com/wmo-raf/adl-s3-plugin.git
cd adl-s3-plugin
```

2. Create a `.env` file:

```bash
cp .env.sample .env
```

3. Edit the `.env` file with your settings:

```bash
nano .env
```

4. Build and start:

```bash
docker compose build
docker compose up
```

## Configuration

### S3 Upload (AWS S3)

For AWS S3, configure:

| Field       | Description                    |
|-------------|--------------------------------|
| Bucket Name | Your S3 bucket name            |
| Access Key  | AWS Access Key ID              |
| Secret Key  | AWS Secret Access Key          |
| Region      | AWS region (e.g., `us-east-1`) |
| Prefix      | Object prefix/folder path      |

Leave **Endpoint URL** blank for AWS S3.

### MinIO Upload

For MinIO, configure:

| Field        | Description                                      |
|--------------|--------------------------------------------------|
| Endpoint URL | MinIO server URL (e.g., `http://localhost:9000`) |
| Bucket Name  | Your MinIO bucket name                           |
| Access Key   | MinIO access key                                 |
| Secret Key   | MinIO secret key                                 |
| Use SSL      | Enable for HTTPS endpoints                       |
| Verify SSL   | Disable for self-signed certificates             |

## Write Modes

### Append Mode (Default)

Groups observations by station and day. Creates/updates files named:

```
{prefix}/{station_id}/WIGOS_{station_id}_YYYYMMDD.csv
```

Records are merged with existing files, avoiding duplicates based on timestamp.

### New File Mode

Creates a separate file for each observation:

```
{prefix}/{station_id}/WIGOS_{station_id}_YYYYMMDDTHHmmss.csv
```

## Performance Tuning

The **Max Upload Workers** setting controls concurrent uploads:

- **Default (10)**: Good balance for most use cases
- **Higher (20-50)**: Faster for many small files, more memory/CPU usage
- **Lower (1-5)**: Gentler on resources, better for constrained environments

## Output Format

CSV files contain:

```csv
station_id,wigos_id,date,time,temperature,humidity,...
STN001,0-123-456-789,2024-01-15,14:30:00,25.5,65,...
```

Or with single timestamp column (`use_single_timestamp=True`):

```csv
station_id,wigos_id,timestamp,temperature,humidity,...
STN001,0-123-456-789,20240115T143000,25.5,65,...
```

## Development

### Testing with MinIO

```bash
# Start MinIO container
docker run -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
```

Then configure MinIO Upload with:

- Endpoint URL: `http://localhost:9000`
- Access Key: `minioadmin`
- Secret Key: `minioadmin`

## License

MIT License - see LICENSE file for details.
