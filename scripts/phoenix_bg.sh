#!/usr/bin/env bash
# Start Phoenix tracing server in the background.
# PID stored at ~/.phoenix.pid — used by run_test.py to check/stop the server.
# Logs written to ~/.phoenix.log.

PID_FILE="$HOME/.phoenix.pid"
LOG_FILE="$HOME/.phoenix.log"

# Check if already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Phoenix already running (PID $PID) — http://localhost:6006"
        exit 0
    else
        echo "Stale PID file found, cleaning up."
        rm -f "$PID_FILE"
    fi
fi

echo "Starting Phoenix server..."
nohup python -m phoenix.server.main serve >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Phoenix started (PID $(cat $PID_FILE)) — http://localhost:6006"
echo "Logs: $LOG_FILE"
