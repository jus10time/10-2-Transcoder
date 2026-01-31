import os
import sys
import time
import logging
import atexit
import shutil
import fcntl
from configparser import ConfigParser
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import json
import threading
import queue
from processor import process_clip, update_status
from api_server import start_api_server

LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.ingest_engine.lock')
# Keep lock file handle open to maintain the lock
_lock_file_handle = None

# Global queue for files to be processed
file_queue = queue.Queue()

# Track files that have been queued to prevent duplicates
# Once a file is added, it stays in the set forever (until engine restart)
queued_files = set()
queued_files_lock = threading.Lock()

# Moved outside class for broader use
def wait_for_file_to_stabilize(file_path: str, delay: int = 5):
    """
    Waits for the file to stop changing size, indicating it's fully copied.
    """
    logging.info(f"Waiting for {os.path.basename(file_path)} to stabilize...")
    last_size = -1
    attempt = 0
    max_attempts = 10 # Wait up to 10 * delay seconds
    while attempt < max_attempts:
        try:
            current_size = os.path.getsize(file_path)
            if current_size == last_size and current_size > 0:
                logging.info(f"File {os.path.basename(file_path)} has stabilized. Ready for processing.")
                return True
            else:
                last_size = current_size
                attempt += 1
                time.sleep(delay)
        except FileNotFoundError:
            logging.warning(f"File {file_path} disappeared during stabilization check.")
            return False # Indicate file was not stabilized or disappeared
        except Exception as e:
            logging.error(f"Error checking file stability for {file_path}: {e}")
            return False # Indicate error during stabilization
    logging.warning(f"File {file_path} did not stabilize after {max_attempts * delay} seconds.")
    return False # Indicate timeout

WORKER_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.worker.lock')

def worker(q: queue.Queue, config: ConfigParser):
    """Worker thread function to process files from the queue."""
    # Get processing folder path
    processing_folder = os.path.expanduser(config['Paths'].get('processing', ''))

    while True:
        original_path = q.get() # Blocks until an item is available
        file_path = original_path  # May change if we move to processing folder
        filename = os.path.basename(file_path)  # For deduplication tracking

        # Acquire file-based lock to ensure only one worker processes at a time
        # This protects against multiple processes or threads
        try:
            worker_lock = open(WORKER_LOCK_FILE, 'w')
            fcntl.flock(worker_lock.fileno(), fcntl.LOCK_EX)  # Blocking exclusive lock
        except Exception as e:
            logging.error(f"Failed to acquire worker lock: {e}")
            q.task_done()
            continue

        logging.info(f"Worker processing file from queue: {file_path}")

        # Initial delay to let file copy complete before checking
        logging.info(f"Waiting 10 seconds for file copy to complete...")
        time.sleep(10)

        # Perform stabilization and existence check here, just before processing
        if not wait_for_file_to_stabilize(file_path):
            logging.warning(f"Skipping {file_path}: It was not stabilized or disappeared.")
            # Never remove from queued_files - file is permanently marked as seen
            try:
                fcntl.flock(worker_lock.fileno(), fcntl.LOCK_UN)
                worker_lock.close()
            except:
                pass
            q.task_done()
            continue

        if not os.path.exists(file_path):
            logging.warning(f"Skipping {file_path}: File disappeared after stabilization.")
            # Never remove from queued_files - file is permanently marked as seen
            try:
                fcntl.flock(worker_lock.fileno(), fcntl.LOCK_UN)
                worker_lock.close()
            except:
                pass
            q.task_done()
            continue

        # IMMEDIATELY move file to processing folder to prevent re-detection by polling
        if processing_folder and os.path.isdir(processing_folder):
            try:
                new_path = os.path.join(processing_folder, filename)
                shutil.move(file_path, new_path)
                logging.info(f"Moved to processing folder: {new_path}")
                file_path = new_path  # Update to new location
            except Exception as e:
                logging.error(f"Failed to move to processing folder: {e}")
                # Continue with original path if move fails

        try:
            process_clip(file_path, config)
        except Exception as e:
            logging.error(f"Error processing {file_path} from worker: {e}")
        finally:
            # Never remove from queued_files - file is permanently marked as seen
            # Release the worker lock
            try:
                fcntl.flock(worker_lock.fileno(), fcntl.LOCK_UN)
                worker_lock.close()
            except:
                pass
            q.task_done()

def enqueue_file(file_path: str, file_queue: queue.Queue, source: str):
    """Safely add a file to the processing queue, preventing duplicates.

    Once a file is added, it can never be added again (until engine restart).
    This prevents all forms of duplicate processing.
    """
    filename = os.path.basename(file_path)

    with queued_files_lock:
        if filename in queued_files:
            # Already queued - never add again
            return False
        queued_files.add(filename)

    logging.info(f"New file detected by {source}: {file_path}")
    file_queue.put(file_path)
    return True


class IngestEventHandler(FileSystemEventHandler):
    """
    Handles file system events for the ingest engine.
    """
    def __init__(self, config: ConfigParser, file_queue: queue.Queue):
        super().__init__()
        self.config = config
        self.file_queue = file_queue # Store the queue
        self.processing_extensions = config.get('Processing', 'allowed_extensions').split(',')
        self.last_seen_files = set() # Initialize for polling

    def on_created(self, event):
        """
        Called when a file or directory is created.
        """
        if event.is_directory:
            return

        # Check if the file extension is one we should process
        _, ext = os.path.splitext(event.src_path)
        if ext.lower() not in self.processing_extensions:
            logging.debug(f"Ignoring file with unallowed extension: {event.src_path}")
            return

        enqueue_file(event.src_path, self.file_queue, "watchdog")

    def on_moved(self, event):
        """
        Called when a file or directory is moved or renamed.
        """
        if event.is_directory:
            return

        # The destination path is the new location of the file
        file_path = event.dest_path

        # Check if the file extension is one we should process
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in self.processing_extensions:
            logging.debug(f"Ignoring moved file with unallowed extension: {file_path}")
            return

        enqueue_file(file_path, self.file_queue, "watchdog")

def scan_watch_folder(handler: IngestEventHandler, watch_path: str):
    """Scans the watch folder for new files and adds them to the queue."""
    try:
        current_files = set(os.listdir(watch_path))
        for file_name in current_files:
            # Only process files we haven't seen in the last scan
            if file_name not in handler.last_seen_files:
                full_path = os.path.join(watch_path, file_name)
                if os.path.isfile(full_path):
                    _, ext = os.path.splitext(file_name)
                    if ext.lower() in handler.processing_extensions:
                        # Polling detects a new file, add it to the queue
                        enqueue_file(full_path, handler.file_queue, "polling")
        handler.last_seen_files = current_files
    except Exception as e:
        logging.error(f"Error during folder scan: {e}")

def acquire_lock():
    """Prevent multiple instances using atomic file locking (fcntl.flock)."""
    global _lock_file_handle

    try:
        # Open (or create) the lock file - keep handle open to maintain lock
        _lock_file_handle = open(LOCK_FILE, 'w')

        # Try to acquire an exclusive lock (non-blocking)
        fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # We got the lock - write our PID
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()

        # Register cleanup on exit
        atexit.register(release_lock)

    except (IOError, OSError) as e:
        # Could not acquire lock - another instance is running
        # Try to read the PID from the lock file for a helpful message
        try:
            with open(LOCK_FILE, 'r') as f:
                existing_pid = f.read().strip()
            print(f"Error: Another instance of Ingest Engine is already running (PID {existing_pid}).")
        except:
            print("Error: Another instance of Ingest Engine is already running.")
        print(f"If this is incorrect, delete the lock file: {LOCK_FILE}")
        sys.exit(1)


def release_lock():
    """Release the lock and remove the lock file on exit."""
    global _lock_file_handle
    try:
        if _lock_file_handle:
            fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_UN)
            _lock_file_handle.close()
            _lock_file_handle = None
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def main():
    """
    Main function to start the ingest engine.
    """
    # --- Prevent multiple instances ---
    acquire_lock()

    # --- Read Configuration ---
    config_path = 'config.ini'
    if not os.path.exists(config_path):
        print(f"Error: Configuration file '{config_path}' not found. Please create it.")
        return
        
    config = ConfigParser()
    config.read(config_path)

    paths = config['Paths']
    watch_path = os.path.expanduser(paths['watch'])
    log_path = os.path.expanduser(paths['logs'])
    processing_path = os.path.expanduser(paths.get('processing', ''))

    # --- Setup Logging ---
    log_file = os.path.join(log_path, 'ingest_engine.log')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    logging.info("--- Starting Ingest Engine ---")

    # --- Start API Server ---
    api_port = int(config.get('API', 'port', fallback='8080'))
    api_host = config.get('API', 'host', fallback='0.0.0.0')
    base_dir = os.path.dirname(os.path.abspath(__file__))
    start_api_server(config, base_dir, host=api_host, port=api_port)

    # --- Create processing folder if needed ---
    if processing_path and not os.path.exists(processing_path):
        os.makedirs(processing_path)
        logging.info(f"Created processing folder: {processing_path}")

    # --- Recover files stuck in processing folder from previous crash ---
    if processing_path and os.path.isdir(processing_path):
        processing_extensions = config.get('Processing', 'allowed_extensions').split(',')
        for filename in os.listdir(processing_path):
            if filename.startswith('.'):
                continue
            _, ext = os.path.splitext(filename)
            if ext.lower() in processing_extensions:
                stuck_file = os.path.join(processing_path, filename)
                recovery_dest = os.path.join(watch_path, filename)
                try:
                    shutil.move(stuck_file, recovery_dest)
                    logging.info(f"Recovered stuck file from processing folder: {filename}")
                except Exception as e:
                    logging.error(f"Failed to recover stuck file {filename}: {e}")

    # --- Clean up any leftover temp files ---
    temp_path = os.path.expanduser(paths.get('temp', ''))
    if temp_path and os.path.isdir(temp_path):
        for filename in os.listdir(temp_path):
            if filename.endswith('_BAKED.mxf'):
                leftover = os.path.join(temp_path, filename)
                try:
                    os.remove(leftover)
                    logging.info(f"Cleaned up leftover temp file: {filename}")
                except Exception as e:
                    logging.error(f"Failed to clean up temp file {filename}: {e}")

    # --- Initialize Status File ---
    status_path = os.path.expanduser(config['Paths']['status_file'])
    update_status(status_path, {"status": "idle", "file": "None", "progress": 0, "stage": "Idle"})

    # --- Check for tool paths ---
    art_cli_path = os.path.expanduser(paths['art_cli'])
    if not os.path.exists(art_cli_path):
        logging.warning(f"ARRI CLI not found at '{art_cli_path}'. Please verify the path in config.ini.")
        # Depending on strictness, you might want to exit here.
        # For now, we'll let it fail during processing.

    # --- Start Watchdog Observer ---
    if not os.path.exists(watch_path):
        logging.error(f"Watch folder not found at '{watch_path}'. Please create it or check the path in config.ini.")
        return

    # Initialize queue and worker thread (SINGLE worker only)
    processing_queue = queue.Queue()
    worker_thread = threading.Thread(target=worker, args=(processing_queue, config), daemon=True)
    worker_thread.start()

    event_handler = IngestEventHandler(config, processing_queue)

    # DISABLED watchdog - only use polling to avoid race conditions
    # observer = Observer()
    # observer.schedule(event_handler, watch_path, recursive=False)
    # observer.start()

    logging.info(f"Monitoring folder: {watch_path} (polling only, every 15 seconds)")

    try:
        while True:
            # Only use polling - no watchdog
            scan_watch_folder(event_handler, watch_path)
            time.sleep(15)  # Poll every 15 seconds to let files settle
    except KeyboardInterrupt:
        logging.info("--- Stopping Ingest Engine ---")
        logging.shutdown()

    processing_queue.join()  # Wait for all tasks to be done

if __name__ == "__main__":
    main()