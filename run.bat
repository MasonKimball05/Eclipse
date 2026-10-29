@echo off
rem Double-click to start Eclipse on Windows (it runs run.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
rem (Double-clicked, the window closes when it's done: on an error, it stays until you've read it)
if errorlevel 1 pause
