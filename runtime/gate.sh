#!/bin/sh
# PYTHON selects the interpreter for every test, including optional numpy checks.
set -eu
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" tests/gate.py
