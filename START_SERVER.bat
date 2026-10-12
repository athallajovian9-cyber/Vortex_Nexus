@echo off
rem Vortex Nexus server (text chat backend - flat files, no SharePoint)
cd /d "%~dp0"
python nexus.py serve --host 127.0.0.1 --port 8765 --data data
pause
