#!/bin/bash
#
# Start Transcoder v2
# The web dashboard runs on productiondev.am.com:3030 (Docker)
#

# Check for any existing ingest engine processes
EXISTING=$(pgrep -f "python.*main.py" | wc -l | tr -d ' ')
if [ "$EXISTING" -gt 0 ]; then
    echo "ERROR: $EXISTING ingest engine process(es) already running!"
    echo "Run ~/stop_ingest_engine.sh first to stop existing processes."
    pgrep -af "python.*main.py"
    exit 1
fi

# Check if port 8080 is in use
if lsof -ti:8080 > /dev/null 2>&1; then
    echo "WARNING: Port 8080 is in use. Attempting to free it..."
    lsof -ti:8080 | xargs kill -9 2>/dev/null
    sleep 1
fi

echo "Starting Transcoder..."
cd "/Users/admin/ingest_engine_v2" || exit

# Clean up any stale lock files
rm -f .ingest_engine.lock
rm -f .worker.lock

# Ensure log directory exists
mkdir -p logs

# Start the engine
nohup python3 main.py >> logs/ingest_engine.log 2>&1 &
INGEST_PID=$!
echo "Transcoder PID: $INGEST_PID"

# Wait and verify it started successfully
sleep 2
if ! ps -p $INGEST_PID > /dev/null 2>&1; then
    echo "ERROR: Transcoder failed to start!"
    echo "Last 20 lines of log:"
    tail -20 logs/ingest_engine.log
    exit 1
fi

# Verify API is responding
sleep 1
if curl -s http://localhost:8080/api/health | grep -q "ok"; then
    echo "Transcoder started successfully."
    echo ""
    echo "API: http://localhost:8080"
    echo "Dashboard: http://productiondev.am.com:3030"
    echo "Logs: tail -f ~/ingest_engine_v2/logs/ingest_engine.log"
else
    echo "WARNING: Engine started but API not responding yet."
    echo "Check logs: tail -f ~/ingest_engine_v2/logs/ingest_engine.log"
fi
