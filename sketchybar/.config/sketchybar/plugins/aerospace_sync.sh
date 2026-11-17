#!/bin/bash
# Called through run_once.py by SketchyBar: one snapshot, no event-driven loop.
exec python3 -B "${BASH_SOURCE[0]%/*}/aerospace_sync.py" "$@"
