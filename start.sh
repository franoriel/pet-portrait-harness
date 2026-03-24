#!/bin/sh
cd "$(dirname "$0")"
source ~/.zshrc 2>/dev/null || true
exec python3 app.py
