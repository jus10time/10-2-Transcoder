# Field Ingest Engine

A native macOS application for transcoding ARRI camera footage (MXF/MOV) into DNxHD files for Avid compatibility. Designed for easy use in the field on external drives.

## Installation

### Prerequisites

1. **FFmpeg:** Required for video transcoding. Install via [Homebrew](https://brew.sh/):
   ```bash
   brew install ffmpeg
   ```

2. **ARRI Reference Tool (ART):** For color science processing.
   - Download from the [official ARRI website](https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool)
   - Note the installation path for `art-cmd`

### Install the App

1. Copy `Field Ingest Engine.app` from `dist/` to your Applications folder
2. On first launch, right-click → Open (to bypass Gatekeeper for unsigned apps)

## How to Use

### 1. Select Source Folder

- Launch the app and click **Browse Folder**
- Select the folder containing your camera footage (e.g., `B_0001_1DZI` on a camera card)
- The app will show where output folders will be created (drive root)

### 2. Start Processing

- Click **Start Processing**
- Output folders are created at the drive root:
  ```
  /Volumes/YourDrive/
  ├── 02_OUTPUT/        (Transcoded DNxHD files)
  ├── 03_ERROR/         (Failed files, if any)
  └── _internal/        (Logs and status files)
  ```
- Source files remain untouched in their original location

### 3. Monitor Progress

- **Status Indicator:** Pulsing green = processing, amber = paused
- **Progress Bar:** Shows current file progress
- **Queue Panel:** Files waiting to be processed
- **Completed Panel:** Processed files with success (✓) or failure (✗)
- **Log Panel:** Real-time processing log (collapsible)

### 4. Stop and Generate Report

- Click **Back** or close the app when done
- A PDF report is generated in `02_OUTPUT/` with all processed files

## Features

- **Native macOS App:** Built with py2app, handles external drive permissions
- **Dark "Matrix" Theme:** Professional dark UI with green accents
- **Process in Place:** Source files stay untouched on camera cards
- **Alphabetical Processing:** Files process in order (C001, C002, C003...)
- **Pause/Resume:** Safely pause between files for transport
- **Session-Based Tracking:** Completed list and reports only show current session
- **Auto PDF Reports:** Report automatically generated when all files complete

## Building from Source

### Setup Development Environment

```bash
cd field_ingest_app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run in Development Mode

```bash
source venv/bin/activate
python run_gui.py
```

### Build Native App

```bash
source venv/bin/activate
python setup.py py2app
```

The built app will be in `dist/Field Ingest Engine.app`

## Configuration

Edit `config.ini` to customize:
- `art_cli` - Path to ARRI Reference Tool
- `ffmpeg_path` - Path to FFmpeg (default: system path)
- Processing extensions and output settings

## Technical Notes

- Uses FileHelper.app for external drive permissions
- Lock files stored in `/tmp/` for bundled app compatibility
- Files sorted alphabetically before queuing
- Stabilization check ensures files are fully copied before processing
- 10-second idle cooldown before declaring session complete (prevents false triggers)

## Recent Updates (February 2026)

- **Session-based tracking:** Completed list only shows files from current session
- **Auto PDF generation:** Report generated automatically when all files finish
- **Completion cooldown:** 10-second delay prevents premature completion detection
- **Full FFmpeg path:** Config now uses absolute path for bundled app compatibility

---

**Version:** 2.1.0
**Last Updated:** February 2026
**Platform:** macOS (Apple Silicon and Intel)
