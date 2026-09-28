#!/bin/sh
set -e

echo "=========================================================="
echo " Starting Local Drop & Share (Home Assistant Add-on)     "
echo "=========================================================="

OPTIONS_PATH="/data/options.json"

if [ -f "$OPTIONS_PATH" ]; then
    echo "[INFO] Opzioni caricate da $OPTIONS_PATH:"
    cat "$OPTIONS_PATH"
fi

exec python3 /app/server.py
