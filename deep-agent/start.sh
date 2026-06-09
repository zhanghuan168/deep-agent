#!/usr/bin/env bash
# Linux/macOS 启动脚本
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[INFO] 创建虚拟环境 .venv ..."
    python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

PORT=${DAGENT_PORT:-8765}
echo "[INFO] 启动服务 (http://127.0.0.1:$PORT) ..."
python main.py
