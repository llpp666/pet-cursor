@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小虫光标 Pet Cursor

rem 优先启动打包好的桌面软件（无需 Python 环境）
set "EXE=%~dp0发布\小虫光标\小虫光标.exe"
if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)

set "PY=C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import PIL" 2>nul
if errorlevel 1 (
  echo 正在安装依赖 Pillow ...
  "%PY%" -m pip install Pillow
)

echo 正在启动，浏览器会自动打开 http://127.0.0.1:8899
echo 关闭本窗口即退出工具。
"%PY%" server.py
pause
