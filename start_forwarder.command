#!/bin/bash
# ChatBridge Forwarder - macOS double-click launcher
# Set executable: chmod +x start_forwarder.command

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$SCRIPT_DIR' && bash start_forwarder.sh"
end tell
EOF
