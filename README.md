# Ingest Engine v2

Automated video transcoding pipeline for ARRI footage. Watches a folder for new MXF/MOV files, applies ARRI looks using the ARRI Reference Tool, and transcodes to DNxHD for Avid compatibility.

## Architecture Overview

```
[Dev Server - productiondev.am.com]          [Mac Studio - 192.168.99.171]
┌─────────────────────────────────┐          ┌─────────────────────────────┐
│  React Frontend (:3030)         │          │                             │
│         │                       │          │  Ingest Engine              │
│         ▼                       │          │  (main.py + API :8080)      │
│  FastAPI Backend (:3031)  ─────────────►   │         │                   │
│                                 │  HTTP    │         ▼                   │
└─────────────────────────────────┘          │  [QNAP Storage]             │
       Docker Containers                     │  [ARRI Tool]                │
                                             │  [FFmpeg]                   │
                                             └─────────────────────────────┘
```

## Quick Start

### New Machine Setup

1. Copy this entire `ingest_engine_v2` folder to the new Mac:
   ```bash
   scp -r /Users/admin/ingest_engine_v2 newmac:~/
   ```

2. Run the setup script:
   ```bash
   cd ~/ingest_engine_v2
   ./setup_new_machine.sh
   ```

3. Follow the prompts to configure paths and verify dependencies

4. Update the web app on the dev server to point to the new Mac's IP:
   ```bash
   # SSH to dev server
   ssh justin@productiondev.am.com

   # Edit docker-compose.yml
   cd ~/ingest-engine-web/docker
   nano docker-compose.yml
   # Change INGEST_ENGINE_API to new Mac's IP

   # Rebuild and restart
   docker-compose down
   docker-compose up -d --build
   ```

### Starting/Stopping

```bash
# Start the engine
~/start_ingest_engine.sh

# Stop the engine
~/stop_ingest_engine.sh

# View logs
tail -f ~/ingest_engine_v2/logs/ingest_engine.log
```

## Current Deployment

| Component | Location | URL/Port |
|-----------|----------|----------|
| Ingest Engine | Mac Studio (192.168.99.171) | :8080 (API) |
| Web Frontend | productiondev.am.com | :3030 |
| Web Backend | productiondev.am.com | :3031 |
| Dashboard | Browser | http://productiondev.am.com:3030 |

## Requirements

### Software Dependencies

| Software | Version | Installation |
|----------|---------|--------------|
| Python 3 | 3.9+ | Pre-installed on macOS |
| FFmpeg | Latest | `brew install ffmpeg` |
| ARRI Reference Tool | 1.0.0+ | [Download from ARRI](https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool) |

### Python Dependencies

```bash
pip3 install --user watchdog
```

## Configuration

### config.ini

```ini
[Paths]
# Tool paths
art_cli = /Applications/art-cmd_1.0.0_macos_universal/bin/art-cmd
ffmpeg = ffmpeg

# Storage paths (QNAP network share)
watch = /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/watch_folder
processing = /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/processing_folder
output = /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/output_folder
processed = /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/processed_folder
temp = /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/temp_folder
error = /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/error_folder

# Local paths
logs = logs
status_file = status.json
history_file = history.json

[Settings]
art_colorspace = Rec.709/D65/BT.1886

[Processing]
allowed_extensions = .mov,.mxf

[API]
host = 0.0.0.0
port = 8080
```

## Folder Structure

| Folder | Purpose |
|--------|---------|
| `watch` | Drop source files here for processing |
| `processing` | Files currently being processed (auto-managed) |
| `output` | Transcoded DNxHD files appear here |
| `processed` | Source files moved here after successful processing |
| `temp` | Intermediate files during processing |
| `error` | Files that failed processing |

## Processing Pipeline

1. **Detection**: Polls watch folder every 15 seconds for new MXF/MOV files
2. **Stabilization**: Waits for file to finish copying (size stops changing)
3. **Move to Processing**: Immediately moves file to processing folder
4. **ARRI Processing**: Applies embedded look using ARRI Reference Tool → ProRes intermediate
5. **FFmpeg Transcode**: Converts to DNxHD 145Mbps, 1920x1080, PCM audio
6. **Cleanup**: Removes intermediate file, archives source to processed folder

## API Endpoints

The engine exposes an API on port 8080 for remote monitoring:

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | Current processing status |
| `GET /api/history` | Processing history (last 100 jobs) |
| `GET /api/folders/{name}` | List files in watch/processing/output/processed/error |
| `GET /api/logs` | Recent log entries |
| `GET /api/health` | Health check |

## Web Dashboard

The dashboard is titled "ARRI35 Footage Dashboard" and includes:

- **Featured Processing Box**: Large status display at top with progress bar
- **Queue**: Scrollable list of waiting/processing files (handles hundreds of files)
- **Failed**: Files that encountered errors
- **Processing History**: Scrollable table of completed jobs
- **Live Log**: Real-time log viewer
- **Theme Switcher**: 6 themes (Midnight, Ocean, Forest, Sunset, Light, Rose)

## Troubleshooting

### Engine won't start - "Address already in use"
```bash
# Kill process on port 8080
lsof -ti:8080 | xargs kill -9

# Then start
~/start_ingest_engine.sh
```

### Files not being processed (stuck in queue)
The engine uses permanent deduplication - files seen once won't be processed again until restart:
```bash
# Restart to clear deduplication
~/stop_ingest_engine.sh
~/start_ingest_engine.sh
```

### Multiple instances running
```bash
# Check for instances
pgrep -af "python.*main.py"

# Kill all
pkill -9 -f "python.*main.py"

# Clean up and restart
rm -f ~/ingest_engine_v2/.ingest_engine.lock
~/start_ingest_engine.sh
```

### ARRI processing fails - "Clip container not readable"
- File may still be copying - engine waits but large files need more time
- Verify ARRI Reference Tool is installed
- Check the file has an embedded ARRI look

### FFmpeg fails
- Verify FFmpeg is installed: `which ffmpeg`
- Check available disk space in output folder
- Review FFmpeg warnings in logs

## Files

| File | Description |
|------|-------------|
| `main.py` | Main entry point, file detection and queue management |
| `processor.py` | Video processing pipeline |
| `api_server.py` | HTTP API for remote monitoring |
| `config.ini` | Configuration settings |
| `setup_new_machine.sh` | Setup script for new installations |
| `status.json` | Current processing status (auto-generated) |
| `history.json` | Processing history (auto-generated) |

## Web App Deployment (Dev Server)

The web app runs on productiondev.am.com in Docker:

```bash
# Location on dev server
~/ingest-engine-web/

# Docker files
~/ingest-engine-web/docker/
  - docker-compose.yml
  - Dockerfile.frontend
  - Dockerfile.backend

# Rebuild after changes
cd ~/ingest-engine-web/docker
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# View logs
docker logs -f ingest-engine-frontend
docker logs -f ingest-engine-backend
```

### Dev Server Ports (PORT_REGISTRY.md)

| Port | Application |
|------|-------------|
| 3030 | ingest-engine-web frontend |
| 3031 | ingest-engine-web backend |

## Migrating to New Mac Studio

1. **On Current Mac**: Copy the ingest_engine_v2 folder
2. **On New Mac**:
   - Run `./setup_new_machine.sh`
   - Install ARRI Reference Tool
   - Verify QNAP mount path
   - Update config.ini paths if needed
3. **On Dev Server**:
   - Update `INGEST_ENGINE_API` in docker-compose.yml to new Mac's IP
   - Rebuild: `docker-compose down && docker-compose up -d --build`

## Notes

- Engine processes ONE file at a time (sequential, no parallelism)
- Polling interval: 15 seconds
- File stabilization check: waits for file size to stop changing
- Deduplication: same filename won't process twice per session (restart to clear)
- Theme preference saved to browser localStorage
