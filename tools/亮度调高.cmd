@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0brightness.ps1" -Step +10
powershell -NoProfile -Command "Start-Sleep -Milliseconds 900"

