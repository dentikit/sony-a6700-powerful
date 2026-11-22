@echo off
title Powerful6700 Tools
cd /d "%~dp0"
where py >NUL 2>NUL
if %errorlevel%==0 (
    py -3.13 "%~dp0A6700-Tools.py"
) else (
    python "%~dp0A6700-Tools.py"
)
if errorlevel 1 (
    echo.
    echo Python 3.13 is needed. Install it from https://www.python.org/downloads/ then try again.
    pause
)
