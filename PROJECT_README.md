# Project: Transcoder

## Version 2.1: Command-Line Application

**Status:** Complete & Tested

### Summary
This project is a complete, working Python application (`ingest_engine_v2`) that provides an automated transcoding workflow.

**Core Features:**
- **Network-Aware:** Monitors a `watch_folder` on a QNAP network share.
- **Automated Transcoding:** Uses the ARRI Reference Tool (ART) CLI to bake in looks and FFmpeg to transcode 4K source files into HD DNxHD media for Avid.
- **Robust:** Includes real-time console progress monitoring for FFmpeg and quarantines any failed files into an `error_folder`.
- **Configurable:** All paths and settings are managed via the `config.ini` file.

### Key Locations
- **Application Code:** `/Users/admin/ingest_engine_v2/` (on the local Mac Studio)
- **Media & Log Folders:** `/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/` (on the QNAP server)

---

## Next Steps: Version 3.0 Web Interface

**Status:** Planning Phase

**Goal:** To build a web-based front-end for meticulously monitoring the Transcoder, especially for high-volume batches.

---

## Roadmap Notes: Multi-Format Output

**Status:** Planning Phase

**Context:** This started as an ARRI Alexa 35 → DNxHD tool, but it is evolving into a full mixed‑camera transcoder.

**Future Capability Goals:**
- Add additional output targets beyond DNxHD (e.g., ProRes, H.264/H.265 proxies).
- Allow per‑job output presets (codec, resolution, bitrate, audio settings).
- Maintain a small, curated preset library for common deliverables.
- Keep DNxHD as the default until new presets are validated.

### Proposed Architecture
1.  **Backend API (Python/FastAPI):** A new, lightweight web server that will run locally. It will provide API endpoints to get the status of the engine, list files in the network folders, and read the log files. It will communicate with the ingest engine via a shared `status.json` file for real-time progress updates.
2.  **Frontend (React):** A modern, single-page web application that communicates with the backend API to display a live dashboard. The dashboard will show the contents of each folder, the live log, and the progress of any currently processing file.

### Action Item for Next Session
- Begin development of the **Version 3.0 web application**.
- The first step will be to set up the new project structure for the FastAPI backend and React frontend.
