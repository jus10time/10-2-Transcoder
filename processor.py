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

CAMERA_FAMILIES = [
    "ARRI Alexa 35",
    "ARRI Alexa Mini",
    "ARRI Amira",
    "ARRI Alexa",
    "Sony FX6",
    "Sony FX3",
    "Sony A7S",
    "Sony",
    "DJI Video",
    "Unknown",
]

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


def _load_lut_selection():
    raw = os.environ.get("TEN2_LUT_SELECTION", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _ffprobe_tags(file_path: str, ffprobe_path: str):
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
    tags.update(data.get("format", {}).get("tags", {}) or {})
    for stream in data.get("streams", []) or []:
        tags.update(stream.get("tags", {}) or {})
    return tags


def _tag_text(tags: dict):
    if not tags:
        return ""
    parts = []
    for k, v in tags.items():
        parts.append(str(k))
        parts.append(str(v))
    return " ".join(parts).upper()


def _detect_camera_family(file_path: str, ffprobe_path: str):
    tags = _ffprobe_tags(file_path, ffprobe_path)
    combined = _tag_text(tags)
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
    if ext in (".mp4", ".mov"):
        if "DJI" in filename:
            return "DJI Video"
        if "SONY" in filename or "FX6" in filename or "FX3" in filename or "A7S" in filename:
            return "Sony"
    return "Unknown"


def _should_use_art(camera_family: str, art_cli_path: str, file_path: str):
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".mov", ".mp4", ".m4v"):
        return False
    if camera_family == "ARRI Alexa 35":
        return True
    if camera_family in ("ARRI Alexa Mini", "ARRI Amira", "ARRI Alexa"):
        return bool(art_cli_path and os.path.exists(art_cli_path))
    return False


def _get_lut_for_camera(camera_family: str, config: ConfigParser):
    lut_map = _load_lut_selection()
    lut_path = lut_map.get(camera_family)
    if lut_path and os.path.exists(lut_path):
        return lut_path
    return None


_FILTER_CACHE = {}


def _escape_lut_path(path: str):
    return path.replace("\\", "\\\\").replace(":", "\\:")


def _build_vf_chain(lut_path: str | None, preset_vf: str | None, pre_vf: str | None = None):
    filters = []
    if pre_vf:
        filters.append(pre_vf)
    if lut_path:
        filters.append(f"lut3d=file={_escape_lut_path(lut_path)}")
    if preset_vf:
        filters.append(preset_vf)
    return ",".join(filters)


def _ffmpeg_supports_filter(ffmpeg_path: str, filter_name: str) -> bool:
    cache_key = (ffmpeg_path, filter_name)
    if cache_key in _FILTER_CACHE:
        return _FILTER_CACHE[cache_key]
    try:
        output = subprocess.check_output([ffmpeg_path, "-hide_banner", "-filters"], text=True)
        supported = f" {filter_name} " in output
    except Exception:
        supported = False
    _FILTER_CACHE[cache_key] = supported
    return supported


def _ffprobe_pix_fmt(file_path: str, ffprobe_path: str) -> str | None:
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return out or None
    except Exception:
        return None


def _validate_media_readable(file_path: str, ffprobe_path: str) -> tuple[bool, str]:
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        msg = stderr or stdout or "ffprobe failed to read input"
        return False, msg


def _get_output_preset(config: ConfigParser):
    preset_name = os.environ.get("TEN2_OUTPUT_PRESET") or config.get('Output', 'default_preset', fallback='DNxHD_145')
    section = f"Preset.{preset_name}"
    if not config.has_section(section):
        preset_name = config.get('Output', 'default_preset', fallback='DNxHD_145')
        section = f"Preset.{preset_name}"
    preset = {
        "name": preset_name,
        "container": config.get(section, 'container', fallback='mxf'),
        "vcodec": config.get(section, 'vcodec', fallback='dnxhd'),
        "video_bitrate": config.get(section, 'video_bitrate', fallback=''),
        "vf": config.get(section, 'vf', fallback=''),
        "profile": config.get(section, 'profile', fallback=''),
        "crf": config.get(section, 'crf', fallback=''),
        "preset": config.get(section, 'preset', fallback=''),
        "acodec": config.get(section, 'acodec', fallback='pcm_s24le'),
        "audio_bitrate": config.get(section, 'audio_bitrate', fallback=''),
        "audio_rate": config.get(section, 'audio_rate', fallback='48000'),
    }
    return preset


def _build_ffmpeg_cmd(ffmpeg_path: str, input_path: str, output_path: str, preset: dict, vf_chain: str | None):
    cmd = [ffmpeg_path, "-i", input_path]
    cmd += ["-c:v", preset["vcodec"]]

    if preset["video_bitrate"]:
        cmd += ["-b:v", preset["video_bitrate"]]
    if preset["profile"]:
        cmd += ["-profile:v", str(preset["profile"])]
    if preset["crf"]:
        cmd += ["-crf", str(preset["crf"])]
    if preset["preset"]:
        cmd += ["-preset", preset["preset"]]
    if vf_chain:
        cmd += ["-vf", vf_chain]

    cmd += ["-c:a", preset["acodec"]]
    if preset["audio_bitrate"]:
        cmd += ["-b:a", preset["audio_bitrate"]]
    if preset["audio_rate"]:
        cmd += ["-ar", str(preset["audio_rate"])]

    cmd += ["-f", preset["container"], "-y", output_path]
    return cmd

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
        ffprobe_path = ffmpeg_path.replace('ffmpeg', 'ffprobe')

        temp_folder = os.path.expanduser(paths['temp'])
        output_folder = os.path.expanduser(paths['output'])
        processed_folder = os.path.expanduser(paths['processed'])

        # --- Pre-flight checks ---
        if not os.path.exists(source_path):
            raise RuntimeError(f"Source file not found or network share unavailable: {source_path}")

        if not os.path.isdir(temp_folder):
            raise RuntimeError(f"Temp folder not found: {temp_folder}")

        if not os.path.isdir(output_folder):
            raise RuntimeError(f"Output folder not found: {output_folder}")

        # --- Validate input is readable ---
        ok, info = _validate_media_readable(source_path, ffprobe_path)
        if not ok:
            raise RuntimeError(f"Invalid or incomplete media file: {info}")

        # --- Detect camera family and LUT ---
        camera_family = _detect_camera_family(source_path, ffprobe_path)
        logging.info(f"Detected camera: {camera_family}")
        use_art = _should_use_art(camera_family, art_cli_path, source_path)
        if camera_family.startswith("ARRI") and not use_art:
            ext = os.path.splitext(source_path)[1].lower()
            if ext in (".mov", ".mp4", ".m4v"):
                logging.info("ART CLI skipped for MOV/MP4 container; using LUT/FFmpeg pipeline.")
        embed_lut = os.environ.get("TEN2_EMBED_LUT", "1") != "0"
        skip_lut_env = os.environ.get("TEN2_SKIP_LUT_CAMERAS", "")
        skip_lut = {c.strip() for c in skip_lut_env.split(";") if c.strip()}
        lut_path = None
        if embed_lut and not use_art and camera_family not in skip_lut:
            lut_path = _get_lut_for_camera(camera_family, config)
        if not use_art and lut_path is None:
            logging.warning(f"No LUT found for {camera_family}. Proceeding without LUT.")

        if use_art and not os.path.exists(art_cli_path):
            raise RuntimeError(f"ARRI Reference Tool (art-cmd) not found at: {art_cli_path}")

        # --- Output preset ---
        preset = _get_output_preset(config)
        container_ext = preset["container"]

        # --- Define file paths ---
        intermediate_path = os.path.join(temp_folder, f"{os.path.splitext(filename)[0]}_BAKED.mxf")
        final_output_path = os.path.join(output_folder, f"{os.path.splitext(filename)[0]}.{container_ext}")
        
        if use_art:
            # --- 1. Run ARRI CLI ---
            logging.info("Step 1: Baking ARRI Look with ART CLI...")
            update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "ARRI Processing", "elapsed": 0})
            use_target_colorspace = settings.get("art_use_target_colorspace", "true").strip().lower() in ("1", "true", "yes", "on")
            base_art_cmd = [
                art_cli_path,
                "process",
                "--input", source_path,
                "--output", intermediate_path,
                "--embedded-look",
                "--video-codec", "prores422"
            ]
            if use_target_colorspace:
                base_art_cmd += ["--target-colorspace", settings['art_colorspace']]

            def _run_art(cmd: list[str]) -> tuple[int, str, str, float]:
                logging.info(f"Running ART CLI: {' '.join(cmd)}")
                art_start_time = time.time()
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

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

                stdout, stderr = process.communicate()
                sys.stdout.write('\n')
                art_elapsed = time.time() - art_start_time
                return process.returncode, stdout or "", stderr or "", art_elapsed

            # Run ART CLI with progress updates showing elapsed time
            rc, stdout, stderr, art_elapsed = _run_art(base_art_cmd)

            if rc != 0:
                # Fallback for newer ART behavior: target-colorspace only valid with DRT LUTs
                err_text = (stdout + "\n" + stderr).lower()
                if "target-colorspace argument is only valid for embedded looks with drt luts" in err_text:
                    logging.warning("ART CLI rejected --target-colorspace for this embedded look; retrying without it.")
                    fallback_cmd = [arg for arg in base_art_cmd if arg not in ("--target-colorspace", settings['art_colorspace'])]
                    rc, stdout, stderr, art_elapsed = _run_art(fallback_cmd)

            if rc != 0:
                logging.error(f"ART CLI failed with exit code {rc}")
                if stdout:
                    logging.error(f"ART CLI stdout: {stdout}")
                if stderr:
                    logging.error(f"ART CLI stderr: {stderr}")
                # Check if intermediate file was partially created
                if os.path.exists(intermediate_path):
                    partial_size = os.path.getsize(intermediate_path)
                    logging.error(f"Partial intermediate file exists ({partial_size} bytes) - cleaning up")
                    os.remove(intermediate_path)
                raise subprocess.CalledProcessError(rc, base_art_cmd, stdout, stderr)

            logging.info(f"ART CLI finished successfully in {str(timedelta(seconds=int(art_elapsed)))}")
            if stdout:
                logging.info(f"ART CLI stdout:\n{stdout}")
            if stderr:
                logging.warning(f"ART CLI stderr:\n{stderr}")

            # --- 2. Get video duration for progress calculation ---
            logging.info("Step 2: Analyzing intermediate file...")
            update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "Analyzing"})
            ffprobe_cmd = [
                ffprobe_path,
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
            logging.info("Step 3: Transcoding with FFmpeg...")
            update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "FFmpeg Transcoding"})
            pix_fmt = _ffprobe_pix_fmt(intermediate_path, ffprobe_path)
            pre_vf = None
            if pix_fmt and (pix_fmt.startswith("yuv444p") or "gbr" in pix_fmt):
                if _ffmpeg_supports_filter(ffmpeg_path, "zscale"):
                    pre_vf = "zscale=primaries=bt709:transfer=bt709:matrix=bt709,format=yuv422p"
                elif _ffmpeg_supports_filter(ffmpeg_path, "colorspace"):
                    pre_vf = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709,colorspace=all=bt709,format=yuv422p"
                else:
                    logging.warning("No colorspace filter available for 4444 input; attempting direct conversion.")
            vf_chain = _build_vf_chain(None, preset.get("vf") or "", pre_vf=pre_vf)
            ffmpeg_cmd = _build_ffmpeg_cmd(ffmpeg_path, intermediate_path, final_output_path, preset, vf_chain)
        else:
            # --- Direct FFmpeg transcode (optional LUT) ---
            logging.info("Step 1: Analyzing source file...")
            update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": "Analyzing"})
            ffprobe_cmd = [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                source_path
            ]
            try:
                duration_str = subprocess.check_output(ffprobe_cmd, text=True).strip()
                total_duration = float(duration_str)
                logging.info(f"Total duration to process: {total_duration:.2f}s")
            except (subprocess.CalledProcessError, ValueError) as e:
                logging.error(f"Failed to get video duration from source file: {e}")
                total_duration = 0

            logging.info("Step 2: Transcoding with FFmpeg...")
            stage_name = "FFmpeg Transcoding"
            if lut_path:
                stage_name = "Applying LUT + Transcoding"
            update_status(status_path, {"status": "processing", "file": filename, "progress": 0, "stage": stage_name})
            pix_fmt = _ffprobe_pix_fmt(source_path, ffprobe_path)
            pre_vf = None
            if pix_fmt and (pix_fmt.startswith("yuv444p") or "gbr" in pix_fmt):
                if _ffmpeg_supports_filter(ffmpeg_path, "zscale"):
                    pre_vf = "zscale=primaries=bt709:transfer=bt709:matrix=bt709,format=yuv422p"
                elif _ffmpeg_supports_filter(ffmpeg_path, "colorspace"):
                    pre_vf = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709,colorspace=all=bt709,format=yuv422p"
                else:
                    logging.warning("No colorspace filter available for 4444 input; attempting direct conversion.")
            vf_chain = _build_vf_chain(lut_path, preset.get("vf") or "", pre_vf=pre_vf)
            ffmpeg_cmd = _build_ffmpeg_cmd(ffmpeg_path, source_path, final_output_path, preset, vf_chain)
        
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
        if use_art and os.path.exists(intermediate_path):
            os.remove(intermediate_path)
            logging.info(f"Removed intermediate file: {intermediate_path}")

        # --- 5. Complete (source file stays in place) ---
        logging.info("Step 5: Processing complete (source file unchanged)")
        update_status(status_path, {"status": "processing", "file": filename, "progress": 100, "stage": "Complete"})

        logging.info(f"--- Successfully processed {filename}. Final file at: {final_output_path} ---")
        processing_status = "succeeded" # Set success status


    except subprocess.CalledProcessError as e:
        # Include both stdout and stderr since some tools write errors to stdout
        stderr_output = e.stderr.strip() if e.stderr else ""
        stdout_output = e.stdout.strip() if e.stdout else ""
        output_info = stderr_output or stdout_output or "(no output captured)"
        error_details = f"Command '{' '.join(e.cmd)}' returned non-zero exit status {e.returncode}. Output: {output_info}"
        logging.error(f"An error occurred while processing {source_path}.")
        logging.error(error_details)
        update_status(status_path, {"status": "error", "file": filename, "progress": 0, "stage": "Error"})
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
        update_status(status_path, {"status": "error", "file": filename, "progress": 0, "stage": "Error"})
    finally:
        end_time = datetime.now()
        history_record = {
            "file": filename,
            "source_path": source_path,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "status": processing_status,
            "error_details": error_details
        }
        log_to_history(history_path, history_record)
        update_status(status_path, {"status": "idle", "file": "None", "progress": 0, "stage": "Idle"})
