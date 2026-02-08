import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk
import threading
import os
import sys
import time
import json
import atexit
import shutil
from datetime import datetime
from configparser import ConfigParser
import subprocess
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Drag-and-drop is disabled (tkinterdnd2 has issues on macOS M1)
DND_AVAILABLE = False

# Import the refactored main engine script
import main as ingest_engine

# --- Color Palette (Dark + Green "Matrix" Theme) ---
COLORS = {
    "bg_dark": "#0d1117",       # Deep dark background
    "bg_card": "#161b22",       # Card/panel background
    "bg_input": "#21262d",      # Input fields
    "accent": "#00ff88",        # Vibrant green accent
    "accent_hover": "#33ffaa",  # Hover state - brighter green
    "accent_dim": "#238636",    # Subdued green for borders
    "success": "#00ff88",       # Success green
    "error": "#ff5555",         # Error red
    "warning": "#ffaa00",       # Warning amber
    "text": "#e6edf3",          # Primary text
    "text_dim": "#7d8590",      # Secondary text
    "border": "#30363d",        # Subtle borders
}


class FieldIngestApp(ctk.CTk):
    """Modern 10-2 Transcoder GUI using CustomTkinter."""

    def __init__(self):
        super().__init__()

        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title("10-2 Transcoder")
        self.geometry("900x750")
        self.minsize(800, 600)
        self.configure(fg_color=COLORS["bg_dark"])

        # Config and LUT library
        self.config = self._load_config()
        self.lut_library_dir, self.lut_map_path = self._get_lut_paths()

        # State variables
        self.project_folder = ctk.StringVar()
        self.engine_thread = None
        self.paths = {}
        self.last_log_position = 0
        self.current_queue = []
        self.current_history = []
        self.session_history = []  # Only files from current session
        self.is_processing = False
        self.pulse_state = 0
        self.start_time = None
        self.processed_count = 0
        self.failed_count = 0
        self.queued_count = 0
        self.was_actively_processing = False  # Track if we ever processed a file
        self.completion_reported = False  # Prevent duplicate PDF generation
        self.idle_since = None  # Track when engine went idle (for cooldown)
        self.completion_cooldown = 10  # Seconds to wait before declaring completion
        self.session_skip_lut = set()
        self.session_lut_selection = {}

        # Window close handling
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        atexit.register(ingest_engine.release_lock)

        # Main container
        self.container = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"])
        self.container.pack(fill="both", expand=True)

        # Create frames
        self.setup_frame = SetupFrame(self.container, self)
        self.monitor_frame = MonitorFrame(self.container, self)

        # Show setup initially
        self.show_setup_frame()

    def show_setup_frame(self):
        """Switch to setup/welcome screen."""
        self.monitor_frame.pack_forget()
        self.setup_frame.pack(fill="both", expand=True, padx=20, pady=20)

    def show_monitor_frame(self):
        """Switch to processing monitor screen."""
        self.setup_frame.pack_forget()
        self.monitor_frame.pack(fill="both", expand=True, padx=20, pady=20)

    def start_ingest(self, source_folder, drive_root, embed_lut_enabled=True, selected_files=None):
        """Initialize and start the ingest engine."""
        if not (source_folder and os.path.isdir(source_folder)):
            messagebox.showerror("Error", "Please select a valid source folder.")
            return False

        if not (drive_root and os.path.isdir(drive_root)):
            messagebox.showerror("Error", "Could not determine drive root.")
            return False

        self.project_folder.set(drive_root)
        self.source_folder = source_folder
        self.selected_files = selected_files or []
        base_name = os.path.basename(os.path.normpath(source_folder)).strip() or "transcoded"
        safe_base = base_name.replace(" ", "_")
        output_folder = os.path.join(drive_root, f"{safe_base}_transcoded")
        internal_root = os.path.join(output_folder, "_internal")

        # Source folder is where files are (watch folder points here)
        # Output folders go at drive root
        # Note: No 'processed' folder - source files stay in place
        self.paths = {
            'watch': source_folder,  # Process files directly from source
            'output': output_folder,
            'processed': os.path.join(internal_root, 'processed'),  # Placeholder, not used
            'error': os.path.join(internal_root, 'error'),  # Not used; errors stay in PDF
            'processing': os.path.join(internal_root, 'processing'),
            'temp': os.path.join(internal_root, 'temp'),
            'logs': os.path.join(internal_root, 'logs'),
            'status_file': os.path.join(internal_root, 'status.json'),
            'queue_file': os.path.join(internal_root, 'queue.json'),
            'history_file': os.path.join(internal_root, 'history.json'),
            'pause_file': os.path.join(internal_root, 'pause_control.json'),
        }

        try:
            # Create output folders at drive root (skip 'watch' - that's the source folder)
            for key, path in self.paths.items():
                if key == 'watch':
                    continue  # Source folder already exists, don't try to create it
                folder = os.path.dirname(path) if '.' in os.path.basename(path) else path
                self._create_directory(folder)
            # Clear log file
            if os.path.exists(self.paths['logs']):
                open(os.path.join(self.paths['logs'], 'ingest_engine.log'), 'w').close()
            self.last_log_position = 0
        except PermissionError as e:
            messagebox.showerror(
                "Permission Denied",
                f"Cannot create directories at:\n{drive_root}\n\n"
                f"Error: {e}\n\n"
                "Try one of these solutions:\n\n"
                "Option 1 - Grant Full Disk Access (recommended):\n"
                "1. Open System Settings → Privacy & Security → Full Disk Access\n"
                "2. Click + and add BOTH:\n"
                "   - 10-2 Transcoder.app (in dist folder)\n"
                "   - FileHelper.app (in Resources folder inside the app)\n"
                "3. Restart both apps\n\n"
                "Option 2 - Enable 'Ignore ownership' on the drive:\n"
                "1. Select the drive in Finder\n"
                "2. Press Cmd+I (Get Info)\n"
                "3. Check 'Ignore ownership on this volume'"
            )
            return False
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create directory structure: {e}")
            return False

        os.environ["TEN2_EMBED_LUT"] = "1" if embed_lut_enabled else "0"
        if self.selected_files:
            os.environ["TEN2_FILE_LIST"] = json.dumps(self.selected_files)
        else:
            os.environ.pop("TEN2_FILE_LIST", None)
        if embed_lut_enabled:
            if not self._ensure_luts_for_batch(source_folder, self.selected_files):
                return False
        if self.session_skip_lut:
            os.environ["TEN2_SKIP_LUT_CAMERAS"] = ";".join(sorted(self.session_skip_lut))
        else:
            os.environ.pop("TEN2_SKIP_LUT_CAMERAS", None)
        if self.session_lut_selection:
            os.environ["TEN2_LUT_SELECTION"] = json.dumps(self.session_lut_selection)
        else:
            os.environ.pop("TEN2_LUT_SELECTION", None)

        # Directories created successfully, now start processing
        return self.do_start_processing()

    def _load_config(self):
        config = ConfigParser()
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
        config.read(config_path)
        return config

    def _get_lut_paths(self):
        library_dir = os.path.expanduser(self.config.get('LUT', 'library_dir', fallback='~/.10-2-transcoder/luts'))
        map_file = os.path.expanduser(self.config.get('LUT', 'map_file', fallback='~/.10-2-transcoder/lut_map.json'))
        return library_dir, map_file

    def _list_lut_library(self):
        os.makedirs(self.lut_library_dir, exist_ok=True)
        luts = []
        for name in os.listdir(self.lut_library_dir):
            if name.lower().endswith(".cube"):
                luts.append(name)
        return sorted(luts)

    def _copy_lut_to_library(self, lut_path):
        os.makedirs(self.lut_library_dir, exist_ok=True)
        base_name = os.path.basename(lut_path)
        dest_path = os.path.join(self.lut_library_dir, base_name)
        if os.path.abspath(lut_path) != os.path.abspath(dest_path):
            shutil.copy2(lut_path, dest_path)
        return dest_path

    def _remove_lut_from_library(self, lut_name):
        lut_path = os.path.join(self.lut_library_dir, lut_name)
        if os.path.exists(lut_path):
            os.remove(lut_path)
            return True
        return False

    def refresh_lut_summary(self):
        if hasattr(self, "setup_frame"):
            self.setup_frame.refresh_lut_list()

    def _ffprobe_camera_info(self, file_path):
        ffmpeg_path = os.path.expanduser(self.config.get('Paths', 'ffmpeg', fallback='ffmpeg'))
        ffprobe_path = ffmpeg_path.replace('ffmpeg', 'ffprobe')
        cmd = [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format_tags:stream_tags",
            "-of", "json",
            file_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout) if result.stdout else {}
        except Exception:
            return {}
        tags = {}
        for section in ("format",):
            for key in ("tags",):
                tags.update(data.get(section, {}).get(key, {}) or {})
        for stream in data.get("streams", []) or []:
            tags.update(stream.get("tags", {}) or {})
        return tags

    def _tag_text(self, tags):
        if not tags:
            return ""
        parts = []
        for k, v in tags.items():
            parts.append(str(k))
            parts.append(str(v))
        return " ".join(parts).upper()

    def _detect_camera_family(self, file_path):
        tags = self._ffprobe_camera_info(file_path)
        combined = self._tag_text(tags)
        filename = os.path.basename(file_path).upper()
        ext = os.path.splitext(filename)[1].lower()

        if "ARRI" in combined or "ARRI" in filename:
            if "ALEXA 35" in combined or "ALEXA35" in combined or "ALEXA35" in filename:
                return "ARRI Alexa 35"
            if "ALEXA MINI" in combined or "ALEXA_MINI" in combined or "ALEXA MINI" in filename:
                return "ARRI Alexa Mini"
            if "AMIRA" in combined or "AMIRA" in filename:
                return "ARRI Amira"
            if "ALEXA" in combined:
                return "ARRI Alexa"
        if "SONY" in combined or "SONY" in filename:
            if "FX6" in combined or "FX6" in filename:
                return "Sony FX6"
            if "FX3" in combined or "FX3" in filename:
                return "Sony FX3"
            if "A7S" in combined or "A7S" in filename:
                return "Sony A7S"
            return "Sony"
        if "DJI" in combined or "DJI" in filename:
            return "DJI Video"
        # Heuristic fallback for common Sony/DJI MP4/MOV when tags are missing
        if ext in (".mp4", ".mov"):
            if "DJI" in filename:
                return "DJI Video"
            if "SONY" in filename or "FX6" in filename or "FX3" in filename or "A7S" in filename:
                return "Sony"
        return "Unknown"

    def _needs_lut_for_camera(self, camera_family):
        if camera_family == "Unknown":
            return True
        art_cli = os.path.expanduser(self.config.get('Paths', 'art_cli', fallback=''))
        art_available = bool(art_cli and os.path.exists(art_cli))
        if camera_family == "ARRI Alexa 35":
            return False
        if camera_family in ("ARRI Alexa Mini", "ARRI Amira", "ARRI Alexa"):
            return not art_available
        return True

    def _suggest_lut_from_tags(self, file_path, library_names):
        if not library_names:
            return None
        look_name = self._get_embedded_look_name(file_path)
        if look_name:
            match = self._match_lut_name(look_name, library_names)
            if match:
                return match
        tags = self._ffprobe_camera_info(file_path)
        if not tags:
            return None
        combined = " ".join([str(k) + " " + str(v) for k, v in tags.items()]).upper()
        for name in library_names:
            base = os.path.splitext(name)[0].upper()
            if base and base in combined:
                return name
        return None

    def _get_embedded_look_name(self, file_path):
        if not file_path:
            return None
        tags = self._ffprobe_camera_info(file_path)
        if not tags:
            return None
        for key in ("com.arri.camera.look.name", "ARRI:LookName", "lookname", "look_name"):
            if key in tags and tags[key]:
                return str(tags[key]).strip()
        return None

    def _normalize_name(self, value: str) -> str:
        if not value:
            return ""
        return "".join(ch for ch in value.lower() if ch.isalnum())

    def _match_lut_name(self, look_name: str, library_names):
        target = self._normalize_name(look_name)
        if not target:
            return None
        for name in library_names:
            stem = os.path.splitext(name)[0]
            if self._normalize_name(stem) == target:
                return name
        for name in library_names:
            stem = os.path.splitext(name)[0]
            if target and target in self._normalize_name(stem):
                return name
        return None

    def _ensure_luts_for_batch(self, source_folder, selected_files=None):
        required = []
        camera_samples = {}

        if selected_files:
            entries = [os.path.basename(p) for p in selected_files]
            entry_map = {os.path.basename(p): p for p in selected_files}
        else:
            try:
                entries = sorted(os.listdir(source_folder))
            except Exception as e:
                messagebox.showerror("Error", f"Failed to scan source folder: {e}")
                return False
            entry_map = {}

        extensions = self.config.get('Processing', 'allowed_extensions', fallback='.mov,.mxf,.mp4').split(',')
        extensions = [e.strip().lower() for e in extensions if e.strip()]

        seen_cameras = set()
        for name in entries:
            full_path = entry_map.get(name) or os.path.join(source_folder, name)
            if not os.path.isfile(full_path):
                continue
            _, ext = os.path.splitext(name)
            if ext.lower() not in extensions:
                continue
            camera = self._detect_camera_family(full_path)
            if camera in seen_cameras:
                continue
            seen_cameras.add(camera)
            if self._needs_lut_for_camera(camera):
                camera_samples[camera] = full_path
                required.append(camera)

        if not required:
            return True

        library_names = self._list_lut_library()
        dialog = LutSelectionDialog(
            self,
            required,
            camera_samples,
            library_names,
            suggest_fn=self._suggest_lut_from_tags
        )
        self.wait_window(dialog)
        if not dialog.confirmed:
            return False

        self.session_skip_lut.clear()
        self.session_lut_selection.clear()

        for cam, choice in dialog.selections.items():
            if choice == "__NONE__":
                self.session_skip_lut.add(cam)
                continue
            lut_path = os.path.join(self.lut_library_dir, choice)
            if os.path.exists(lut_path):
                self.session_lut_selection[cam] = lut_path
            else:
                messagebox.showerror("Missing LUT", f"LUT file not found for {cam}: {choice}")
                return False

        return True

    def _get_file_helper_app_path(self):
        """Get path to the FileHelper.app bundle."""
        # Try multiple locations to find the helper app

        # Location 1: In app bundle Resources (py2app)
        try:
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            bundle_resources = os.path.join(os.path.dirname(exe_dir), 'Resources', 'FileHelper.app')
            if os.path.exists(bundle_resources):
                return bundle_resources
        except Exception:
            pass

        # Location 2: Next to __file__ in Resources
        try:
            file_dir = os.path.dirname(os.path.abspath(__file__))
            nearby_helper = os.path.join(file_dir, 'FileHelper.app')
            if os.path.exists(nearby_helper):
                return nearby_helper
        except Exception:
            pass

        # Location 3: Development mode - same directory as script
        try:
            dev_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'FileHelper.app')
            if os.path.exists(dev_path):
                return dev_path
        except Exception:
            pass

        return None

    def _create_directory(self, path):
        """Create directory, trying multiple methods for external drive compatibility."""
        if os.path.exists(path):
            return

        import subprocess

        # Method 1: Use native Swift FileHelper.app via 'open' (gets independent permissions)
        helper_app = self._get_file_helper_app_path()
        if helper_app:
            # Try multiple times - first attempt may trigger permission dialog,
            # subsequent attempts work after permission is granted
            for attempt in range(3):
                try:
                    # Use 'open -W' to launch as independent app and wait for completion
                    # This gives the helper its own permission context
                    result = subprocess.run(
                        ['open', '-W', helper_app, '--args', 'mkdir', path],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    # Check if directory was created (open always returns 0)
                    if os.path.exists(path):
                        return
                    # Small delay before retry
                    if attempt < 2:
                        time.sleep(0.5)
                except Exception as e:
                    print(f"FileHelper.app attempt {attempt+1} exception: {e}")

        # Method 2: Normal os.makedirs (fallback)
        try:
            os.makedirs(path, exist_ok=True)
            return
        except PermissionError:
            pass

        # Method 3: subprocess mkdir -p
        try:
            result = subprocess.run(
                ['mkdir', '-p', path],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and os.path.exists(path):
                return
        except Exception:
            pass

        # If all else fails, raise the original error
        os.makedirs(path, exist_ok=True)  # This will raise PermissionError

    def do_start_processing(self):
        """Actually start the processing after directories are created."""
        # Reset counters and flags for new session
        self.processed_count = 0
        self.failed_count = 0
        self.queued_count = 0
        self.was_actively_processing = False
        self.completion_reported = False
        self.idle_since = None
        self.current_history = []
        self.session_history = []
        self.start_time = datetime.now()
        self.is_processing = True

        # Start engine thread
        self.engine_thread = threading.Thread(
            target=ingest_engine.main,
            kwargs={'path_overrides': self.paths},
            daemon=True
        )
        self.engine_thread.start()

        # Switch to monitor view
        self.show_monitor_frame()
        self.monitor_frame.start_monitoring()

        return True

    def generate_pdf_report(self):
        """Generate a dark-mode PDF report of files processed in the current session."""
        history = self.session_history if hasattr(self, 'session_history') else []
        if not history:
            return None

        output_folder = self.paths.get('output', '')
        os.makedirs(output_folder, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_path = os.path.join(output_folder, f"Transcode_Report_{timestamp}.pdf")

        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            Image as RLImage
        )
        from reportlab.platypus.flowables import Flowable

        width, height = letter

        # --- Colors (Dark Mode) ---
        dark_bg = colors.HexColor("#0d1117")
        accent_green = colors.HexColor("#00ff88")
        white = colors.white
        card_dark = colors.HexColor("#141a22")
        card_mid = colors.HexColor("#1b2230")
        border_gray = colors.HexColor("#2d3540")
        success_green = colors.HexColor("#00ff88")
        error_red = colors.HexColor("#ff5555")

        # --- Custom Header Flowable ---
        class DarkHeaderBand(Flowable):
            """Full-width dark header band with icon, title, and date."""
            def __init__(self, icon_path, job_name, date_str, w, h):
                Flowable.__init__(self)
                self.icon_path = icon_path
                self.job_name = job_name
                self.date_str = date_str
                self.width = w
                self.height = h

            def wrap(self, availWidth, availHeight):
                return self.width, self.height

            def draw(self):
                canvas = self.canv
                canvas.setFillColor(dark_bg)
                canvas.rect(0, 0, self.width, self.height, fill=1, stroke=0)

                icon_size = 64
                icon_x = 20
                icon_y = (self.height - icon_size) / 2
                if self.icon_path and os.path.exists(self.icon_path):
                    try:
                        canvas.drawImage(
                            self.icon_path, icon_x, icon_y,
                            width=icon_size, height=icon_size,
                            preserveAspectRatio=True, mask='auto'
                        )
                    except Exception:
                        pass

                text_x = icon_x + icon_size + 16
                canvas.setFillColor(accent_green)
                canvas.setFont("Helvetica-Bold", 22)
                canvas.drawString(text_x, self.height - 38, "10-2 TRANSCODER REPORT")

                canvas.setFillColor(white)
                canvas.setFont("Helvetica", 11)
                job_display = self.job_name or "N/A"
                canvas.drawString(text_x, self.height - 58, f"Job: {job_display}")
                canvas.drawString(text_x, self.height - 75, self.date_str)

        styles = getSampleStyleSheet()
        style_section_title = ParagraphStyle(
            'SectionTitle',
            parent=styles['Heading2'],
            fontSize=18,
            spaceAfter=8,
            spaceBefore=16,
            textColor=white,
        )
        style_body = ParagraphStyle(
            'BodyCustom',
            parent=styles['Normal'],
            fontSize=13,
            textColor=white,
            leading=18,
        )
        style_log = ParagraphStyle(
            'LogEntry',
            parent=styles['Normal'],
            fontName='Courier',
            fontSize=9,
            textColor=colors.HexColor("#c9d1d9"),
            leading=12,
        )

        def _find_ffprobe_local() -> str | None:
            ffmpeg_path = os.path.expanduser(self.config.get('Paths', 'ffmpeg', fallback='ffmpeg'))
            ffprobe_path = ffmpeg_path.replace('ffmpeg', 'ffprobe')
            if os.path.isfile(ffprobe_path) and os.access(ffprobe_path, os.X_OK):
                return ffprobe_path
            for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
            return shutil.which("ffprobe")

        def _ffprobe_info(path: str) -> dict:
            ffprobe = _find_ffprobe_local()
            if not ffprobe:
                return {}
            ext = os.path.splitext(path)[1].lower()
            input_fmt = ["-f", "mxf"] if ext == ".mxf" else []
            cmd = [
                ffprobe,
                "-v", "error",
                "-probesize", "200M",
                "-analyzeduration", "200M",
                *input_fmt,
                "-show_entries", "format=duration,format_name:stream=index,codec_name,codec_type,width,height,avg_frame_rate,channels,sample_rate",
                "-of", "json",
                path,
            ]
            try:
                out = subprocess.check_output(cmd, text=True, timeout=10)
                return json.loads(out)
            except Exception:
                return {}

        story = []

        # Icon path
        icon_path = None
        base_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate in [
            os.path.join(base_dir, "icon.iconset", "icon_128x128.png"),
            os.path.join(base_dir, "icon_128x128.png"),
        ]:
            if os.path.exists(candidate):
                icon_path = candidate
                break

        # Header band
        date_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        header_width = width - 72
        job_name = os.path.basename(os.path.normpath(self.source_folder)) if getattr(self, "source_folder", "") else "N/A"
        header = DarkHeaderBand(icon_path, job_name, date_str, header_width, 90)
        story.append(header)
        story.append(Spacer(1, 16))

        # Summary + Media details
        succeeded = sum(1 for item in history if item.get('status', '').upper() == 'SUCCEEDED')
        failed = sum(1 for item in history if item.get('status', '').upper() == 'FAILED')
        status_text = "Verified" if failed == 0 else "Errors"
        preset = os.environ.get("TEN2_OUTPUT_PRESET", self.config.get('Output', 'default_preset', fallback='DNxHD_145'))

        elapsed = "—"
        if self.start_time:
            elapsed = str(datetime.now() - self.start_time).split('.')[0]

        source_paths = []
        for item in history:
            sp = item.get("source_path") or ""
            if not sp and getattr(self, "source_folder", "") and item.get("file"):
                sp = os.path.join(self.source_folder, item.get("file"))
            if sp:
                source_paths.append(sp)

        total_bytes = 0
        for sp in source_paths:
            try:
                total_bytes += os.path.getsize(sp)
            except Exception:
                pass

        fps_values: set[float] = set()
        formats: set[str] = set()
        video_codecs: set[str] = set()
        audio_codecs: set[str] = set()
        resolutions: set[str] = set()
        total_duration = 0.0
        media_files = 0
        min_date = None
        max_date = None

        for sp in source_paths:
            ext = os.path.splitext(sp)[1].lower()
            if ext in (".mxf", ".mov", ".mp4", ".m4v", ".avi"):
                media_files += 1
                info = _ffprobe_info(sp)
                fmt = info.get("format", {})
                if "format_name" in fmt:
                    formats.add(fmt["format_name"])
                if "duration" in fmt:
                    try:
                        total_duration += float(fmt["duration"])
                    except Exception:
                        pass
                for s in info.get("streams", []):
                    if s.get("codec_type") == "video":
                        if s.get("codec_name"):
                            video_codecs.add(s["codec_name"])
                        if s.get("width") and s.get("height"):
                            resolutions.add(f"{s['width']}x{s['height']}")
                        afr = s.get("avg_frame_rate")
                        rfr = s.get("r_frame_rate")
                        rate = afr or rfr
                        if rate and "/" in rate:
                            try:
                                num, den = rate.split("/", 1)
                                if float(den) != 0:
                                    fps_values.add(round(float(num) / float(den), 2))
                            except Exception:
                                pass
                    if s.get("codec_type") == "audio":
                        if s.get("codec_name"):
                            audio_codecs.add(s["codec_name"])
                try:
                    mtime = os.path.getmtime(sp)
                    if min_date is None or mtime < min_date:
                        min_date = mtime
                    if max_date is None or mtime > max_date:
                        max_date = mtime
                except Exception:
                    pass

        if len(fps_values) == 1:
            fps_display = f"{next(iter(fps_values))} fps"
        elif len(fps_values) > 1:
            fps_display = "Mixed"
        else:
            fps_display = "Unknown"

        def _fmt_duration(seconds: float) -> str:
            if seconds <= 0:
                return "0:00"
            mins, secs = divmod(int(seconds), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                return f"{hours}:{mins:02d}:{secs:02d}"
            return f"{mins}:{secs:02d}"

        story.append(Paragraph("Summary", style_section_title))

        summary_cards = [
            ("Job #", job_name or "N/A"),
            ("Elapsed", elapsed),
            ("Status", status_text),
            ("Total Size", self._format_size(total_bytes)),
            ("Total Files", str(len(history))),
            ("FPS", fps_display),
        ]
        card_w = header_width / 3
        card_rows = []
        for i in range(0, len(summary_cards), 3):
            row = []
            for label, value in summary_cards[i:i+3]:
                cell = Table(
                    [[label], [value]],
                    colWidths=[card_w - 8],
                    style=TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), card_dark),
                        ('BACKGROUND', (0, 1), (-1, 1), card_mid),
                        ('TEXTCOLOR', (0, 0), (-1, 0), white),
                        ('TEXTCOLOR', (0, 1), (-1, 1), white),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica'),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('FONTSIZE', (0, 1), (-1, 1), 14),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('TOPPADDING', (0, 0), (-1, -1), 6),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ])
                )
                row.append(cell)
            card_rows.append(row)
        story.append(Table(card_rows, colWidths=[card_w] * 3, style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')])))
        story.append(Spacer(1, 10))

        source_text = f"Source: {self.source_folder or 'Unknown'}"
        dest_text = f"Destinations: {output_folder or 'Unknown'}"
        sd_table = Table(
            [[Paragraph(source_text, style_body), Paragraph(dest_text, style_body)]],
            colWidths=[header_width / 2, header_width / 2],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), card_mid),
                ('TEXTCOLOR', (0, 0), (-1, -1), white),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 12),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ])
        )
        story.append(sd_table)
        story.append(Spacer(1, 12))

        story.append(Paragraph("Media Details", style_section_title))
        date_range = "Unknown"
        if min_date and max_date:
            date_range = f"{datetime.fromtimestamp(min_date).date()} - {datetime.fromtimestamp(max_date).date()}"
        media_cards = [
            ("Media Files", str(media_files)),
            ("Total Duration", _fmt_duration(total_duration)),
            ("Resolutions", ", ".join(sorted(resolutions)) or "Unknown"),
            ("Video Codecs", ", ".join(sorted(video_codecs)) or "Unknown"),
            ("Audio Codecs", ", ".join(sorted(audio_codecs)) or "Unknown"),
            ("Recording Dates", date_range),
        ]
        media_rows = []
        for i in range(0, len(media_cards), 3):
            row = []
            for label, value in media_cards[i:i+3]:
                cell = Table(
                    [[label], [value]],
                    colWidths=[card_w - 8],
                    style=TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), card_dark),
                        ('BACKGROUND', (0, 1), (-1, 1), card_mid),
                        ('TEXTCOLOR', (0, 0), (-1, -1), white),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica'),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('FONTSIZE', (0, 1), (-1, 1), 12),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('TOPPADDING', (0, 0), (-1, -1), 6),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ])
                )
                row.append(cell)
            media_rows.append(row)
        story.append(Table(media_rows, colWidths=[card_w] * 3, style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')])))
        story.append(Spacer(1, 12))

        # --- File Details Table ---
        story.append(Paragraph("File Details", style_section_title))
        file_table_header = ["Status", "Filename", "Size", "Duration", "Error"]
        file_table_data = [file_table_header]

        style_cell = ParagraphStyle(
            'CellText',
            fontName='Courier',
            fontSize=9,
            leading=12,
            textColor=white,
        )
        style_cell_pass = ParagraphStyle(
            'CellPass',
            parent=style_cell,
            textColor=success_green,
        )
        style_cell_fail = ParagraphStyle(
            'CellFail',
            parent=style_cell,
            textColor=error_red,
        )

        for item in history:
            filename = os.path.basename(item.get('file', 'Unknown'))
            status = item.get('status', 'Unknown').upper()
            try:
                start = datetime.fromisoformat(item.get('start_time', ''))
                end = datetime.fromisoformat(item.get('end_time', ''))
                duration = str(end - start).split('.')[0]
            except (ValueError, TypeError):
                duration = "—"
            error = (item.get('error_details', '') or '').replace('\n', ' ')
            sp = item.get("source_path") or ""
            size_str = "—"
            if sp:
                try:
                    size_str = self._format_size(os.path.getsize(sp))
                except Exception:
                    size_str = "—"
            status_cell = Paragraph("PASS", style_cell_pass) if status == "SUCCEEDED" else Paragraph("FAIL", style_cell_fail)
            file_table_data.append([
                status_cell,
                filename,
                size_str,
                duration,
                Paragraph(error[:160], style_cell) if error else "",
            ])

        num_cols = len(file_table_header)
        status_w = 40
        size_w = 60
        duration_w = 70
        error_w = 140
        fixed_w = status_w + size_w + duration_w + error_w
        filename_w = max(80, header_width - fixed_w)
        col_widths = [status_w, filename_w, size_w, duration_w, error_w]

        file_table = Table(
            file_table_data,
            colWidths=col_widths,
            repeatRows=1,
        )

        table_style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f141a")),
            ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTNAME', (0, 1), (-1, -1), 'Courier'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TEXTCOLOR', (0, 1), (-1, -1), white),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 0), (3, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 1), (-1, -1), card_dark),
            ('GRID', (0, 0), (-1, -1), 0.25, border_gray),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]

        for i in range(1, len(file_table_data)):
            if i % 2 == 0:
                table_style_cmds.append(('BACKGROUND', (0, i), (-1, i), card_mid))

        file_table.setStyle(TableStyle(table_style_cmds))
        story.append(file_table)
        story.append(Spacer(1, 12))

        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=letter,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        def _draw_dark_bg(canvas, doc_obj):
            canvas.setFillColor(dark_bg)
            canvas.rect(0, 0, width, height, fill=1, stroke=0)

        doc.build(story, onFirstPage=_draw_dark_bg, onLaterPages=_draw_dark_bg)
        return pdf_path

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes as human-readable size."""
        try:
            size = float(size_bytes)
        except Exception:
            return "—"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def on_closing(self):
        """Handle window close event."""
        if self.is_processing:
            if not messagebox.askokcancel("Quit", "Processing is active. Stop and exit?"):
                return
            # Generate report before closing (only if not already generated)
            if not self.completion_reported:
                pdf_path = self.generate_pdf_report()
                if pdf_path:
                    messagebox.showinfo("Report Generated", f"Transcode report saved to:\n{pdf_path}")
        self.is_processing = False
        self.destroy()


class SetupFrame(ctk.CTkFrame):
    """Welcome/setup screen with folder selection and drag-drop."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.app = app
        self.selected_folder = None
        self.selected_files = []
        self.drop_highlight = False
        self.lut_rows = {}

        self.create_widgets()

    def create_widgets(self):
        """Build the setup screen UI."""
        # Header section
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", pady=(6, 6))

        title_label = ctk.CTkLabel(
            header_frame,
            text="🎬  10-2 TRANSCODER",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["accent"]
        )
        title_label.pack()

        # Detected output location
        self.output_info = ctk.CTkLabel(
            header_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"]
        )
        self.output_info.pack(pady=(6, 0))

        # Main card with drop zone
        self.drop_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=15,
            border_width=2,
            border_color=COLORS["border"]
        )
        self.drop_card.pack(fill="x", padx=26, pady=4)

        # Drop zone content
        drop_content = ctk.CTkFrame(self.drop_card, fg_color="transparent")
        drop_content.pack(fill="both", expand=True, padx=18, pady=10)

        folder_icon = ctk.CTkLabel(
            drop_content,
            text="📁",
            font=ctk.CTkFont(size=30)
        )
        folder_icon.pack(pady=(6, 8))

        self.drop_label = ctk.CTkLabel(
            drop_content,
            text="Select a folder or files to transcode",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"],
            justify="center"
        )
        self.drop_label.pack(pady=(0, 8))

        # Browse buttons
        browse_row = ctk.CTkFrame(drop_content, fg_color="transparent")
        browse_row.pack(pady=(0, 6))

        self.browse_btn = ctk.CTkButton(
            browse_row,
            text="Browse Folder…",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=34,
            width=170,
            command=self.browse_folder
        )
        self.browse_btn.pack(side="left", padx=(0, 8))

        self.browse_files_btn = ctk.CTkButton(
            browse_row,
            text="Browse Files…",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=34,
            width=170,
            command=self.browse_files
        )
        self.browse_files_btn.pack(side="left")

        # Selected path display with clear button
        path_row = ctk.CTkFrame(drop_content, fg_color="transparent")
        path_row.pack(fill="x", pady=(0, 4))

        self.path_label = ctk.CTkLabel(
            path_row,
            text="No folder selected",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
            wraplength=450
        )
        self.path_label.pack(side="left", expand=True)

        self.clear_btn = ctk.CTkButton(
            path_row,
            text="✕",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["error"],
            text_color=COLORS["text_dim"],
            width=30,
            height=30,
            corner_radius=5,
            command=self.clear_selection
        )
        # Initially hidden
        self.clear_btn.pack_forget()

        # Enable drag-and-drop if available
        if DND_AVAILABLE:
            self.drop_card.drop_target_register(DND_FILES)
            self.drop_card.dnd_bind('<<Drop>>', self.on_drop)
            self.drop_card.dnd_bind('<<DragEnter>>', self.on_drag_enter)
            self.drop_card.dnd_bind('<<DragLeave>>', self.on_drag_leave)

        # LUT options
        lut_frame = ctk.CTkFrame(self, fg_color="transparent")
        lut_frame.pack(fill="x", padx=30, pady=(8, 0))

        self.embed_lut_var = ctk.IntVar(value=1)
        lut_toggle = ctk.CTkSwitch(
            lut_frame,
            text="Embed Look / LUT (per camera)",
            variable=self.embed_lut_var,
            onvalue=1,
            offvalue=0,
            text_color=COLORS["text"],
            fg_color=COLORS["accent"],
            progress_color=COLORS["accent"],
            button_color=COLORS["bg_input"]
        )
        lut_toggle.pack(anchor="w")

        lut_hint = ctk.CTkLabel(
            lut_frame,
            text="LUTs are stored in a library. You’ll choose per camera at start.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"]
        )
        lut_hint.pack(anchor="w", pady=(4, 0))

        toolbar = ctk.CTkFrame(lut_frame, fg_color="transparent")
        toolbar.pack(anchor="w", pady=(6, 0))

        manage_btn = ctk.CTkButton(
            toolbar,
            text="Manage LUT Library",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=30,
            width=165,
            command=self.open_lut_manager
        )
        manage_btn.pack(side="left")

        detect_btn = ctk.CTkButton(
            toolbar,
            text="Detect Cameras",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=30,
            width=140,
            command=self.detect_cameras
        )
        detect_btn.pack(side="left", padx=(8, 0))

        dng_btn = ctk.CTkButton(
            toolbar,
            text="DNG Sequence Tool…",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=30,
            width=160,
            command=self.open_dng_tool
        )
        dng_btn.pack(side="left", padx=(8, 0))

        # Output preset selection
        output_frame = ctk.CTkFrame(self, fg_color="transparent")
        output_frame.pack(fill="x", padx=30, pady=(8, 0))

        output_label = ctk.CTkLabel(
            output_frame,
            text="Output Format",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text"]
        )
        output_label.pack(anchor="w")

        preset_list = self.app.config.get('Output', 'preset_list', fallback='DNxHD_145').split(',')
        preset_list = [p.strip() for p in preset_list if p.strip()]
        default_preset = self.app.config.get('Output', 'default_preset', fallback=preset_list[0] if preset_list else 'DNxHD_145')

        self.output_preset_var = ctk.StringVar(value=default_preset)
        self.output_menu = ctk.CTkOptionMenu(
            output_frame,
            values=preset_list,
            variable=self.output_preset_var,
            fg_color=COLORS["bg_input"],
            button_color=COLORS["bg_input"],
            button_hover_color=COLORS["accent_dim"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["accent_dim"],
            text_color=COLORS["text"]
        )
        self.output_menu.pack(anchor="w", pady=(4, 0))

        # Start button (keep visible without resizing)
        self.start_btn = ctk.CTkButton(
            self,
            text="▶  Start Processing",
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            height=46,
            width=220,
            corner_radius=10,
            state="disabled",
            command=self.start_processing
        )
        self.start_btn.pack(pady=(10, 8))

        # LUT summary list
        self.lut_list_frame = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=10, height=70)
        self.lut_list_frame.pack(fill="x", padx=26, pady=(6, 0))
        self.refresh_lut_list()

    def browse_folder(self):
        """Open folder browser dialog."""
        folder = filedialog.askdirectory(title="Select Project Folder")
        if folder:
            self.set_selected_folder(folder)

    def browse_files(self):
        """Open file browser dialog (multiple selection)."""
        extensions = self.app.config.get('Processing', 'allowed_extensions', fallback='.mov,.mxf,.mp4').split(',')
        extensions = [e.strip().lower() for e in extensions if e.strip()]
        if extensions:
            patterns = " ".join(f"*{e}" for e in extensions)
            filetypes = [("Media files", patterns)]
        else:
            filetypes = [("All files", "*.*")]
        files = filedialog.askopenfilenames(title="Select Media Files", filetypes=filetypes)
        if files:
            self.set_selected_files(list(files))

    def _get_drive_root(self, path):
        """Get the drive/volume root from a path."""
        # For paths like /Volumes/531H/B_0001_1DZI, return /Volumes/531H
        if path.startswith('/Volumes/'):
            parts = path.split('/')
            if len(parts) >= 3:
                return '/'.join(parts[:3])  # /Volumes/DriveName
        # For other paths, go up to find a mount point or use parent
        current = path
        while current and current != '/':
            parent = os.path.dirname(current)
            if parent == current:
                break
            # Check if we're at a volume boundary
            if os.path.ismount(current):
                return current
            current = parent
        return os.path.dirname(path)  # Fallback to parent directory

    def set_selected_folder(self, folder_path):
        """Update the selected folder and enable start button."""
        self.selected_files = []
        self.selected_folder = folder_path
        self.drive_root = self._get_drive_root(folder_path)

        # Truncate path for display if needed
        display_path = folder_path
        if len(display_path) > 60:
            display_path = "..." + display_path[-57:]
        self.path_label.configure(
            text=f"Source: {display_path}",
            text_color=COLORS["accent"]
        )

        # Show where output will go
        base_name = os.path.basename(os.path.normpath(folder_path)).strip() or "transcoded"
        safe_base = base_name.replace(" ", "_")
        self.output_info.configure(
            text=f"Output: {os.path.join(self.drive_root, f'{safe_base}_transcoded')}",
            text_color=COLORS["success"]
        )

        self.start_btn.configure(state="normal")
        # Show clear button
        self.clear_btn.pack(side="right", padx=(10, 0))

    def clear_selection(self):
        """Clear the selected folder."""
        self.selected_folder = None
        self.selected_files = []
        self.drive_root = None
        self.path_label.configure(
            text="No folder selected",
            text_color=COLORS["text_dim"]
        )
        self.output_info.configure(text="")
        self.start_btn.configure(state="disabled")
        self.clear_btn.pack_forget()

    def on_drop(self, event):
        """Handle file/folder drop."""
        self.on_drag_leave(event)
        # Parse dropped path (may have braces on some platforms)
        path = event.data
        if path.startswith('{') and path.endswith('}'):
            path = path[1:-1]
        if os.path.isdir(path):
            self.set_selected_folder(path)
        else:
            # If file dropped, use its parent directory
            parent = os.path.dirname(path)
            if os.path.isdir(parent):
                self.set_selected_files([path])

    def on_drag_enter(self, event):
        """Highlight drop zone when dragging over."""
        self.drop_card.configure(border_color=COLORS["accent"])
        self.drop_label.configure(text="Drop folder or files here!", text_color=COLORS["accent"])

    def on_drag_leave(self, event):
        """Remove highlight when drag leaves."""
        self.drop_card.configure(border_color=COLORS["border"])
        self.drop_label.configure(
            text="Select or drag a folder or files\nto begin processing",
            text_color=COLORS["text"]
        )

    def start_processing(self):
        """Start the ingest engine."""
        if self.selected_folder and self.drive_root:
            self.app.session_skip_lut.clear()
            self.app.session_lut_selection.clear()
            embed_lut_enabled = bool(self.embed_lut_var.get())
            os.environ["TEN2_OUTPUT_PRESET"] = self.output_preset_var.get()
            self.app.start_ingest(
                self.selected_folder,
                self.drive_root,
                embed_lut_enabled=embed_lut_enabled,
                selected_files=self.selected_files
            )

    def set_selected_files(self, files):
        """Update the selected files list and enable start button."""
        if not files:
            return
        parent_dirs = {os.path.dirname(f) for f in files}
        if len(parent_dirs) != 1:
            messagebox.showerror("Selection Error", "Please select files from a single folder.")
            return
        folder_path = parent_dirs.pop()
        self.selected_files = files
        self.selected_folder = folder_path
        self.drive_root = self._get_drive_root(folder_path)

        count = len(files)
        display_path = folder_path
        if len(display_path) > 60:
            display_path = "..." + display_path[-57:]
        self.path_label.configure(
            text=f"{count} file(s) selected\n{display_path}",
            text_color=COLORS["accent"]
        )

        base_name = os.path.basename(os.path.normpath(folder_path)).strip() or "transcoded"
        safe_base = base_name.replace(" ", "_")
        self.output_info.configure(
            text=f"Output: {os.path.join(self.drive_root, f'{safe_base}_transcoded')}",
            text_color=COLORS["success"]
        )

        self.start_btn.configure(state="normal")
        self.clear_btn.pack(side="right", padx=(10, 0))

    def refresh_lut_list(self):
        for child in self.lut_list_frame.winfo_children():
            child.destroy()

        header = ctk.CTkLabel(
            self.lut_list_frame,
            text="LUT Library (available)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text"]
        )
        header.pack(anchor="w", padx=12, pady=(8, 6))

        luts = self.app._list_lut_library()
        if not luts:
            empty = ctk.CTkLabel(
                self.lut_list_frame,
                text="No LUTs added yet.",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_dim"]
            )
            empty.pack(anchor="w", padx=12, pady=(4, 8))
            return

        for name in luts:
            row = ctk.CTkFrame(self.lut_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)

            name_label = ctk.CTkLabel(
                row,
                text=name,
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text"],
                anchor="w"
            )
            name_label.pack(side="left", expand=True, fill="x")

    def open_lut_manager(self):
        LutManagerWindow(self, self.app)

    def detect_cameras(self):
        if not self.selected_folder or not os.path.isdir(self.selected_folder):
            messagebox.showinfo("Detect Cameras", "Select a source folder first.")
            return

        extensions = self.app.config.get('Processing', 'allowed_extensions', fallback='.mov,.mxf,.mp4').split(',')
        extensions = [e.strip().lower() for e in extensions if e.strip()]
        skip_ext = self.app.config.get('Processing', 'skip_extensions', fallback='.dng').split(',')
        skip_ext = [e.strip().lower() for e in skip_ext if e.strip()]

        cameras = {}
        for name in sorted(os.listdir(self.selected_folder)):
            full_path = os.path.join(self.selected_folder, name)
            if not os.path.isfile(full_path):
                continue
            _, ext = os.path.splitext(name)
            if ext.lower() in skip_ext:
                continue
            if ext.lower() not in extensions:
                continue
            camera = self.app._detect_camera_family(full_path)
            cameras[camera] = cameras.get(camera, 0) + 1

        if not cameras:
            messagebox.showinfo("Detect Cameras", "No supported media files found.")
            return

        lines = ["Detected camera families:"]
        for cam, count in cameras.items():
            lines.append(f"- {cam} ({count} files)")
        lines.append("")
        lines.append("LUTs will be selected per camera when you start processing.")

        messagebox.showinfo("Detect Cameras", "\n".join(lines))

    def open_dng_tool(self):
        messagebox.showinfo(
            "DNG Sequence Tool",
            "DNG sequence processing will be handled by a separate tool.\n\n"
            "This is a placeholder for launching that app once it exists."
        )


class LutManagerWindow(ctk.CTkToplevel):
    """Manage LUT library (add/remove)."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("LUT Library")
        self.geometry("520x500")
        self.configure(fg_color=COLORS["bg_dark"])
        self.resizable(False, False)

        header = ctk.CTkLabel(
            self,
            text="LUT Library Manager",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"]
        )
        header.pack(pady=(15, 10))

        info = ctk.CTkLabel(
            self,
            text="Add .cube LUTs to the library. You’ll select per camera at start.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"]
        )
        info.pack(pady=(0, 10))

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=15, pady=(0, 10))

        add_btn = ctk.CTkButton(
            controls,
            text="Add LUT…",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=30,
            width=120,
            command=self.add_lut
        )
        add_btn.pack(side="left")

        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.list_frame = list_frame
        self.refresh_list()

    def refresh_list(self):
        for child in self.list_frame.winfo_children():
            child.destroy()

        luts = self.app._list_lut_library()
        if not luts:
            empty = ctk.CTkLabel(
                self.list_frame,
                text="No LUTs in library.",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_dim"]
            )
            empty.pack(anchor="w", padx=12, pady=10)
            return

        for name in luts:
            row = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=4)

            name_label = ctk.CTkLabel(
                row,
                text=name,
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text"],
                anchor="w"
            )
            name_label.pack(side="left", expand=True, fill="x")

            remove_btn = ctk.CTkButton(
                row,
                text="Remove",
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["bg_input"],
                hover_color=COLORS["error"],
                border_width=1,
                border_color=COLORS["border"],
                width=80,
                height=24,
                command=lambda n=name: self.remove_lut(n)
            )
            remove_btn.pack(side="right")

    def add_lut(self):
        lut_path = filedialog.askopenfilename(
            title="Add LUT to Library",
            filetypes=[("LUT files", "*.cube")]
        )
        if lut_path:
            try:
                self.app._copy_lut_to_library(lut_path)
            except Exception as e:
                messagebox.showerror("LUT Error", f"Failed to add LUT: {e}")
                return
            self.refresh_list()
            self.app.refresh_lut_summary()

    def remove_lut(self, lut_name):
        if not messagebox.askyesno("Remove LUT", f"Remove {lut_name} from the library?"):
            return
        try:
            removed = self.app._remove_lut_from_library(lut_name)
        except Exception as e:
            messagebox.showerror("LUT Error", f"Failed to remove LUT: {e}")
            return
        if not removed:
            messagebox.showerror("LUT Error", f"LUT not found: {lut_name}")
        self.refresh_list()
        self.app.refresh_lut_summary()


class LutSelectionDialog(ctk.CTkToplevel):
    """Prompt for LUT selection per camera for the current session."""

    def __init__(self, parent, cameras, camera_samples, library_names, suggest_fn):
        super().__init__(parent)
        self.title("Select LUTs")
        self.geometry("720x480")
        self.configure(fg_color=COLORS["bg_dark"])
        self.resizable(False, False)
        self.confirmed = False
        self.selections = {}
        self._library_names = list(library_names)
        self._menus = {}
        self._vars = {}
        self._suggest_fn = suggest_fn
        self._camera_samples = camera_samples

        header = ctk.CTkLabel(
            self,
            text="Select LUTs for Detected Cameras",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"]
        )
        header.pack(pady=(15, 6))

        subtitle = ctk.CTkLabel(
            self,
            text="Auto-selected LUTs are based on clip metadata. Please verify before continuing.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_dim"]
        )
        subtitle.pack(pady=(0, 10))

        list_frame = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"], corner_radius=10, height=280)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 12))

        for cam in cameras:
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=6)

            name_label = ctk.CTkLabel(
                row,
                text=cam,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text"],
                width=160,
                anchor="w"
            )
            name_label.pack(side="left")

            values = ["None"] + (self._library_names or [])
            var = ctk.StringVar(value="None")
            menu = ctk.CTkOptionMenu(
                row,
                values=values,
                variable=var,
                fg_color=COLORS["bg_input"],
                button_color=COLORS["bg_input"],
                button_hover_color=COLORS["accent_dim"],
                dropdown_fg_color=COLORS["bg_card"],
                dropdown_hover_color=COLORS["accent_dim"],
                text_color=COLORS["text"],
                width=260
            )
            menu.pack(side="left", padx=(8, 8))

            status_label = ctk.CTkLabel(
                row,
                text="",
                font=ctk.CTkFont(size=10),
                text_color=COLORS["text_dim"],
                width=90,
                anchor="w"
            )
            status_label.pack(side="left")

            add_btn = ctk.CTkButton(
                row,
                text="Add LUT…",
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["bg_input"],
                hover_color=COLORS["accent_dim"],
                border_width=1,
                border_color=COLORS["border"],
                width=90,
                height=26,
                command=lambda c=cam: self._add_lut_for_camera(c)
            )
            add_btn.pack(side="right")

            self._menus[cam] = menu
            self._vars[cam] = var

            sample_path = camera_samples.get(cam)
            look_name = self.master._get_embedded_look_name(sample_path) if sample_path else None
            if look_name:
                status_label.configure(text=f"embedded: {look_name}")
            if cam == "Unknown":
                var.set("None")
                if not look_name:
                    status_label.configure(text="no LUT by default")
            else:
                suggested = self._suggest_fn(sample_path, self._library_names) if sample_path else None
                if suggested and suggested in self._library_names:
                    var.set(suggested)
                    status_label.configure(text="auto-selected (embedded)")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(0, 12))

        cancel_btn = ctk.CTkButton(
            btn_row,
            text="Cancel",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["error"],
            border_width=1,
            border_color=COLORS["border"],
            width=120,
            command=self._cancel
        )
        cancel_btn.pack(side="right", padx=(8, 0))

        ok_btn = ctk.CTkButton(
            btn_row,
            text="Continue",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            width=140,
            command=self._confirm
        )
        ok_btn.pack(side="right")

        self.grab_set()

    def _refresh_options(self):
        values = ["None"] + (self._library_names or [])
        for cam, menu in self._menus.items():
            menu.configure(values=values)

    def _add_lut_for_camera(self, camera_label):
        lut_path = filedialog.askopenfilename(
            title=f"Add LUT for {camera_label}",
            filetypes=[("LUT files", "*.cube")]
        )
        if not lut_path:
            return
        try:
            dest = self.master._copy_lut_to_library(lut_path)
        except Exception as e:
            messagebox.showerror("LUT Error", f"Failed to add LUT: {e}")
            return
        name = os.path.basename(dest)
        if name not in self._library_names:
            self._library_names.append(name)
            self._library_names.sort()
        self._refresh_options()
        self._vars[camera_label].set(name)
        if hasattr(self.master, "refresh_lut_summary"):
            self.master.refresh_lut_summary()

    def _confirm(self):
        for cam, var in self._vars.items():
            choice = var.get()
            if not choice or choice == "None":
                self.selections[cam] = "__NONE__"
            else:
                self.selections[cam] = choice
        self.confirmed = True
        self.destroy()

    def _cancel(self):
        self.confirmed = False
        self.destroy()


class MonitorFrame(ctk.CTkFrame):
    """Processing monitor screen with status, queues, and logs."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self.app = app
        self.pulse_state = 0
        self.log_expanded = True

        self.create_widgets()

    def create_widgets(self):
        """Build the monitor screen UI."""
        # Header with back button
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 15))

        self.back_btn = ctk.CTkButton(
            header_frame,
            text="← Back",
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["bg_card"],
            text_color=COLORS["text_dim"],
            width=80,
            height=30,
            command=self.go_back
        )
        self.back_btn.pack(side="left")

        header_title = ctk.CTkLabel(
            header_frame,
            text="10-2 TRANSCODER",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text"]
        )
        header_title.pack(side="left", expand=True)

        # Spacer for centering
        spacer = ctk.CTkLabel(header_frame, text="", width=80)
        spacer.pack(side="right")

        # Status card
        self.status_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"]
        )
        self.status_card.pack(fill="x", pady=(0, 15))

        status_content = ctk.CTkFrame(self.status_card, fg_color="transparent")
        status_content.pack(fill="x", padx=20, pady=20)

        # Status header row
        status_header = ctk.CTkFrame(status_content, fg_color="transparent")
        status_header.pack(fill="x")

        self.status_indicator = ctk.CTkLabel(
            status_header,
            text="●",
            font=ctk.CTkFont(size=20),
            text_color=COLORS["text_dim"]
        )
        self.status_indicator.pack(side="left")

        self.status_text = ctk.CTkLabel(
            status_header,
            text="IDLE",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"]
        )
        self.status_text.pack(side="left", padx=(10, 0))

        # File info
        self.file_label = ctk.CTkLabel(
            status_content,
            text="File: Waiting for files...",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_dim"],
            anchor="w"
        )
        self.file_label.pack(fill="x", pady=(15, 5))

        self.stage_label = ctk.CTkLabel(
            status_content,
            text="Stage: —",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_dim"],
            anchor="w"
        )
        self.stage_label.pack(fill="x", pady=(0, 10))

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            status_content,
            height=12,
            corner_radius=6,
            fg_color=COLORS["bg_input"],
            progress_color=COLORS["accent"]
        )
        self.progress_bar.pack(fill="x", pady=(5, 5))
        self.progress_bar.set(0)

        # Progress info row
        progress_info = ctk.CTkFrame(status_content, fg_color="transparent")
        progress_info.pack(fill="x")

        self.progress_percent = ctk.CTkLabel(
            progress_info,
            text="0%",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"]
        )
        self.progress_percent.pack(side="left")

        self.time_label = ctk.CTkLabel(
            progress_info,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"]
        )
        self.time_label.pack(side="right")

        # Queue and History lists
        lists_frame = ctk.CTkFrame(self, fg_color="transparent")
        lists_frame.pack(fill="both", expand=True, pady=(0, 15))
        lists_frame.grid_columnconfigure(0, weight=1)
        lists_frame.grid_columnconfigure(1, weight=1)
        lists_frame.grid_rowconfigure(0, weight=1)

        # Queue panel
        queue_card = ctk.CTkFrame(
            lists_frame,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"]
        )
        queue_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        self.queue_header = ctk.CTkLabel(
            queue_card,
            text="QUEUE (0)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_dim"]
        )
        self.queue_header.pack(fill="x", padx=15, pady=(12, 8))

        self.queue_scroll = ctk.CTkScrollableFrame(
            queue_card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_input"],
            scrollbar_button_hover_color=COLORS["accent_dim"]
        )
        self.queue_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # History panel
        history_card = ctk.CTkFrame(
            lists_frame,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"]
        )
        history_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        self.history_header = ctk.CTkLabel(
            history_card,
            text="COMPLETED (0)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_dim"]
        )
        self.history_header.pack(fill="x", padx=15, pady=(12, 8))

        self.history_scroll = ctk.CTkScrollableFrame(
            history_card,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_input"],
            scrollbar_button_hover_color=COLORS["accent_dim"]
        )
        self.history_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Log panel
        self.log_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"]
        )
        self.log_card.pack(fill="x", pady=(0, 15))

        log_header = ctk.CTkFrame(self.log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=15, pady=(12, 8))

        log_title = ctk.CTkLabel(
            log_header,
            text="LOG",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_dim"]
        )
        log_title.pack(side="left")

        self.log_toggle = ctk.CTkButton(
            log_header,
            text="▼ Collapse",
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            hover_color=COLORS["bg_input"],
            text_color=COLORS["text_dim"],
            width=80,
            height=24,
            command=self.toggle_log
        )
        self.log_toggle.pack(side="right")

        self.log_text = ctk.CTkTextbox(
            self.log_card,
            height=120,
            font=ctk.CTkFont(family="Courier", size=11),
            fg_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            scrollbar_button_color=COLORS["bg_card"],
            scrollbar_button_hover_color=COLORS["accent_dim"],
            corner_radius=8
        )
        self.log_text.pack(fill="x", padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")

        # Stats bar
        stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        stats_frame.pack(fill="x", pady=(0, 10))

        self.stats_label = ctk.CTkLabel(
            stats_frame,
            text="Stats: 0 processed | 0 failed | 0 queued",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"]
        )
        self.stats_label.pack()

        # Button frame for pause and stop
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=(5, 10))

        # Pause/Resume button
        self.pause_btn = ctk.CTkButton(
            button_frame,
            text="⏸  Pause",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["warning"],
            hover_color="#ffcc44",
            text_color=COLORS["bg_dark"],
            height=45,
            width=150,
            corner_radius=10,
            command=self.toggle_pause
        )
        self.pause_btn.pack(side="left", padx=(0, 10))

        # Stop button
        self.stop_btn = ctk.CTkButton(
            button_frame,
            text="⏹  Stop",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#ff7777",
            text_color=COLORS["text"],
            height=45,
            width=150,
            corner_radius=10,
            command=self.app.on_closing
        )
        self.stop_btn.pack(side="left")

    def toggle_log(self):
        """Toggle log panel visibility."""
        if self.log_expanded:
            self.log_text.pack_forget()
            self.log_toggle.configure(text="▶ Expand")
            self.log_expanded = False
        else:
            self.log_text.pack(fill="x", padx=10, pady=(0, 10))
            self.log_toggle.configure(text="▼ Collapse")
            self.log_expanded = True

    def get_pause_state(self):
        """Read current pause state from control file."""
        pause_file = self.app.paths.get('pause_file', '')
        if not pause_file or not os.path.exists(pause_file):
            return {"paused": False, "pause_requested": False}
        try:
            with open(pause_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"paused": False, "pause_requested": False}

    def set_pause_state(self, paused=None, pause_requested=None):
        """Update the pause state control file."""
        pause_file = self.app.paths.get('pause_file', '')
        if not pause_file:
            return
        state = self.get_pause_state()
        if paused is not None:
            state["paused"] = paused
        if pause_requested is not None:
            state["pause_requested"] = pause_requested
        try:
            with open(pause_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            print(f"Failed to write pause state: {e}")

    def toggle_pause(self):
        """Toggle between pause and resume states."""
        pause_state = self.get_pause_state()

        if pause_state.get("paused", False):
            # Currently paused - resume
            self.set_pause_state(paused=False, pause_requested=False)
            self.pause_btn.configure(
                text="⏸  Pause",
                fg_color=COLORS["warning"],
                hover_color="#ffcc44"
            )
        elif pause_state.get("pause_requested", False):
            # Pause already requested - do nothing (waiting for file to finish)
            pass
        else:
            # Request pause
            self.set_pause_state(pause_requested=True)
            self.pause_btn.configure(
                text="⏳ Pausing...",
                fg_color=COLORS["text_dim"],
                hover_color=COLORS["text_dim"]
            )

    def go_back(self):
        """Return to setup screen."""
        if self.app.is_processing:
            if not messagebox.askokcancel("Stop Processing", "Stop processing and return to setup?"):
                return
            # Generate report before stopping
            pdf_path = self.app.generate_pdf_report()
            if pdf_path:
                messagebox.showinfo("Report Generated", f"Transcode report saved to:\n{pdf_path}")
            self.app.is_processing = False
        self.app.show_setup_frame()

    def start_monitoring(self):
        """Begin the monitoring update loop."""
        self.update_monitor()

    def update_monitor(self):
        """Update all monitor displays."""
        if not self.app.is_processing:
            return

        is_idle = True
        current_file = "Waiting for files..."
        current_stage = "—"
        progress = 0

        # Read status file
        try:
            status_file = self.app.paths.get('status_file', '')
            if status_file and os.path.exists(status_file):
                with open(status_file, 'r') as f:
                    status = json.load(f)

                is_idle = status.get('status') == 'idle'
                current_stage = status.get('stage', '—')
                current_file = os.path.basename(status.get('file', '')) or "Waiting for files..."
                progress = status.get('progress', 0)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            print(f"Error updating monitor: {e}")

        # Read pause state
        pause_state = self.get_pause_state()
        is_paused = pause_state.get("paused", False)
        pause_requested = pause_state.get("pause_requested", False)

        # Update status indicator based on state
        if is_paused:
            # Fully paused
            self.status_indicator.configure(text_color=COLORS["warning"])
            self.status_text.configure(text="PAUSED", text_color=COLORS["warning"])
            self.pause_btn.configure(
                text="▶  Resume",
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"]
            )
        elif pause_requested:
            # Pause requested, finishing current file
            self.pulse_state = (self.pulse_state + 1) % 2
            color = COLORS["warning"] if self.pulse_state else "#cc8800"
            self.status_indicator.configure(text_color=color)
            self.status_text.configure(text="PAUSING", text_color=COLORS["warning"])
            current_stage = f"Finishing file... ({current_stage})"
        elif is_idle:
            self.status_indicator.configure(text_color=COLORS["text_dim"])
            self.status_text.configure(text="IDLE", text_color=COLORS["text_dim"])
            # Reset pause button if not paused
            self.pause_btn.configure(
                text="⏸  Pause",
                fg_color=COLORS["warning"],
                hover_color="#ffcc44"
            )
        else:
            # Actively processing
            self.app.was_actively_processing = True
            self.app.idle_since = None  # Reset idle timer when actively processing
            self.pulse_state = (self.pulse_state + 1) % 2
            color = COLORS["accent"] if self.pulse_state else COLORS["accent_dim"]
            self.status_indicator.configure(text_color=color)
            self.status_text.configure(text="PROCESSING", text_color=COLORS["accent"])
            # Reset pause button if not paused
            self.pause_btn.configure(
                text="⏸  Pause",
                fg_color=COLORS["warning"],
                hover_color="#ffcc44"
            )

        # Track when idle started (for completion cooldown)
        if is_idle and self.app.idle_since is None:
            self.app.idle_since = datetime.now()
        elif not is_idle:
            self.app.idle_since = None

        # Update file and stage
        self.file_label.configure(text=f"File: {current_file}")
        self.stage_label.configure(text=f"Stage: {current_stage}")

        # Update progress
        self.progress_bar.set(progress / 100)
        self.progress_percent.configure(text=f"{int(progress)}%")

        # Update lists (must happen before completion check to get current counts)
        self.update_queue_list()
        self.update_history_list()

        # Update log
        self.update_log_viewer()

        # Update stats
        self.stats_label.configure(
            text=f"Stats: {self.app.processed_count} processed | {self.app.failed_count} failed | {self.app.queued_count} queued"
        )

        # Check for processing completion (was processing, now idle, queue empty)
        # This must happen AFTER update_queue_list and update_history_list so counts are current
        # Use cooldown to avoid false completion when briefly idle between files
        idle_long_enough = (
            self.app.idle_since is not None and
            (datetime.now() - self.app.idle_since).total_seconds() >= self.app.completion_cooldown
        )

        if (is_idle and
            idle_long_enough and
            self.app.was_actively_processing and
            self.app.queued_count == 0 and
            (self.app.processed_count > 0 or self.app.failed_count > 0) and
            not self.app.completion_reported):

            self.app.completion_reported = True
            self.status_text.configure(text="COMPLETE", text_color=COLORS["success"])

            # Generate PDF report
            pdf_path = self.app.generate_pdf_report()
            if pdf_path:
                from tkinter import messagebox
                if self.app.failed_count > 0:
                    messagebox.showwarning(
                        "Processing Complete (With Errors)",
                        f"Processing finished with errors.\n\n"
                        f"Processed: {self.app.processed_count}\n"
                        f"Failed: {self.app.failed_count}\n\n"
                        f"Report saved to:\n{pdf_path}"
                    )
                else:
                    messagebox.showinfo(
                        "Processing Complete",
                        f"All files processed!\n\n"
                        f"Processed: {self.app.processed_count}\n"
                        f"Failed: {self.app.failed_count}\n\n"
                        f"Report saved to:\n{pdf_path}"
                    )

        # Schedule next update
        self.after(500, self.update_monitor)

    def update_queue_list(self):
        """Update the queue list display."""
        queue_file = self.app.paths.get('queue_file', '')
        if not queue_file or not os.path.exists(queue_file):
            return

        try:
            with open(queue_file, 'r') as f:
                data = json.load(f)

            if data != self.app.current_queue:
                self.app.current_queue = data.copy()
                self.app.queued_count = len(data)

                # Clear existing items
                for widget in self.queue_scroll.winfo_children():
                    widget.destroy()

                # Add new items
                for item in data:
                    filename = os.path.basename(item) if isinstance(item, str) else str(item)
                    item_label = ctk.CTkLabel(
                        self.queue_scroll,
                        text=f"📄 {filename}",
                        font=ctk.CTkFont(size=12),
                        text_color=COLORS["text"],
                        anchor="w"
                    )
                    item_label.pack(fill="x", pady=2)

                # Update header
                self.queue_header.configure(text=f"QUEUE ({len(data)})")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def update_history_list(self):
        """Update the history list display (current session only)."""
        history_file = self.app.paths.get('history_file', '')
        if not history_file or not os.path.exists(history_file):
            return

        try:
            with open(history_file, 'r') as f:
                all_data = json.load(f)

            # Filter to only current session (items processed after session start)
            session_data = []
            if self.app.start_time:
                for item in all_data:
                    try:
                        item_start = datetime.fromisoformat(item.get('start_time', ''))
                        if item_start >= self.app.start_time:
                            session_data.append(item)
                    except (ValueError, TypeError):
                        pass

            # Store session-only data for PDF generation
            self.app.session_history = session_data

            if session_data != self.app.current_history:
                self.app.current_history = session_data.copy()

                # Count successes and failures (session only)
                succeeded = sum(1 for item in session_data if item.get('status', '').upper() == 'SUCCEEDED')
                failed = sum(1 for item in session_data if item.get('status', '').upper() == 'FAILED')
                self.app.processed_count = succeeded
                self.app.failed_count = failed

                # Clear existing items
                for widget in self.history_scroll.winfo_children():
                    widget.destroy()

                # Add new items (most recent first)
                for item in reversed(session_data[-50:]):  # Show last 50
                    status = item.get('status', 'UNKNOWN').upper()
                    filename = os.path.basename(item.get('file', ''))

                    # Determine icon and color
                    if status == 'SUCCEEDED':
                        icon = "✓"
                        color = COLORS["success"]
                    elif status == 'FAILED':
                        icon = "✗"
                        color = COLORS["error"]
                    else:
                        icon = "?"
                        color = COLORS["text_dim"]

                    # Calculate duration
                    try:
                        start = datetime.fromisoformat(item.get('start_time', ''))
                        end = datetime.fromisoformat(item.get('end_time', ''))
                        duration = str(end - start).split('.')[0]
                    except (ValueError, TypeError):
                        duration = "—"

                    item_frame = ctk.CTkFrame(self.history_scroll, fg_color="transparent")
                    item_frame.pack(fill="x", pady=2)

                    icon_label = ctk.CTkLabel(
                        item_frame,
                        text=icon,
                        font=ctk.CTkFont(size=12),
                        text_color=color,
                        width=20
                    )
                    icon_label.pack(side="left")

                    name_label = ctk.CTkLabel(
                        item_frame,
                        text=filename,
                        font=ctk.CTkFont(size=12),
                        text_color=COLORS["text"],
                        anchor="w"
                    )
                    name_label.pack(side="left", fill="x", expand=True)

                    time_label = ctk.CTkLabel(
                        item_frame,
                        text=f"({duration})",
                        font=ctk.CTkFont(size=11),
                        text_color=COLORS["text_dim"]
                    )
                    time_label.pack(side="right")

                # Update header
                self.history_header.configure(text=f"COMPLETED ({len(session_data)})")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def update_log_viewer(self):
        """Update the log text display."""
        log_path = os.path.join(self.app.paths.get('logs', ''), 'ingest_engine.log')
        if not log_path or not os.path.exists(log_path):
            return

        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(self.app.last_log_position)
                new_logs = f.read()
                if new_logs:
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", new_logs)
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                self.app.last_log_position = f.tell()
        except Exception as e:
            print(f"Error reading log file: {e}")


if __name__ == "__main__":
    app = FieldIngestApp()
    app.mainloop()
