@echo off
rem Vortex Nexus A/V server (LiveKit on localhost). First run? powershell -File setup_livekit.ps1
cd /d "%~dp0"
if not exist livekit-server.exe (
  echo LiveKit binary missing - running setup...
  powershell -NoProfile -ExecutionPolicy Bypass -File setup_livekit.ps1
)
livekit-server.exe --config livekit.yaml
