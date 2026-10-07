@echo off
title Godsdownload Unit Tests
color 0a
echo ==============================================================================
echo                 ? RUNNING GODSDOWNLOAD TEST SUITE ?
echo ==============================================================================
echo.
cd /d "%~dp0"
python -m unittest tests/test_platforms.py -v
echo.
echo ==============================================================================
pause
