#!/bin/bash
#
# Ingest Engine v2 - New Machine Setup Script
#
# This script sets up the ingest engine on a new Mac.
# Run this after copying the ingest_engine_v2 folder to the new machine.
#
# Usage: ./setup_new_machine.sh
#

set -e

echo "=============================================="
echo "  Ingest Engine v2 - New Machine Setup"
echo "=============================================="
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# ============================================
# Step 1: Check Prerequisites
# ============================================
echo "Step 1: Checking prerequisites..."
echo ""

# Check Python 3
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    print_status "Python 3 found: $PYTHON_VERSION"
else
    print_error "Python 3 not found. Please install Python 3.9 or later."
    exit 1
fi

# Check pip
if command -v pip3 &> /dev/null; then
    print_status "pip3 found"
else
    print_error "pip3 not found. Please install pip."
    exit 1
fi

# Check FFmpeg
if command -v ffmpeg &> /dev/null; then
    FFMPEG_VERSION=$(ffmpeg -version | head -1)
    print_status "FFmpeg found: $FFMPEG_VERSION"
else
    print_warning "FFmpeg not found. Installing via Homebrew..."
    if command -v brew &> /dev/null; then
        brew install ffmpeg
        print_status "FFmpeg installed"
    else
        print_error "Homebrew not found. Please install FFmpeg manually."
        echo "  Install Homebrew: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "  Then run: brew install ffmpeg"
        exit 1
    fi
fi

# Check ARRI Reference Tool
ART_PATH="/Applications/art-cmd_1.0.0_macos_universal/bin/art-cmd"
if [ -f "$ART_PATH" ]; then
    print_status "ARRI Reference Tool found at $ART_PATH"
else
    print_warning "ARRI Reference Tool not found at $ART_PATH"
    echo ""
    echo "  Please download and install the ARRI Reference Tool (ART):"
    echo "  1. Go to: https://www.arri.com/en/learn-help/learn-help-camera-system/tools/arri-reference-tool"
    echo "  2. Download 'ARRI Reference Tool' for macOS"
    echo "  3. Install to /Applications/"
    echo "  4. Update the path in config.ini if different"
    echo ""
    read -p "Press Enter to continue anyway, or Ctrl+C to exit..."
fi

echo ""

# ============================================
# Step 2: Install Python Dependencies
# ============================================
echo "Step 2: Installing Python dependencies..."
echo ""

pip3 install --user watchdog

print_status "Python dependencies installed"
echo ""

# ============================================
# Step 3: Create Local Folders
# ============================================
echo "Step 3: Creating local folders..."
echo ""

mkdir -p "$SCRIPT_DIR/logs"
print_status "Created logs folder"

echo ""

# ============================================
# Step 4: Configure Storage Paths
# ============================================
echo "Step 4: Configuring storage paths..."
echo ""

# Check if config.ini exists
if [ ! -f "$SCRIPT_DIR/config.ini" ]; then
    print_error "config.ini not found!"
    exit 1
fi

# Read current paths from config
echo "Current storage paths in config.ini:"
grep -E "^(watch|processing|output|processed|temp|error) =" config.ini | while read line; do
    echo "  $line"
done
echo ""

read -p "Do you need to update storage paths? (y/N): " UPDATE_PATHS
if [[ "$UPDATE_PATHS" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Please edit config.ini to set the correct paths for:"
    echo "  - watch: Where source files are dropped"
    echo "  - processing: Temp location during processing"
    echo "  - output: Where transcoded files go"
    echo "  - processed: Archive for source files after processing"
    echo "  - temp: For intermediate files"
    echo "  - error: For files that failed processing"
    echo ""
    read -p "Press Enter to open config.ini in nano (or Ctrl+C to edit manually later)..."
    nano "$SCRIPT_DIR/config.ini"
fi

# Verify storage paths exist or create them
echo ""
echo "Verifying storage paths..."
for path_key in watch processing output processed temp error; do
    path=$(grep "^$path_key = " config.ini | cut -d'=' -f2 | xargs)
    if [ -n "$path" ]; then
        expanded_path=$(eval echo "$path")
        if [ -d "$expanded_path" ]; then
            print_status "$path_key: $expanded_path (exists)"
        else
            print_warning "$path_key: $expanded_path (does not exist)"
            read -p "  Create this folder? (Y/n): " CREATE_FOLDER
            if [[ ! "$CREATE_FOLDER" =~ ^[Nn]$ ]]; then
                mkdir -p "$expanded_path"
                print_status "  Created $expanded_path"
            fi
        fi
    fi
done

echo ""

# ============================================
# Step 5: Create Startup Scripts
# ============================================
echo "Step 5: Creating startup scripts..."
echo ""

# Get the admin user's home directory
ADMIN_HOME=$(eval echo ~)

# Create start script
cat > "$ADMIN_HOME/start_ingest_engine.sh" << 'STARTEOF'
#!/bin/bash

# Check for any existing ingest engine processes
EXISTING=$(pgrep -f "python.*main.py" | wc -l | tr -d ' ')
if [ "$EXISTING" -gt 0 ]; then
    echo "ERROR: $EXISTING ingest engine process(es) already running!"
    echo "Run ./stop_ingest_engine.sh first to stop existing processes."
    pgrep -af "python.*main.py"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$SCRIPT_DIR/ingest_engine_v2"

echo "Starting Ingest Engine..."
cd "$ENGINE_DIR" || exit

# Clean up any stale lock file
rm -f .ingest_engine.lock

# Ensure log file exists and redirect all output to it
touch logs/ingest_engine.log
nohup python3 main.py >> logs/ingest_engine.log 2>&1 &
INGEST_PID=$!
echo "Ingest Engine PID: $INGEST_PID"

# Wait a moment and verify it started successfully
sleep 2
if ! ps -p $INGEST_PID > /dev/null 2>&1; then
    echo "ERROR: Ingest Engine failed to start! Check logs/ingest_engine.log"
    tail -20 logs/ingest_engine.log
    exit 1
fi

echo "Ingest Engine started successfully."
echo "Dashboard: Check your configured web app URL"
echo "Logs: tail -f $ENGINE_DIR/logs/ingest_engine.log"
STARTEOF

chmod +x "$ADMIN_HOME/start_ingest_engine.sh"
print_status "Created ~/start_ingest_engine.sh"

# Create stop script
cat > "$ADMIN_HOME/stop_ingest_engine.sh" << 'STOPEOF'
#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$SCRIPT_DIR/ingest_engine_v2"

echo "Stopping Ingest Engine..."

# First try graceful shutdown
pkill -f "python.*main.py"
sleep 1

# Force kill any remaining python main.py processes
pkill -9 -f "python.*main.py" 2>/dev/null

# Double-check and report
REMAINING=$(pgrep -f "python.*main.py" | wc -l | tr -d ' ')
if [ "$REMAINING" -gt 0 ]; then
    echo "WARNING: $REMAINING ingest engine process(es) still running!"
    pgrep -af "python.*main.py"
else
    echo "Ingest Engine stopped."
fi

# Clean up lock file and temp files
rm -f "$ENGINE_DIR/.ingest_engine.lock"
rm -f "$ENGINE_DIR/.worker.lock"

# Clean up temp folder if configured
TEMP_PATH=$(grep "^temp = " "$ENGINE_DIR/config.ini" | cut -d'=' -f2 | xargs)
if [ -n "$TEMP_PATH" ] && [ -d "$TEMP_PATH" ]; then
    rm -f "$TEMP_PATH"/*_BAKED.mxf 2>/dev/null
    echo "Cleaned up temp files."
fi

echo "Done."
STOPEOF

chmod +x "$ADMIN_HOME/stop_ingest_engine.sh"
print_status "Created ~/stop_ingest_engine.sh"

echo ""

# ============================================
# Step 6: Configure API for Remote Access
# ============================================
echo "Step 6: Configuring API for remote access..."
echo ""

# Get the Mac's IP address
MAC_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "unknown")
echo "This Mac's IP address: $MAC_IP"
echo ""
echo "The ingest engine API will be available at:"
echo "  http://$MAC_IP:8080"
echo ""
echo "Make sure this port is accessible from your web app server."
echo "You may need to configure your firewall to allow incoming connections on port 8080."
echo ""

# ============================================
# Step 7: Test Run
# ============================================
echo "Step 7: Testing the setup..."
echo ""

read -p "Would you like to run a quick test? (Y/n): " RUN_TEST
if [[ ! "$RUN_TEST" =~ ^[Nn]$ ]]; then
    echo ""
    echo "Starting ingest engine for test..."
    cd "$SCRIPT_DIR"
    python3 main.py &
    TEST_PID=$!
    sleep 3

    if ps -p $TEST_PID > /dev/null 2>&1; then
        print_status "Ingest engine started successfully (PID: $TEST_PID)"

        # Test API
        echo "Testing API..."
        if curl -s http://localhost:8080/api/health | grep -q "ok"; then
            print_status "API is responding correctly"
        else
            print_warning "API test failed - check logs"
        fi

        echo ""
        echo "Stopping test instance..."
        kill $TEST_PID 2>/dev/null
        sleep 1
        print_status "Test complete"
    else
        print_error "Ingest engine failed to start. Check the logs:"
        tail -20 logs/ingest_engine.log
    fi
fi

echo ""
echo "=============================================="
echo "  Setup Complete!"
echo "=============================================="
echo ""
echo "To start the ingest engine:"
echo "  ~/start_ingest_engine.sh"
echo ""
echo "To stop the ingest engine:"
echo "  ~/stop_ingest_engine.sh"
echo ""
echo "To view logs:"
echo "  tail -f $SCRIPT_DIR/logs/ingest_engine.log"
echo ""
echo "API endpoint (for web app configuration):"
echo "  http://$MAC_IP:8080"
echo ""
echo "IMPORTANT: Update your web app's INGEST_ENGINE_API environment"
echo "variable to point to this Mac's IP address."
echo ""
