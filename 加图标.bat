@echo off
chcp 65001 >nul
title 小虫光标 · 加图标

rem 定位源码目录：本 bat 可以放在桌面「小虫光标」文件夹里，源码在别的路径
set "SRC=%~dp0"
if not exist "%SRC%add_icon.py" (
  for /d %%d in ("%USERPROFILE%\WorkBuddy\*") do (
    if exist "%%d\pet-cursor\add_icon.py" set "SRC=%%d\pet-cursor\"
  )
)
rem 兜底：直接认本机源码目录
if not exist "%SRC%add_icon.py" if exist "C:\Users\lenovo\WorkBuddy\2026-09-19-21-25-32\pet-cursor\add_icon.py" set "SRC=C:\Users\lenovo\WorkBuddy\2026-09-19-21-25-32\pet-cursor\"
if not exist "%SRC%add_icon.py" (
  echo 没找到源码文件 add_icon.py
  echo 请把本 bat 放到小虫光标源码文件夹里，或手动指定源码路径。
  pause
  exit /b 1
)
cd /d "%SRC%"

rem 用法：把图片拖到本文件上，或双击后手动填路径
set "PY=python"
python -c "import sys" >nul 2>nul || set "PY=py"
py -c "import sys" >nul 2>nul || (
  echo 没找到 Python，请先安装 Python 3.11 及以上版本。
  pause
  exit /b 1
)

rem 缺 Pillow 就自动装（国内走清华源；装不上会给明确提示）
"%PY%" -c "import PIL" 2>nul
if errorlevel 1 (
  echo 正在安装依赖 Pillow ...
  "%PY%" -m pip install Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
  "%PY%" -c "import PIL" 2>nul
  if errorlevel 1 (
    echo.
    echo Pillow 没装上。请手动执行下面这行，成功后再来一次：
    echo     %PY% -m pip install Pillow
    pause
    exit /b 1
  )
)

set "IMG=%~1"
if "%IMG%"=="" set /p IMG=图片路径（把图片拖进本窗口后回车）：

set /p NAME=图标名字：
if "%NAME%"=="" (
  echo 名字不能为空，已取消。
  pause
  exit /b 1
)

set "CAT="
set /p CAT=分类（直接回车 = 我的图标）：
if "%CAT%"=="" set "CAT=我的图标"

echo.
"%PY%" add_icon.py "%IMG%" --name "%NAME%" --cat "%CAT%"
if errorlevel 1 (
  echo.
  echo 失败了，检查上面的错误信息。
  pause
  exit /b 1
)

echo.
echo ────────────────────────────────
echo 已加入图标库。
echo 想让所有人也能下载到，还要推送到 GitHub：
echo.
echo   git add icons/ catalog.json
echo   git commit -m "新增图标：%NAME%"
echo   git push
echo.
echo 详细流程见「更新与发布指南.md」
pause
