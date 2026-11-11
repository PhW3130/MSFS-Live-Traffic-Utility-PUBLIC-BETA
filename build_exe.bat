@echo off
rem =====================================================================
rem build_exe.bat - MSFS Live Traffic Utility.exe mit PyInstaller bauen (Windows)
rem Ergebnis: dist\MSFS Live Traffic Utility\MSFS Live Traffic Utility.exe (inkl. Karte, Theme, Modelmap)
rem Gleicher Build wie im GitHub-Workflow .github/workflows/release.yml
rem =====================================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo FEHLER: Python wurde nicht gefunden.
    exit /b 1
)

python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo FEHLER: Python-Abhaengigkeiten konnten nicht installiert werden.
    exit /b 1
)

python -m PyInstaller --noconfirm --clean --windowed --name "MSFS Live Traffic Utility" ^
    --add-data "gui_web;gui_web" ^
    --add-data "colors.json;." ^
    --add-data "modelmap.txt;." ^
    --add-data "SimConnect.dll;." ^
    --add-data "LICENSE;." ^
    --add-data "THIRD_PARTY_NOTICES.txt;." ^
    main.py

if errorlevel 1 (
    echo BUILD FEHLGESCHLAGEN
    exit /b 1
)

python "%~dp0scripts\verify_map_bundle.py" "%~dp0dist\MSFS Live Traffic Utility"
if errorlevel 1 (
    echo FEHLER: Lokale Karten-Dateien fehlen im Windows-Paket.
    exit /b 1
)

rem PyInstaller 6 legt Daten in _internal ab; Notices fuer Nutzer gut sichtbar ins Bundle kopieren.
for %%N in (LICENSE THIRD_PARTY_NOTICES.txt) do (
    if not exist "dist\MSFS Live Traffic Utility\%%N" (
        if exist "dist\MSFS Live Traffic Utility\_internal\%%N" copy /y "dist\MSFS Live Traffic Utility\_internal\%%N" "dist\MSFS Live Traffic Utility\%%N" >nul
    )
    if not exist "dist\MSFS Live Traffic Utility\%%N" (
        echo FEHLER: %%N fehlt im Windows-Paket.
        exit /b 1
    )
)

echo.
rem page.txt (URL der Webseite, gitignoriert) neben die exe legen:
if exist page.txt copy /y page.txt "dist\MSFS Live Traffic Utility\page.txt" >nul

echo Fertig: dist\MSFS Live Traffic Utility\MSFS Live Traffic Utility.exe
