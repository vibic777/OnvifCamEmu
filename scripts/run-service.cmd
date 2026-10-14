@echo off
REM Long-lived emulator with web GUI. Used by install-service.ps1 so cwd is the project, not System32.
cd /d "%~dp0\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m onvif_emu --service %*
) else (
  python -m onvif_emu --service %*
)
