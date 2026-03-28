#!/bin/bash
set -e

cd /Users/gabe/projects/bldg-code-2-json

# Ensure venv exists
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Install dependencies
.venv/bin/python -m pip install -r requirements.txt --quiet

# Ensure test directories exist
mkdir -p tests
touch tests/__init__.py

# Ensure output directories exist
mkdir -p output/raw output/qc output/validated output/fixed

echo "Environment ready."
