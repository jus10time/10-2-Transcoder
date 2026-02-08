#!/bin/bash

# This script automates launching the 10-2 Transcoder.
# It activates the virtual environment and then runs the GUI application.

# Get the absolute path of the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Activate the virtual environment located in the same directory
source "$SCRIPT_DIR/venv/bin/activate"

# Run the Python GUI application
echo "Launching 10-2 Transcoder..."
python3 "$SCRIPT_DIR/run_gui.py"

echo "Application closed."
