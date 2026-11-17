@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [QuarkMover] 首次运行，正在创建虚拟环境并安装依赖...
    python -m venv venv
    venv\Scripts\pip install -q -r requirements.txt
)

venv\Scripts\python.exe quark_mover.py %*
pause
