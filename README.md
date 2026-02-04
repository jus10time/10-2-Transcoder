# Field Ingest Engine

This application provides a standalone graphical user interface (GUI) to transcode ARRI camera footage (MXF/MOV) into DNxHD files for Avid compatibility. It is designed for easy use in the field on a macOS laptop.

## First-Time Setup

Before you can run the application, you need to install its dependencies.

### 1. Install FFmpeg & ARRI Reference Tool

-   **FFmpeg:** This is critical for video transcoding. The easiest way to install it on macOS is with [Homebrew](https://brew.sh/):
    ```bash
    brew install ffmpeg
    ```
-   **ARRI Reference Tool (ART):** The application uses ART to apply color science.
    1.  Download and install the tool from the [official ARRI website](https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool).
    2.  Open `config.ini` and verify that the `art_cli` path points to your installation of the `art-cmd` executable.

### 2. Set up Python Environment

This project uses a virtual environment to manage its Python packages, preventing conflicts with your system.

1.  **Create Virtual Environment:** From inside the `field_ingest_app` directory, run this command once:
    ```bash
    python3 -m venv venv
    ```
2.  **Activate Environment:** Before installing packages or running the app, you must activate the environment:
    ```bash
    source venv/bin/activate
    ```
3.  **Install Python Packages:** With the environment active, install the required packages:
    ```bash
    pip3 install -r requirements.txt
    ```

## How to Run

1.  Navigate to the `field_ingest_app` directory in your terminal.
2.  Run the launcher script:
    ```bash
    ./launch.sh
    ```
This script automatically activates the virtual environment and starts the GUI application.

## Using the Application

### 1. Setup

When you launch the app, you will see the setup screen.

-   **Select Project Folder:** Click "Browse..." and select a main folder for your project (e.g., on an external drive). The application will automatically create a set of subdirectories inside this folder.
-   **Start Processing:** Click this button to begin.

The application will create the following structure inside your chosen Project Folder. All work happens here, and no media is copied to your local computer.

```
<Your Project Folder>/
├── 01_WATCH_FOLDER/     (Drop your raw footage here)
├── 02_OUTPUT/           (Transcoded DNxHD files appear here)
├── 03_PROCESSED/        (Source files are moved here after success)
├── 04_ERROR/            (Source files are moved here on failure)
└── _internal/           (For logs and status files)
```

### 2. Monitoring

After starting, the view will switch to the monitoring dashboard.

-   **Status Indicator:** A pulsing green dot shows when processing is active, with the current stage displayed (e.g., "FFmpeg Transcoding").
-   **Progress Bar:** A smooth, animated progress bar showing percentage complete.
-   **Queue Panel:** Files waiting to be processed, with file icons.
-   **Completed Panel:** Processed files with success (✓) or failure (✗) indicators and duration.
-   **Collapsible Log:** A detailed, real-time log panel that can be collapsed to save space.
-   **Stats Bar:** Running totals of processed, failed, and queued files.

## GUI Features

The application uses a modern dark theme interface built with CustomTkinter:

- **Dark "Matrix" Theme:** Deep dark background with vibrant green accents, optimized for field use
- **Rounded Widgets:** Modern card-based layout with rounded corners
- **Visual Feedback:** Pulsing status indicator, color-coded success/failure states
- **Responsive Layout:** Window can be resized while maintaining proper proportions
- **Pause/Resume:** Safely pause processing between files for transport

## Pause Feature

Designed for field use where you may need to move locations mid-session:

1. **Click Pause** → Status shows "PAUSING" (amber, pulsing)
2. **Current file completes** → Status changes to "PAUSED" (amber, static)
3. **Safe to close laptop** and move to new location
4. **Click Resume** → Processing continues with remaining queue

The pause feature waits for the current file to finish (art-cli/ffmpeg cannot be interrupted mid-process), then holds the queue until you're ready to continue.

---
### For Claude Code Manifest

Please add the following entry to the project manifest:

```json
{
  "project_name": "Field Ingest Engine",
  "version": "2.0.0",
  "description": "A standalone GUI application for transcoding ARRI footage in the field.",
  "entry_point": "field_ingest_app/launch.sh",
  "type": "GUI Application",
  "tags": ["python", "customtkinter", "video", "transcoding", "arri", "ffmpeg"]
}
```
---