@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_CMD="
python --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo ERROR: Python 3 is not installed or not available through python or py -3.
    pause
    exit /b 1
)

%PYTHON_CMD% gba_cheat_patcher_studio.py
if errorlevel 1 pause
