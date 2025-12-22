@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%"

set PIP_DISABLE_PIP_VERSION_CHECK=1
set PIP_INDEX_URL=https://pypi.org/simple
set PIP_TRUSTED_HOST=pypi.org files.pythonhosted.org
set PIP_NO_CACHE_DIR=1
set PIP_SIMPLE_INDEX_FORMAT=html
set PIP_DEFAULT_TIMEOUT=60

echo [1/3] Prepare venv...
if not exist ".venv" (
    python -m venv ".venv"
)
call ".venv\Scripts\activate.bat"

echo [2/3] Install requirements...
python -m pip install --upgrade pip
python -m pip install -r "%SCRIPT_DIR%requirements.txt"

echo [3/3] Build BarTenderGUI via PyInstaller...
python "%SCRIPT_DIR%build_exe.py"
set "BUILD_ERR=%ERRORLEVEL%"
if not "%BUILD_ERR%"=="0" (
    echo Build failed. Exit code: %BUILD_ERR%
) else (
    echo Done. EXE: dist\BarTenderGUI\BarTenderGUI.exe
)
echo.
echo ---------- Finished (exit code %BUILD_ERR%). ----------
echo Нажмите любую клавишу, чтобы закрыть окно.
pause
popd
exit /b %BUILD_ERR%
