@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Сборка rename_for_plex.exe
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в PATH.
    echo Установите Python 3.8+ с https://www.python.org/downloads/
    echo и включите галочку "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PyInstaller не найден, устанавливаю...
    python -m pip install -r requirements-dev.txt
    if errorlevel 1 (
        echo.
        echo [ОШИБКА] Не удалось установить PyInstaller.
        pause
        exit /b 1
    )
)

echo.
python build_exe.py %*
set "RESULT=%ERRORLEVEL%"

echo.
if /i not "%1"=="--no-pause" pause
exit /b %RESULT%
