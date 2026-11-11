@echo off
rem Erstellt oder erneuert das private zentrale token.txt mit Windows PowerShell.
setlocal
cd /d "%~dp0"

set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"

if exist "token.txt" (
    if defined QUIET (
        echo FEHLER: Im stillen Modus wird ein vorhandenes token.txt nicht ueberschrieben.
        exit /b 1
    )
    echo WARNUNG: Ein neues Token macht bereits gebaute PHP- und INI-Dateien ungueltig.
    choice /C JN /N /M "token.txt wirklich ersetzen? [J/N] "
    if errorlevel 2 (
        echo Abgebrochen. Das vorhandene token.txt bleibt unveraendert.
        pause
        exit /b 0
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\generate_token.ps1" -OutputPath "%~dp0token.txt" -Force
if errorlevel 1 goto :failed

if defined QUIET exit /b 0
echo.
echo token.txt ist bereit und bleibt privat.
echo Danach build_all.bat erneut starten, damit PHP und Collector-INI aktualisiert werden.
pause
exit /b 0

:failed
echo.
echo FEHLER: token.txt konnte nicht erzeugt werden. Pruefe Windows PowerShell.
if not defined QUIET pause
exit /b 1
