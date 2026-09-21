@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小虫光标 Pet Cursor

rem 优先启动打包好的桌面软件（无需 Python 环境）
rem 两种布局都支持：Release zip（exe 与本文件同级）/ 源码仓库（发布\小虫光标\ 下）
set "EXE=%~dp0小虫光标.exe"
if not exist "%EXE%" set "EXE=%~dp0发布\小虫光标\小虫光标.exe"
if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)

rem 没有 exe 就退回 Python 方式（自动探测，不写死路径）
set "PY=python"
python -c "import sys" >nul 2>nul || set "PY=py"
py -c "import sys" >nul 2>nul || (
  echo 没有找到 Python，也没有打包好的 exe。
  echo 请到 Releases 页面下载「小虫光标-Windows.zip」，解压后双击本文件即可。
  pause
  exit /b 1
)

"%PY%" -c "import PIL" 2>nul
if errorlevel 1 (
  echo 正在安装依赖 Pillow ...
  "%PY%" -m pip install Pillow
)

echo 正在启动，浏览器会自动打开 http://127.0.0.1:8899
echo 关闭本窗口即退出工具。
"%PY%" server.py
pause
