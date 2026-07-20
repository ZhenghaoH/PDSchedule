@echo off
setlocal

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" %*
set "PUBLISH_EXIT_CODE=%ERRORLEVEL%"

echo(%* | %SystemRoot%\System32\findstr.exe /I /C:"-NoPause" >nul
if errorlevel 1 pause

exit /b %PUBLISH_EXIT_CODE%
