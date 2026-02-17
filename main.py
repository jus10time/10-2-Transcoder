import os
import sys
import time
import logging
import atexit
import shutil
import fcntl
from configparser import ConfigParser
import json
import threading
import queue
import argparse
from watchdog.events import FileSystemEventHandler

# These imports will fail if the modules don't exist, but they are part of the project
from processor import process_clip, update_status
from api_server import start_api_server

# Use temp directory for lock files (works in bundled apps)
import tempfile
_temp_dir = tempfile.gettempdir()
LOCK_FILE = os.path.join(_temp_dir, '.field_ingest_engine.lock')
_lock_file_handle = None

# Global queue for files to be processed
file_queue = queue.Queue()

# Track files that have been queued to prevent duplicates
queued_files = set()
queued_files_lock = threading.Lock()

# Pause control - shared between GUI and engine
_pause_control_path = None
_pause_lock = threading.Lock()


def get_pause_state():
    """Read the current pause state from control file."""
    global _pause_control_path
    if not _pause_control_path or not os.path.exists(_pause_control_path):
        return {"paused": False, "pause_requested": False}
    try:
        with open(_pause_control_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"paused": False, "pause_requested": False}


def set_pause_state(paused=None, pause_requested=None):
    """Update the pause state control file."""
    global _pause_control_path
    if not _pause_control_path:
        return
    with _pause_lock:
        state = get_pause_state()
        if paused is not None:
            state["paused"] = paused
        if pause_requested is not None:
            state["pause_requested"] = pause_requested
        try:
            with open(_pause_control_path, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            logging.error(f"Failed to write pause state: {e}")


def wait_for_file_to_stabilize(file_path: str, delay: int = 2):
    """Waits for the file to stop changing size."""
    logging.info(f"Waiting for {os.path.basename(file_path)} to stabilize...")
    last_size = -1
    attempt = 0
    max_attempts = 5  # Reduced - camera card files are already complete
    while attempt < max_attempts:
        try:
            current_size = os.path.getsize(file_path)
            if current_size == last_size and current_size > 0:
                logging.info(f"File {os.path.basename(file_path)} has stabilized.")
                return True
            else:
                last_size = current_size
                attempt += 1
                time.sleep(delay)
        except FileNotFoundError:
            logging.warning(f"File {file_path} disappeared during stabilization check.")
            return False
        except Exception as e:
            logging.error(f"Error checking file stability for {file_path}: {e}")
            return False
    logging.warning(f"File {file_path} did not stabilize after {max_attempts * delay} seconds.")
    return False

WORKER_LOCK_FILE = os.path.join(_temp_dir, '.field_ingest_worker.lock')

def worker(q: queue.Queue, config: ConfigParser):
    """Worker thread function to process files from the queue."""
    processing_folder = os.path.expanduser(config['Paths'].get('processing', ''))

    while True:
        # Check if paused before getting next file
        while True:
            pause_state = get_pause_state()
            if pause_state.get("paused", False):
                logging.info("Engine is paused. Waiting to resume...")
                time.sleep(1)
                continue
            break

        # Use timeout so we can check pause state periodically
        try:
            original_path = q.get(timeout=1)
        except queue.Empty:
            continue

        if original_path is None:  # Signal to stop the worker
            break

        file_path = original_path
        filename = os.path.basename(file_path)

        try:
            worker_lock = open(WORKER_LOCK_FILE, 'w')
            fcntl.flock(worker_lock.fileno(), fcntl.LOCK_EX)
        except Exception as e:
            logging.error(f"Failed to acquire worker lock: {e}")
            q.task_done()
            continue

        logging.info(f"Worker processing file from queue: {file_path}")

        if not wait_for_file_to_stabilize(file_path):
            logging.warning(f"Skipping {file_path}: Not stabilized or disappeared.")
        elif not os.path.exists(file_path):
            logging.warning(f"Skipping {file_path}: Disappeared after stabilization.")
        else:
            # Process file in place (don't move from source - it may be read-only)
            logging.info(f"Starting transcode of: {filename}")
            try:
                process_clip(file_path, config)
            except Exception as e:
                logging.error(f"Error processing {file_path} from worker: {e}")

        try:
            fcntl.flock(worker_lock.fileno(), fcntl.LOCK_UN)
            worker_lock.close()
        except:
            pass
        q.task_done()

        # After completing a file, check if pause was requested
        pause_state = get_pause_state()
        if pause_state.get("pause_requested", False):
            logging.info("Pause requested. Engine pausing after completing current file.")
            set_pause_state(paused=True, pause_requested=False)

def enqueue_file(file_path: str, file_queue: queue.Queue, source: str):
    """Safely add a file to the processing queue."""
    filename = os.path.basename(file_path)
    with queued_files_lock:
        if filename in queued_files:
            return False
        queued_files.add(filename)
    logging.info(f"New file detected by {source}: {file_path}")
    file_queue.put(file_path)
    return True

class IngestEventHandler(FileSystemEventHandler):
    # ... (rest of the class is unchanged)
    def __init__(self, config: ConfigParser, file_queue: queue.Queue):
        super().__init__()
        self.config = config
        self.file_queue = file_queue
        self.processing_extensions = config.get('Processing', 'allowed_extensions').split(',')
        self.skip_extensions = config.get('Processing', 'skip_extensions', fallback='').split(',')
        self.last_seen_files = set()

    def on_created(self, event):
        if not event.is_directory:
            _, ext = os.path.splitext(event.src_path)
            if ext.lower() in self.skip_extensions:
                logging.warning(f"Skipping unsupported image sequence file: {event.src_path}")
                return
            if ext.lower() in self.processing_extensions:
                enqueue_file(event.src_path, self.file_queue, "watchdog")

    def on_moved(self, event):
        if not event.is_directory:
            _, ext = os.path.splitext(event.dest_path)
            if ext.lower() in self.skip_extensions:
                logging.warning(f"Skipping unsupported image sequence file: {event.dest_path}")
                return
            if ext.lower() in self.processing_extensions:
                enqueue_file(event.dest_path, self.file_queue, "watchdog")

def scan_watch_folder(handler: IngestEventHandler, watch_path: str):
    """Scans the watch folder for new files."""
    try:
        current_files = set(os.listdir(watch_path))
        new_files = current_files - handler.last_seen_files
        # Sort alphabetically so clips process in order (C001, C002, C003...)
        for file_name in sorted(new_files):
            full_path = os.path.join(watch_path, file_name)
            if os.path.isfile(full_path):
                _, ext = os.path.splitext(file_name)
                if ext.lower() in handler.skip_extensions:
                    logging.warning(f"Skipping unsupported image sequence file: {full_path}")
                    continue
                if ext.lower() in handler.processing_extensions:
                    enqueue_file(full_path, handler.file_queue, "polling")
        handler.last_seen_files = current_files
    except Exception as e:
        logging.error(f"Error during folder scan: {e}")

def acquire_lock():
    """Prevent multiple instances using atomic file locking."""
    global _lock_file_handle
    try:
        _lock_file_handle = open(LOCK_FILE, 'w')
        fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        atexit.register(release_lock)
    except (IOError, OSError):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid = f.read().strip()
            print(f"Error: Another instance is running (PID {pid}).")
        except:
            print("Error: Another instance is already running.")
        sys.exit(1)

def release_lock():
    """Release the lock on exit."""
    global _lock_file_handle
    if _lock_file_handle:
        try:
            fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_UN)
            _lock_file_handle.close()
        except Exception:
            pass
        _lock_file_handle = None
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)

def start_engine(config: ConfigParser):
    """
    The core logic of the ingest engine.
    This function is designed to be called from main() or from the GUI.
    """
    paths = config['Paths']
    watch_path = os.path.expanduser(paths['watch'])
    log_path = os.path.expanduser(paths['logs'])
    processing_path = os.path.expanduser(paths.get('processing', ''))
    queue_file_path = os.path.expanduser(paths.get('queue_file', 'queue.json'))

    # --- Setup Logging ---
    log_file = os.path.join(log_path, 'ingest_engine.log')
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ],
        force=True
    )
    logging.info("--- Starting Transcoder ---")

    # --- Start API Server ---
    api_port = int(config.get('API', 'port', fallback='8081'))
    api_host = config.get('API', 'host', fallback='0.0.0.0')
    base_dir = os.path.dirname(os.path.abspath(__file__))
    start_api_server(config, base_dir, host=api_host, port=api_port)

    # --- Create Folders ---
    for key in ['processing', 'temp', 'output', 'processed', 'error']:
        p = os.path.expanduser(paths.get(key, ''))
        if p and not os.path.exists(p):
            os.makedirs(p)
            logging.info(f"Created folder: {p}")

    # --- Recover stuck files ---
    if processing_path and os.path.isdir(processing_path):
        pass

    # --- Initialize Status & Queue Files ---
    status_path = os.path.expanduser(config['Paths']['status_file'])
    update_status(status_path, {"status": "idle", "file": "None", "progress": 0, "stage": "Idle"})
    with open(queue_file_path, 'w') as f:
        json.dump([], f)

    # --- Initialize Pause Control File ---
    global _pause_control_path
    _pause_control_path = os.path.expanduser(paths.get('pause_file', os.path.join(os.path.dirname(status_path), 'pause_control.json')))
    with open(_pause_control_path, 'w') as f:
        json.dump({"paused": False, "pause_requested": False}, f)
    logging.info(f"Pause control file: {_pause_control_path}")

    # --- Check for tool paths ---
    art_cli_path = os.path.expanduser(paths['art_cli'])
    if not os.path.exists(art_cli_path):
        logging.warning(f"ARRI CLI not found at '{art_cli_path}'. Verify path in config.ini.")

    # --- Start Worker and Polling ---
    if not os.path.exists(watch_path):
        logging.error(f"Watch folder not found at '{watch_path}'.")
        return

    processing_queue = queue.Queue()
    worker_thread = threading.Thread(target=worker, args=(processing_queue, config), daemon=True)
    worker_thread.start()

    # --- Manual file list mode (from GUI) ---
    file_list_raw = os.environ.get("TEN2_FILE_LIST", "")
    if file_list_raw:
        try:
            file_list = json.loads(file_list_raw)
        except Exception:
            file_list = []
        if isinstance(file_list, list) and file_list:
            logging.info(f"Processing {len(file_list)} selected file(s).")
            for file_path in file_list:
                if os.path.isfile(file_path):
                    enqueue_file(file_path, processing_queue, "manual")
                else:
                    logging.warning(f"Selected file not found: {file_path}")

            while not processing_queue.empty():
                try:
                    queue_snapshot = list(processing_queue.queue)
                    with open(queue_file_path, 'w') as f:
                        json.dump(queue_snapshot, f)
                except Exception as e:
                    logging.error(f"Failed to write queue status file: {e}")
                time.sleep(1)

            processing_queue.join()
            try:
                with open(queue_file_path, 'w') as f:
                    json.dump([], f)
            except Exception as e:
                logging.error(f"Failed to clear queue status file: {e}")
            try:
                with queued_files_lock:
                    queued_files.clear()
            except Exception as e:
                logging.error(f"Failed to clear queued files set: {e}")
            update_status(status_path, {"status": "idle", "file": "None", "progress": 0, "stage": "Idle"})
            logging.info("--- Manual file list complete ---")
            return

    event_handler = IngestEventHandler(config, processing_queue)
    logging.info(f"Monitoring folder: {watch_path} (polling every 5 seconds)")

    try:
        while True:
            scan_watch_folder(event_handler, watch_path)
            
            # Write queue contents to file for GUI
            try:
                queue_snapshot = list(processing_queue.queue)
                with open(queue_file_path, 'w') as f:
                    json.dump(queue_snapshot, f)
            except Exception as e:
                logging.error(f"Failed to write queue status file: {e}")

            time.sleep(5)  # Poll every 5 seconds
    except KeyboardInterrupt:
        logging.info("--- Stopping Transcoder ---")
    finally:
        processing_queue.put(None) # Signal worker to stop
        processing_queue.join()
        logging.shutdown()


def main(path_overrides=None):
    """
    Entry point: parses args, loads config, and starts the engine.
    """
    acquire_lock()

    # CLI arguments can still be used if the app is not run from the GUI
    parser = argparse.ArgumentParser(description="10-2 Transcoder.")
    parser.add_argument("--watch", help="Override the watch folder path.")
    parser.add_argument("--output", help="Override the output folder path.")
    args = parser.parse_args()

    config = ConfigParser()
    config.read('config.ini')

    # Build a dictionary of overrides from CLI args
    cli_overrides = {}
    if args.watch:
        cli_overrides['watch'] = args.watch
    if args.output:
        cli_overrides['output'] = args.output

    # The GUI provides a complete dictionary, which takes precedence
    final_overrides = path_overrides or cli_overrides

    # Update the config object with all provided overrides
    if final_overrides:
        for key, value in final_overrides.items():
            config.set('Paths', key, value)

    start_engine(config)


if __name__ == "__main__":
    main()
