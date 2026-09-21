@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小虫光标 Pet Cursor · AI 抠图版

rem 这一版带 AI 智能抠图（rembg），需要本机 Python 环境
rem 自动找本机 Python（不写死路径，换台电脑也能用）
set "PY=python"
python -c "import sys" >nul 2>nul || set "PY=py"

"%PY%" -c "import PIL" 2>nul
if errorlevel 1 (
  echo 正在安装依赖 Pillow ...
  "%PY%" -m pip install Pillow
)
"%PY%" -c "import rembg" 2>nul
if errorlevel 1 (
  echo 正在安装 AI 抠图引擎 rembg（首次约需几分钟）...
  "%PY%" -m pip install "rembg[cpu]"
)

echo 正在启动（AI 抠图可用），浏览器会自动打开 http://127.0.0.1:8899
echo 关闭本窗口即退出工具。
"%PY%" server.py
pause
