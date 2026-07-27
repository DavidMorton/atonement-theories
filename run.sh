#!/usr/bin/env bash
set -euo pipefail
 
PORT=8000
 
cd "$(dirname "$0")"
 
# Start the server in the background.
python3 -m http.server "$PORT" &
SERVER_PID=$!
 
# Kill it whenever this script exits — normal exit, Ctrl-C, or error.
trap 'kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null' EXIT
 
# Wait for the port to actually accept connections before opening the browser.
until curl -sf -o /dev/null "http://localhost:$PORT/"; do
  # Bail out if the server died on startup (e.g. port already in use).
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "server failed to start"; exit 1; }
  sleep 0.1
done
 
open "http://localhost:$PORT/"
 
echo "Serving on http://localhost:$PORT/ — Ctrl-C to stop."
 
# Block here until the server exits or you Ctrl-C.
wait "$SERVER_PID"
 