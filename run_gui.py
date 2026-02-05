import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk
import threading
import os
import sys
import time
import json
import atexit
from datetime import datetime
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
    """Modern Field Ingest Engine GUI using CustomTkinter."""

    def __init__(self):
        super().__init__()

        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title("Field Ingest Engine")
        self.geometry("900x750")
        self.minsize(800, 600)
        self.configure(fg_color=COLORS["bg_dark"])

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

    def start_ingest(self, source_folder, drive_root):
        """Initialize and start the ingest engine."""
        if not (source_folder and os.path.isdir(source_folder)):
            messagebox.showerror("Error", "Please select a valid source folder.")
            return False

        if not (drive_root and os.path.isdir(drive_root)):
            messagebox.showerror("Error", "Could not determine drive root.")
            return False

        self.project_folder.set(drive_root)
        self.source_folder = source_folder

        # Source folder is where files are (watch folder points here)
        # Output folders go at drive root
        # Note: No 'processed' folder - source files stay in place
        self.paths = {
            'watch': source_folder,  # Process files directly from source
            'output': os.path.join(drive_root, '02_OUTPUT'),
            'processed': os.path.join(drive_root, '_internal', 'processed'),  # Placeholder, not used
            'error': os.path.join(drive_root, '03_ERROR'),
            'processing': os.path.join(drive_root, '_internal', 'processing'),
            'temp': os.path.join(drive_root, '_internal', 'temp'),
            'logs': os.path.join(drive_root, '_internal', 'logs'),
            'status_file': os.path.join(drive_root, '_internal', 'status.json'),
            'queue_file': os.path.join(drive_root, '_internal', 'queue.json'),
            'history_file': os.path.join(drive_root, '_internal', 'history.json'),
            'pause_file': os.path.join(drive_root, '_internal', 'pause_control.json'),
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
                "   - Field Ingest Engine.app (in dist folder)\n"
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

        # Directories created successfully, now start processing
        return self.do_start_processing()

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
        """Generate a PDF report of files processed in the current session."""
        # Use session history (only files from this session)
        history = self.session_history if hasattr(self, 'session_history') else []

        if not history:
            return None

        # Create PDF in output folder
        output_folder = self.paths.get('output', '')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_path = os.path.join(output_folder, f"Transcode_Report_{timestamp}.pdf")

        doc = SimpleDocTemplate(pdf_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=colors.darkgreen,
            spaceAfter=20
        )
        elements.append(Paragraph("Field Ingest Engine - Transcode Report", title_style))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
        if self.start_time:
            elements.append(Paragraph(f"Session started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
        elements.append(Spacer(1, 20))

        # Summary
        succeeded = sum(1 for item in history if item.get('status', '').upper() == 'SUCCEEDED')
        failed = sum(1 for item in history if item.get('status', '').upper() == 'FAILED')
        elements.append(Paragraph(f"Total Files: {len(history)} | Succeeded: {succeeded} | Failed: {failed}", styles['Normal']))
        elements.append(Spacer(1, 20))

        # Table of files
        table_data = [['File', 'Status', 'Duration']]
        for item in history:
            filename = os.path.basename(item.get('file', 'Unknown'))
            status = item.get('status', 'Unknown').upper()
            try:
                start = datetime.fromisoformat(item.get('start_time', ''))
                end = datetime.fromisoformat(item.get('end_time', ''))
                duration = str(end - start).split('.')[0]
            except (ValueError, TypeError):
                duration = "—"
            table_data.append([filename[:40], status, duration])

        table = Table(table_data, colWidths=[300, 80, 80])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
        ]))
        elements.append(table)

        doc.build(elements)
        return pdf_path

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
        self.drop_highlight = False

        self.create_widgets()

    def create_widgets(self):
        """Build the setup screen UI."""
        # Header section
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", pady=(20, 30))

        title_label = ctk.CTkLabel(
            header_frame,
            text="🎬  FIELD INGEST ENGINE",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=COLORS["accent"]
        )
        title_label.pack()

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="ARRI → DNxHD Transcoding",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_dim"]
        )
        subtitle_label.pack(pady=(5, 0))

        # Detected output location
        self.output_info = ctk.CTkLabel(
            header_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"]
        )
        self.output_info.pack(pady=(10, 0))

        # Main card with drop zone
        self.drop_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=15,
            border_width=2,
            border_color=COLORS["border"]
        )
        self.drop_card.pack(fill="x", padx=40, pady=10)

        # Drop zone content
        drop_content = ctk.CTkFrame(self.drop_card, fg_color="transparent")
        drop_content.pack(fill="both", expand=True, padx=30, pady=30)

        folder_icon = ctk.CTkLabel(
            drop_content,
            text="📁",
            font=ctk.CTkFont(size=48)
        )
        folder_icon.pack(pady=(10, 15))

        self.drop_label = ctk.CTkLabel(
            drop_content,
            text="Select the folder containing\nfiles to transcode",
            font=ctk.CTkFont(size=16),
            text_color=COLORS["text"],
            justify="center"
        )
        self.drop_label.pack(pady=(0, 20))

        # Browse button
        self.browse_btn = ctk.CTkButton(
            drop_content,
            text="Browse Folder...",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["accent_dim"],
            border_width=1,
            border_color=COLORS["border"],
            height=40,
            width=180,
            command=self.browse_folder
        )
        self.browse_btn.pack(pady=(0, 15))

        # Selected path display with clear button
        path_row = ctk.CTkFrame(drop_content, fg_color="transparent")
        path_row.pack(fill="x", pady=(0, 10))

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

        # Folder structure preview
        preview_frame = ctk.CTkFrame(self, fg_color="transparent")
        preview_frame.pack(fill="x", padx=40, pady=(25, 15))

        self.preview_title = ctk.CTkLabel(
            preview_frame,
            text="Output folders will be created at drive root:",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_dim"],
            anchor="w"
        )
        self.preview_title.pack(fill="x")

        folders_info = [
            ("📂 02_OUTPUT", "DNxHD transcodes"),
            ("📂 03_ERROR", "Failed files (if any)"),
        ]

        for folder_name, description in folders_info:
            folder_row = ctk.CTkFrame(preview_frame, fg_color="transparent")
            folder_row.pack(fill="x", pady=2)

            name_label = ctk.CTkLabel(
                folder_row,
                text=f"    {folder_name}",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text"],
                anchor="w",
                width=200
            )
            name_label.pack(side="left")

            desc_label = ctk.CTkLabel(
                folder_row,
                text=f"- {description}",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_dim"],
                anchor="w"
            )
            desc_label.pack(side="left", padx=(10, 0))

        # Start button
        self.start_btn = ctk.CTkButton(
            self,
            text="▶  Start Processing",
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            height=50,
            width=220,
            corner_radius=10,
            state="disabled",
            command=self.start_processing
        )
        self.start_btn.pack(pady=30)

    def browse_folder(self):
        """Open folder browser dialog."""
        folder = filedialog.askdirectory(title="Select Project Folder")
        if folder:
            self.set_selected_folder(folder)

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
        self.output_info.configure(
            text=f"Output folders will be created at: {self.drive_root}",
            text_color=COLORS["success"]
        )

        self.start_btn.configure(state="normal")
        # Show clear button
        self.clear_btn.pack(side="right", padx=(10, 0))

    def clear_selection(self):
        """Clear the selected folder."""
        self.selected_folder = None
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
                self.set_selected_folder(parent)

    def on_drag_enter(self, event):
        """Highlight drop zone when dragging over."""
        self.drop_card.configure(border_color=COLORS["accent"])
        self.drop_label.configure(text="Drop folder here!", text_color=COLORS["accent"])

    def on_drag_leave(self, event):
        """Remove highlight when drag leaves."""
        self.drop_card.configure(border_color=COLORS["border"])
        self.drop_label.configure(
            text="Select or drag a project folder\nto begin processing",
            text_color=COLORS["text"]
        )

    def start_processing(self):
        """Start the ingest engine."""
        if self.selected_folder and self.drive_root:
            self.app.start_ingest(self.selected_folder, self.drive_root)


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
            text="FIELD INGEST ENGINE",
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
