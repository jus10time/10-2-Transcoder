#!/bin/bash

# This script automates launching the Field Ingest Engine.
# It activates the virtual environment and then runs the GUI application.

# Get the absolute path of the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Activate the virtual environment located in the same directory
source "$SCRIPT_DIR/venv/bin/activate"

# Run the Python GUI application
echo "Launching Field Ingest Engine..."
python3 "$SCRIPT_DIR/run_gui.py"

echo "Application closed."
