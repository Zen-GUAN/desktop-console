@echo off
rem WinCtlPanel launcher - hide the console window
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0WinCtlPanel.ps1"
