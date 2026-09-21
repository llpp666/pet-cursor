@echo off
chcp 65001 >nul
title 小虫光标 · 打包发布

rem 定位源码目录：本 bat 可以放在桌面「小虫光标」文件夹里，源码在别的路径
set "SRC=%~dp0"
if not exist "%SRC%小虫光标.spec" (
  for /d %%d in ("%USERPROFILE%\WorkBuddy\*") do (
    if exist "%%d\pet-cursor\小虫光标.spec" set "SRC=%%d\pet-cursor\"
  )
)
rem 兜底：直接认本机源码目录
if not exist "%SRC%小虫光标.spec" if exist "C:\Users\lenovo\WorkBuddy\2026-09-19-21-25-32\pet-cursor\小虫光标.spec" set "SRC=C:\Users\lenovo\WorkBuddy\2026-09-19-21-25-32\pet-cursor\"
if not exist "%SRC%小虫光标.spec" (
  echo 没找到源码文件 小虫光标.spec
  echo 请把本 bat 放到小虫光标源码文件夹里，或手动指定源码路径。
  pause
  exit /b 1
)
cd /d "%SRC%"

set "PY=python"
python -c "import sys" >nul 2>nul || set "PY=py"
py -c "import sys" >nul 2>nul || (
  echo 没找到 Python，请先安装 Python 3.11 及以上版本。
  pause
  exit /b 1
)

rem 缺 Pillow / PyInstaller 就自动装（国内走清华源）
"%PY%" -c "import PIL" 2>nul
if errorlevel 1 (
  echo 正在安装依赖 Pillow ...
  "%PY%" -m pip install Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
  "%PY%" -c "import PIL" 2>nul
  if errorlevel 1 (
    echo Pillow 没装上。请手动执行：%PY% -m pip install Pillow
    pause
    exit /b 1
  )
)
"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo 正在安装 PyInstaller ...
  "%PY%" -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
  "%PY%" -c "import PyInstaller" 2>nul
  if errorlevel 1 (
    echo PyInstaller 没装上。请手动执行：%PY% -m pip install pyinstaller
    pause
    exit /b 1
  )
)

rem 清掉上次的产物，避免上次运行残留的 user_icons 混进发布包
if exist "dist" (
  echo 清理上次的打包产物 ...
  rd /s /q "dist"
)
if exist "build" rd /s /q "build"

echo.
echo 正在打包 exe（约 1-3 分钟）...
"%PY%" -m PyInstaller --noconfirm 小虫光标.spec
if errorlevel 1 (
  echo 打包失败，看上面的错误。
  pause
  exit /b 1
)

echo.
echo 正在生成发布 zip ...
"%PY%" make_release.py
if errorlevel 1 (
  echo 生成 zip 失败（可能是发布包里混进了私人数据，已中止）。
  pause
  exit /b 1
)

echo.
echo ────────────────────────────────
echo 完成。发布包在 release_pkg\PetCursor-Windows.zip
echo.
echo 下一步：去 https://github.com/llpp666/pet-cursor/releases
echo   新建 Release，把这个 zip 拖进去上传。
echo 详细流程见「更新与发布指南.md」
pause
