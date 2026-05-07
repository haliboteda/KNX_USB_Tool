@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Group addresses used by the ping-pong test.
set "KNX_RX_GA=0/0/1"
set "KNX_TX_GA=0/0/2"
set "KNX_OWN_ADDRESS=15.15.15"

rem append_counter: wait for OpenPLC, append loop counter byte, then reply.
rem increment: one-byte raw/DPT 5.010 style test, RX n -> TX n+1.
rem append: append KNX_APPEND_BYTE to the received payload.
set "KNX_MODE=append_counter"
set "KNX_APPEND_BYTE=1"
set "KNX_DEBUG=0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "UV=%~dp0tools\uv.exe"

echo.
echo KNX Ping-Pong Tool
echo ------------------------------------------
echo RX GA: %KNX_RX_GA%  OpenPLC sends, this tool receives
echo TX GA: %KNX_TX_GA%  this tool sends, OpenPLC receives
echo KNX/USB converter: %KNX_OWN_ADDRESS%
echo Mode: %KNX_MODE%
echo ------------------------------------------
echo Press Ctrl+C to stop.
echo.

if exist "%PY%" (
    "%PY%" "%~dp0ping_pong.py"
    goto :done
)

if exist "%UV%" (
    "%UV%" run "%~dp0ping_pong.py"
    goto :done
)

echo ERROR: No Python runtime found.
echo Expected either:
echo   %PY%
echo or:
echo   %UV%
echo.
echo Create the virtual environment and install hidapi, or put uv.exe in tools.

:done
echo.
pause
