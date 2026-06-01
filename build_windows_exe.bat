@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title GBA Cheat Patcher Studio v1.0 - Build EXE

echo GBA Cheat Patcher Studio v1.0 - Windows portable EXE build
echo ============================================================
echo.

set "APP_NAME=GBA-Cheat-Patcher-Studio"
set "MAIN_SCRIPT=gba_cheat_patcher_studio.py"
set "ICON_FILE=assets\app_icon.ico"
set "VENV_DIR=.venv-build"

if not exist "%MAIN_SCRIPT%" (
    echo ERROR: Script file "%MAIN_SCRIPT%" does not exist.
    pause
    exit /b 1
)

if not exist "database" (
    echo ERROR: Required folder "database" does not exist.
    echo.
    echo The app needs this folder because it contains the bundled databases.
    pause
    exit /b 1
)

if not exist "assets" (
    echo ERROR: Required folder "assets" does not exist.
    echo.
    echo The app needs this folder because it contains the app logo and icon.
    pause
    exit /b 1
)

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

if not exist "requirements-build.txt" (
    echo ERROR: requirements-build.txt does not exist.
    pause
    exit /b 1
)

echo Using Python:
%PYTHON_CMD% --version
echo.

echo Preparing local build environment: %VENV_DIR%
if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create local build environment.
        pause
        exit /b 1
    )
)

set "BUILD_PYTHON=%VENV_DIR%\Scripts\python.exe"

"%BUILD_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo ERROR: pip upgrade failed in the local build environment.
    pause
    exit /b 1
)

"%BUILD_PYTHON%" -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo.
    echo ERROR: Build requirements install failed.
    pause
    exit /b 1
)

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

"%BUILD_PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --onedir ^
  --windowed ^
  --name "%APP_NAME%" ^
  --icon "%ICON_FILE%" ^
  --add-data "database;database" ^
  --add-data "assets;assets" ^
  "%MAIN_SCRIPT%"

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo BUILD COMPLETED
echo ============================================================
echo Portable app folder:
echo %CD%\dist\%APP_NAME%
echo.
echo Share the entire folder, not only the EXE.
echo ============================================================
pause
exit /b 0
