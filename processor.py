import os
import subprocess
import logging
import shutil
import re
import sys
import json
import time
import threading
from datetime import timedelta, datetime
from configparser import ConfigParser

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

def update_status(status_path, status_data):
    """Safely writes status data to a JSON file."""
    try:
        with open(status_path, 'w') as f:
            json.dump(status_data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to write status file {status_path}: {e}")

def log_to_history(history_path, record):
    """Appends a new record to the history JSON file."""
    try:
        history = []
        if os.path.exists(history_path):
            with open(history_path, 'r') as f:
                history = json.load(f)
        
        # Add new record to the top of the list
        history.insert(0, record)
        
        # Keep history to a reasonable size, e.g., 100 entries
        if len(history) > 100:
            history = history[:100]
            
        with open(history_path, 'w') as f:
            json.dump(history, f, indent=4)
            
    except Exception as e:
        logging.error(f"Failed to write to history file {history_path}: {e}")

def process_clip(source_path: str, config: ConfigParser):
    """
    Processes a single video file by applying ARRI look and transcoding to DNxHD.
    """
    status_path = os.path.expanduser(config['Paths']['status_file'])
    history_path = os.path.expanduser(config['Paths']['history_file'])
    
    start_time = datetime.now()
    processing_status = "failed" # Assume failure until proven otherwise
    error_details = ""

    filename = os.path.basename(source_path) # Define filename here for broader scope
    
    try:
        logging.info(f"--- Processing: {filename} ---")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "Starting"})


        # --- Read paths and settings from config ---
        paths = config['Paths']
        settings = config['Settings']

        art_cli_path = os.path.expanduser(paths['art_cli'])
        ffmpeg_path = os.path.expanduser(paths['ffmpeg'])

        temp_folder = os.path.expanduser(paths['temp'])
        output_folder = os.path.expanduser(paths['output'])
        processed_folder = os.path.expanduser(paths['processed'])

        # --- Pre-flight checks ---
        if not os.path.exists(source_path):
            raise RuntimeError(f"Source file not found or network share unavailable: {source_path}")

        if not os.path.exists(art_cli_path):
            raise RuntimeError(f"ARRI Reference Tool (art-cmd) not found at: {art_cli_path}")

        if not os.path.isdir(temp_folder):
            raise RuntimeError(f"Temp folder not found: {temp_folder}")

        if not os.path.isdir(output_folder):
            raise RuntimeError(f"Output folder not found: {output_folder}")

        # --- Define file paths ---
        intermediate_path = os.path.join(temp_folder, f"{os.path.splitext(filename)[0]}_BAKED.mxf")
        final_avid_path = os.path.join(output_folder, f"{os.path.splitext(filename)[0]}.mxf")
        
        # --- 1. Run ARRI CLI ---
        logging.info("Step 1: Baking ARRI Look with ART CLI...")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "ARRI Processing", "elapsed": 0})
        art_cmd = [
            art_cli_path,
            "process",
            "--input", source_path,
            "--output", intermediate_path,
            "--embedded-look",
            "--target-colorspace", settings['art_colorspace'],
            "--video-codec", "prores422"
        ]
        logging.info(f"Running ART CLI: {' '.join(art_cmd)}")

        # Run ART CLI with progress updates showing elapsed time
        art_start_time = time.time()
        process = subprocess.Popen(art_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Update status while ART is processing
        while process.poll() is None:
            elapsed = time.time() - art_start_time
            update_status(status_path, {
                "status": "processing",
                "file": filename,
                "progress": 0,
                "stage": "ARRI Processing",
                "elapsed": round(elapsed, 1)
            })
            # Print progress to console
            sys.stdout.write(f'\rARRI Processing: Elapsed {str(timedelta(seconds=int(elapsed)))}...')
            sys.stdout.flush()
            time.sleep(1)

        # Get the output
        stdout, stderr = process.communicate()
        sys.stdout.write('\n')
        art_elapsed = time.time() - art_start_time

        if process.returncode != 0:
            logging.error(f"ART CLI failed with exit code {process.returncode}")
            if stdout:
                logging.error(f"ART CLI stdout: {stdout}")
            if stderr:
                logging.error(f"ART CLI stderr: {stderr}")
            # Check if intermediate file was partially created
            if os.path.exists(intermediate_path):
                partial_size = os.path.getsize(intermediate_path)
                logging.error(f"Partial intermediate file exists ({partial_size} bytes) - cleaning up")
                os.remove(intermediate_path)
            raise subprocess.CalledProcessError(process.returncode, art_cmd, stdout, stderr)

        logging.info(f"ART CLI finished successfully in {str(timedelta(seconds=int(art_elapsed)))}")
        if stdout:
            logging.info(f"ART CLI stdout:\n{stdout}")
        if stderr:
            logging.warning(f"ART CLI stderr:\n{stderr}")



        # --- 2. Get video duration for progress calculation ---
        logging.info("Step 2: Analyzing intermediate file...")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "Analyzing"})
        ffprobe_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            intermediate_path
        ]
        try:
            duration_str = subprocess.check_output(ffprobe_cmd, text=True).strip()
            total_duration = float(duration_str)
            logging.info(f"Total duration to process: {total_duration:.2f}s")
        except (subprocess.CalledProcessError, ValueError) as e:
            logging.error(f"Failed to get video duration from intermediate file: {e}")
            total_duration = 0

        # --- 3. Run FFmpeg and monitor progress ---
        logging.info("Step 3: Transcoding to DNxHD with FFmpeg...")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "FFmpeg Transcoding"})
        ffmpeg_cmd = [
            ffmpeg_path,
            "-i", intermediate_path,
            "-c:v", "dnxhd",
            "-b:v", "145M",
            "-vf", "scale=1920:1080,format=yuv422p",
            "-c:a", "pcm_s24le",
            "-ar", "48000",
            "-f", "mxf",
            "-y",
            final_avid_path
        ]
        
        process = subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE, universal_newlines=True)

        time_regex = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})")
        
        for line in iter(process.stderr.readline, ''):
            match = time_regex.search(line)
            if match:
                elapsed_time_str = match.group(1)
                h, m, s = map(float, elapsed_time_str.split(':'))
                elapsed_seconds = h * 3600 + m * 60 + s
                
                if total_duration > 0:
                    percent = (elapsed_seconds / total_duration) * 100
                    bar = '█' * int(percent / 2) + '-' * (50 - int(percent / 2))
                    sys.stdout.write(f'\rProgress: [{bar}] {percent:.2f}% | Elapsed: {str(timedelta(seconds=int(elapsed_seconds)))} / {str(timedelta(seconds=int(total_duration)))}')
                    sys.stdout.flush()
                    update_status(status_path, {"status": "processing", "file": filename, "progress": round(percent, 2), "stage": "FFmpeg Transcoding", "elapsed": round(elapsed_seconds, 2), "total_duration": round(total_duration, 2)})
            else:
                logging.warning(f"[FFmpeg Warning]: {line.strip()}")

        process.wait()
        sys.stdout.write('\n')
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, ffmpeg_cmd, stderr="FFmpeg failed. See warnings above.")

        logging.info("FFmpeg finished successfully.")

        # --- 4. Cleanup ---
        logging.info("Step 4: Cleaning up intermediate file...")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 100, "stage": "Cleaning Up"})
        if os.path.exists(intermediate_path):
            os.remove(intermediate_path)
            logging.info(f"Removed intermediate file: {intermediate_path}")

        # --- 5. Move Processed Source File ---
        logging.info("Step 5: Archiving source file...")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 100, "stage": "Archiving"})
        processed_dest = os.path.join(processed_folder, filename)
        shutil.move(source_path, processed_dest)
        logging.info(f"Moved source file to: {processed_dest}")

        logging.info(f"--- Successfully processed {filename}. Final file at: {final_avid_path} ---")
        processing_status = "succeeded" # Set success status


    except subprocess.CalledProcessError as e:
        # Include both stdout and stderr since some tools write errors to stdout
        stderr_output = e.stderr.strip() if e.stderr else ""
        stdout_output = e.stdout.strip() if e.stdout else ""
        output_info = stderr_output or stdout_output or "(no output captured)"
        error_details = f"Command '{' '.join(e.cmd)}' returned non-zero exit status {e.returncode}. Output: {output_info}"
        logging.error(f"An error occurred while processing {source_path}.")
        logging.error(error_details)
        # Move the source file to the error folder to prevent reprocessing
        try:
            error_folder = os.path.expanduser(paths['error'])
            error_dest = os.path.join(error_folder, filename)
            if os.path.exists(source_path):
                shutil.move(source_path, error_dest)
                logging.info(f"Moved problematic source file to: {error_dest}")
            else:
                logging.warning(f"Source file already gone (moved by another process?): {source_path}")
        except Exception as move_e:
            logging.error(f"Failed to move source file to error folder: {move_e}")
    except FileNotFoundError as e:
        # Determine if this is a missing tool or a missing file
        missing_path = e.filename if e.filename else str(e)
        # Check if variables are defined (they might not be if error occurred early)
        tool_paths = []
        try:
            tool_paths = [art_cli_path, ffmpeg_path]
        except NameError:
            pass

        if missing_path and missing_path in tool_paths:
            error_details = f"CLI tool not found: {missing_path}. Check config.ini."
        elif missing_path and (missing_path == source_path or
                               'watch_folder' in str(missing_path) or
                               'processing_folder' in str(missing_path) or
                               missing_path.endswith('.mxf') or
                               missing_path.endswith('.mov')):
            error_details = f"Source file not found: {missing_path}. File may have been moved or deleted."
        else:
            error_details = f"File not found: {missing_path}"
        logging.error(error_details)
    except Exception as e:
        error_details = f"An unexpected error occurred while processing {source_path}: {e}"
        logging.error(error_details)
        # Move the source file to the error folder
        try:
            error_folder = os.path.expanduser(paths['error'])
            filename = os.path.basename(source_path)
            error_dest = os.path.join(error_folder, filename)
            if os.path.exists(source_path):
                shutil.move(source_path, error_dest)
                logging.info(f"Moved problematic source file to: {error_dest}")
            else:
                logging.warning(f"Source file already gone: {source_path}")
        except Exception as move_e:
            logging.error(f"Failed to move source file to error folder: {move_e}")
    finally:
        end_time = datetime.now()
        history_record = {
            "file": filename,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "status": processing_status,
            "error_details": error_details
        }
        log_to_history(history_path, history_record)
        update_status(status_path, {"status": "idle", "file": "None", "progress": 0, "stage": "Idle"})
