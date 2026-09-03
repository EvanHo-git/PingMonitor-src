@echo off
REM =====================================================================
REM  PingMonitor Windows 本地一键构建脚本
REM  前置条件：已安装官方 Python 3.13（含 Tcl/Tk 8.6），且已加入 PATH
REM  产物：dist\PingMonitor.exe （双击即运行，无需任何运行时）
REM =====================================================================
setlocal
set PY=python

where %PY% >nul 2>&1 || (
  echo [ERROR] Cannot find python. Install official Python 3.13 and check "Add to PATH".
  exit /b 1
)

echo ==^> Creating build venv
if not exist build\venv (
  %PY% -m venv build\venv
)
call build\venv\Scripts\activate.bat

echo ==^> Installing build deps
pip install --quiet -r build\requirements-build.txt
if errorlevel 1 exit /b 1

echo ==^> Building
python build\build_windows.py
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\PingMonitor.exe  (double-click to run)
endlocal
