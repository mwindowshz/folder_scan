@echo off
cd /d "%~dp0"
py folder_content_search.py
if errorlevel 1 pause
