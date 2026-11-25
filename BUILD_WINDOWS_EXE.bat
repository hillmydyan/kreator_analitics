@echo off
title ShadowKreator - Build Windows Desktop App (.exe) [IT Kurma Group]
echo ============================================================================
echo   ShadowKreator - TikTok Creator Product Analytics (IT Kurma Group)
echo   One-Click Windows 10/11 64-bit .EXE Builder
echo ============================================================================
echo.

cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3.9+ 64-bit belum terinstal atau belum masuk ke PATH Windows.
    echo Silakan instal Python 3.11 64-bit dari https://www.python.org/downloads/windows/
    echo dan pastikan mencentang "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv_win" (
    echo [1/4] Membuat Virtual Environment Windows (.venv_win)...
    python -m venv .venv_win
)

echo [2/4] Menginstal dependensi (PySide6, openpyxl, SQLAlchemy, PyInstaller)...
call .venv_win\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [3/4] Menjalankan Unit Test & Validasi Sistem...
python -m unittest discover -s tests -t . -v
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Unit test gagal!
    pause
    exit /b 1
)

echo [4/4] Mengunduh Windows adb.exe dan Mem-build TikTokCreatorAnalytics.exe...
python build_windows.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Proses build .exe gagal!
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo   BUILD WINDOWS .EXE BERHASIL!
echo   File Aplikasi : dist\TikTokCreatorAnalytics\TikTokCreatorAnalytics.exe
echo   Bundled ADB   : dist\TikTokCreatorAnalytics\bin\platform-tools\adb.exe
echo   Poster Panduan: dist\TikTokCreatorAnalytics\docs\Poster_Tutorial_ShadowKreator.pdf
echo ============================================================================
echo.
pause
