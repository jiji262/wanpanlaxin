#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x "venv/bin/python" ]; then
  echo "[QuarkMover] 首次运行，正在创建 Python 虚拟环境并安装依赖..."
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements.txt
fi

exec ./venv/bin/python quark_mover.py "$@"
