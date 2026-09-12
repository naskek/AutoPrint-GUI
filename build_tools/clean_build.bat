@echo off
chcp 65001 >nul
setlocal

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%"

echo === Cleanup PyInstaller artifacts ===
if exist "dist" (
    echo Removing dist...
    rmdir /s /q "dist"
) else (
    echo dist not found, skip.
)

if exist "build" (
    echo Removing build...
    rmdir /s /q "build"
) else (
    echo build not found, skip.
)

if exist "AutoPrint.spec" (
    echo Removing AutoPrint.spec...
    del /f /q "AutoPrint.spec"
) else (
    echo AutoPrint.spec not found, skip.
)

echo.
echo Done. venv, данные и ресурсы не тронуты.
pause
popd
