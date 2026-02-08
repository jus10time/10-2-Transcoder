# Transcoder v2 - Deployment Guide

**Last Updated**: January 2026
**Current Mac Studio IP**: 192.168.99.171
**Dev Server**: productiondev.am.com (192.168.100.214)

---

## Complete System Overview

This system processes ARRI camera footage through an automated pipeline:

1. Drop MXF/MOV files into a watch folder (on QNAP NAS)
2. Engine detects files, applies ARRI color science, transcodes to DNxHD
3. Web dashboard monitors progress from any browser

---

## Component Locations

### Mac Studio (Processing Machine)

| Item | Path |
|------|------|
| Transcoder | `~/ingest_engine_v2/` |
| Start Script | `~/start_ingest_engine.sh` |
| Stop Script | `~/stop_ingest_engine.sh` |
| Logs | `~/ingest_engine_v2/logs/ingest_engine.log` |
| Config | `~/ingest_engine_v2/config.ini` |
| API Port | 8080 |

### Dev Server (Web Dashboard)

| Item | Path |
|------|------|
| Web App | `~/ingest-engine-web/` |
| Docker Files | `~/ingest-engine-web/docker/` |
| Frontend Port | 3030 |
| Backend Port | 3031 |

### QNAP Storage

| Folder | Path |
|--------|------|
| Watch | `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/watch_folder` |
| Processing | `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/processing_folder` |
| Output | `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/output_folder` |
| Processed | `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/processed_folder` |
| Temp | `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/temp_folder` |
| Error | `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/error_folder` |

---

## Deploy to New Mac Studio

### Step 1: Prepare Current Mac

```bash
# Stop the engine
~/stop_ingest_engine.sh

# Verify it's stopped
pgrep -af "python.*main.py"
```

### Step 2: Copy to New Mac

```bash
# From current Mac (or use file sharing)
scp -r ~/ingest_engine_v2 username@newmac:~/
scp ~/start_ingest_engine.sh username@newmac:~/
scp ~/stop_ingest_engine.sh username@newmac:~/
```

### Step 3: Setup New Mac

On the new Mac:

```bash
cd ~/ingest_engine_v2
./setup_new_machine.sh
```

The script will:
- Check for Python 3, FFmpeg
- Install watchdog Python package
- Verify/create storage folders
- Create start/stop scripts
- Test the API

### Step 4: Manual Checks on New Mac

1. **Install ARRI Reference Tool** (if not installed):
   - Download from [ARRI website](https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool)
   - Install to `/Applications/`
   - Verify path in `config.ini` matches

2. **Install FFmpeg** (if not installed):
   ```bash
   brew install ffmpeg
   ```

3. **Verify QNAP Mount**:
   ```bash
   ls /Volumes/QNAP1_RAW_Footage/ingest_engine_v2/
   ```
   If not mounted, connect via Finder → Go → Connect to Server

4. **Get New Mac's IP**:
   ```bash
   ipconfig getifaddr en0
   ```

### Step 5: Start Engine on New Mac

```bash
~/start_ingest_engine.sh

# Verify it's running
curl http://localhost:8080/api/health
```

### Step 6: Update Dev Server

SSH to the dev server:
```bash
ssh justin@productiondev.am.com
# Password: McQu33n!
```

Update the docker-compose.yml:
```bash
cd ~/ingest-engine-web/docker
nano docker-compose.yml
```

Change this line to the new Mac's IP:
```yaml
- INGEST_ENGINE_API=http://NEW_MAC_IP:8080
```

Rebuild and restart:
```bash
docker-compose down
docker-compose up -d --build
```

Verify:
```bash
curl http://localhost:3031/api/health
```

### Step 7: Test Dashboard

Open browser: http://productiondev.am.com:3030

Should show "ARRI35 Footage Dashboard" connected to the new Mac.

---

## Credentials & Access

### Dev Server SSH
- Host: `productiondev.am.com` (192.168.100.214)
- User: `justin`
- Password: `McQu33n!`

### Portainer (Docker Management)
- URL: https://productiondev.am.com:9443
- (Check with team for credentials)

---

## Daily Operations

### Start Processing
```bash
~/start_ingest_engine.sh
```

### Stop Processing
```bash
~/stop_ingest_engine.sh
```

### Check Status
```bash
# Via API
curl http://localhost:8080/api/status

# Via logs
tail -f ~/ingest_engine_v2/logs/ingest_engine.log
```

### View Dashboard
Open: http://productiondev.am.com:3030

---

## Common Issues & Fixes

### "Address already in use" on startup
```bash
lsof -ti:8080 | xargs kill -9
~/start_ingest_engine.sh
```

### File stuck in queue (won't process)
Restart clears the deduplication cache:
```bash
~/stop_ingest_engine.sh
~/start_ingest_engine.sh
```

### Multiple engine instances
```bash
pkill -9 -f "python.*main.py"
rm -f ~/ingest_engine_v2/.ingest_engine.lock
~/start_ingest_engine.sh
```

### Web dashboard shows "Cannot connect to ingest engine"
1. Check engine is running: `pgrep -af "python.*main.py"`
2. Check API responds: `curl http://localhost:8080/api/health`
3. Check firewall allows port 8080 from dev server
4. Verify IP in docker-compose.yml matches Mac's current IP

### QNAP not mounted
```bash
# Check mount
ls /Volumes/QNAP1_RAW_Footage/

# If not there, mount via Finder:
# Go → Connect to Server → smb://QNAP_IP/QNAP1_RAW_Footage
```

---

## Web Dashboard Themes

Click the palette icon (top right) to switch themes:
- **Midnight** - Dark indigo (default)
- **Ocean** - Dark blue/cyan
- **Forest** - Dark green
- **Sunset** - Dark orange
- **Light** - Light mode
- **Rose** - Dark pink

Theme preference is saved to browser localStorage.

---

## Files Reference

### Mac Studio
```
~/ingest_engine_v2/
├── main.py              # Main engine script
├── processor.py         # Processing pipeline
├── api_server.py        # HTTP API
├── config.ini           # Configuration
├── setup_new_machine.sh # Setup script
├── README.md            # Documentation
├── DEPLOYMENT_GUIDE.md  # This file
├── logs/
│   └── ingest_engine.log
├── status.json          # Current status (auto-generated)
└── history.json         # History (auto-generated)

~/start_ingest_engine.sh  # Start script
~/stop_ingest_engine.sh   # Stop script
```

### Dev Server
```
~/ingest-engine-web/
├── backend/
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   └── App.js       # Main dashboard component
│   └── package.json
└── docker/
    ├── docker-compose.yml
    ├── Dockerfile.frontend
    └── Dockerfile.backend
```

---

## Support

For issues, check:
1. Engine logs: `tail -100 ~/ingest_engine_v2/logs/ingest_engine.log`
2. Docker logs: `docker logs ingest-engine-backend`
3. API health: `curl http://MAC_IP:8080/api/health`
