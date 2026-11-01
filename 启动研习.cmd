@echo off
setlocal
cd /d "%~dp0"
set "ERT_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%ERT_PYTHON%" (
  start "" "%ERT_PYTHON%" "%~dp0launcher.py"
) else (
  python "%~dp0launcher.py"
)
endlocal
