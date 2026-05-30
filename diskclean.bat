@echo off
title diskclean
python "%~dp0diskclean.py" 2>nul || py "%~dp0diskclean.py"
echo.
echo Press any key to close.
pause >nul
