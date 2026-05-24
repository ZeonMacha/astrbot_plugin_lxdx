#!/bin/bash
cd "$(dirname "$0")"
find . \( -name "__pycache__" -type d -o -name "*.pyc" \) -print -delete 2>/dev/null
zip -r astrbot_plugin_lxdx.zip . -x ".git/*" -x ".kilo/*" -x ".vscode/*" -x "__pycache__/*" -x "*.pyc" -x "pack.sh" -x "*.zip"
echo "Done: $(pwd)/astrbot_plugin_lxdx.zip"
