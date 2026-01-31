#!/bin/bash
#
# Stop Ingest Engine v2
#

echo "Stopping Ingest Engine..."

# First try graceful shutdown
pkill -f "python.*main.py"
sleep 1

# Force kill any remaining processes
pkill -9 -f "python.*main.py" 2>/dev/null

# Double-check and report
REMAINING=$(pgrep -f "python.*main.py" | wc -l | tr -d ' ')
if [ "$REMAINING" -gt 0 ]; then
    echo "WARNING: $REMAINING process(es) still running!"
    pgrep -af "python.*main.py"
else
    echo "Ingest Engine stopped."
fi

# Clean up lock files
rm -f /Users/admin/ingest_engine_v2/.ingest_engine.lock
rm -f /Users/admin/ingest_engine_v2/.worker.lock

# Clean up temp folder if accessible
TEMP_PATH="/Volumes/QNAP1_RAW_Footage/ingest_engine_v2/temp_folder"
if [ -d "$TEMP_PATH" ]; then
    rm -f "$TEMP_PATH"/*_BAKED.mxf 2>/dev/null
    echo "Cleaned up temp files."
fi

echo "Done."
