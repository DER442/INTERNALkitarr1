@echo off
cd /d "%~dp0"
echo ===== DX Arranger debug ===== > debug-log.txt
echo Folder: %CD% >> debug-log.txt
echo --- files here --- >> debug-log.txt
dir /b >> debug-log.txt
echo --- _internal (first entries) --- >> debug-log.txt
dir /b _internal | findstr /i "base_library sounddevice python312 numpy" >> debug-log.txt
echo --- running the app --- >> debug-log.txt
DX-Arranger.exe >> debug-log.txt 2>&1
echo --- app ended, exit code %ERRORLEVEL% --- >> debug-log.txt
type debug-log.txt
echo.
echo Send the file debug-log.txt (in this folder) to Claude.
pause
