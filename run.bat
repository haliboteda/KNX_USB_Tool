@echo off
:: KNX Ping-Pong launcher for Windows 11
:: Requires: uv.exe in the same folder as this script.
:: Download uv from: https://github.com/astral-sh/uv/releases/latest
::   -> uv-x86_64-pc-windows-msvc.zip  -> extract uv.exe here

setlocal
cd /d "%~dp0"

:: Check that uv.exe is present
if not exist "uv.exe" (
    echo.
    echo  ERROR: uv.exe not found in this folder.
    echo  Download it from:
    echo    https://github.com/astral-sh/uv/releases/latest
    echo  Extract uv.exe into this folder, then run this script again.
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting KNX TP Ping-Pong (PC side)
echo  GA 0/0/1  ^<-- OpenPLC sends
echo  GA 0/0/2  --^> OpenPLC receives
echo.

:: Run with xknx and hid packages (uv handles Python + venv automatically)
uv run --with xknx --with hid ping_pong.py

pause
