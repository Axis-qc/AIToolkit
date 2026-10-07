@echo off
REM ============================================================
REM  强制重启 AIToolkit 后端（管理员权限）
REM
REM  用于后端卡死（端口仍 LISTENING 但 /api/health 无响应）时
REM  手动救回。干不了自动化，需要手动双击本文件。
REM
REM  为什么需要管理员：后端本身是管理员进程，非提权进程杀不掉它。
REM  本文件会自动请求提权。
REM ============================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_backend.ps1" %*
pause
