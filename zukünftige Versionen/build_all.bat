@echo off
rem =====================================================================
rem build_all.bat - ALLES bauen und sortiert in den Ordner "rea" packen:
rem
rem   rea\php    index.php              -> Webserver hochladen
rem   rea\proxy  ltu_collector.exe + INI    -> Server-PC (Doppelklick!)
rem   rea\user   MSFS Live Traffic Utility\ (Client-App)  -> Client-PCs verteilen
rem   rea\LIESMICH.txt                      -> Anleitung: was wohin
rem   token.txt                             -> gemeinsames privates Token
rem   generate_token.bat                    -> Token sicher erzeugen oder erneuern
rem
rem Voraussetzungen:
rem   * MinGW (g++) oder MSVC (cl) fuer die Collector-Exe
rem   * Python 3.x fuer die Client-App (requirements.txt + PyInstaller)
rem     werden fuer den Build automatisch installiert
rem =====================================================================
setlocal
cd /d "%~dp0"

rem URL der Webseite (http, NICHT https!) - landet in page.txt
set "LTU_URL=http://dnd.59-seconds.com/ltu/index.php"

set "REA=%~dp0rea"
set "ERR_PHP=OK"
set "ERR_COL=OK"
set "ERR_USER=OK"

rem page.txt fuer Source-Betrieb/build_exe.bat anlegen, falls es fehlt:
if not exist "page.txt" > "page.txt" echo %LTU_URL%

rem Ein zentrales Token fuer PHP und Collector vorbereiten:
if not exist "token.txt" (
    echo HINWEIS: token.txt fehlt; ein sicheres Zufallstoken wird erzeugt.
    call "%~dp0generate_token.bat" /quiet
    if errorlevel 1 (
        echo FEHLER: token.txt konnte nicht erzeugt werden.
        exit /b 1
    )
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\apply_build_token.ps1" -TokenFile "%~dp0token.txt" -ValidateOnly
if errorlevel 1 (
    echo FEHLER: token.txt ist ungueltig. Siehe Format-Hinweis oben.
    exit /b 1
)

echo.
echo ============================================
echo   LTU Build-All   -   Zielordner: rea
echo ============================================

rem ---------------- 1) Ordner neu anlegen ----------------
echo.
echo [1/5] rea-Ordner anlegen ...
if exist "%REA%" rmdir /s /q "%REA%"
mkdir "%REA%\php" "%REA%\proxy" "%REA%\user"
if errorlevel 1 (
    echo FEHLER: rea-Ordner konnten nicht erstellt werden.
    exit /b 1
)

rem ---------------- 2) PHP fuer den Webserver ----------------
echo.
echo [2/5] PHP + zentrale Token-Konfiguration ...
copy /y "proxy\index.php" "%REA%\php\index.php" >nul
if errorlevel 1 set "ERR_PHP=FEHLER"
copy /y "proxy\client_demand.php" "%REA%\php\client_demand.php" >nul
if errorlevel 1 set "ERR_PHP=FEHLER"
copy /y "proxy\aircraft_tracks.php" "%REA%\php\aircraft_tracks.php" >nul
if errorlevel 1 set "ERR_PHP=FEHLER"
copy /y "proxy\ltu_collector.example.ini" "%REA%\proxy\ltu_collector.ini" >nul
if errorlevel 1 set "ERR_COL=FEHLER"
copy /y "proxy\credentials.example.json" "%REA%\proxy\credentials.example.json" >nul
if errorlevel 1 set "ERR_COL=FEHLER"
copy /y "proxy\planetrack_credentials.example.json" "%REA%\proxy\planetrack_credentials.example.json" >nul
if errorlevel 1 set "ERR_COL=FEHLER"
copy /y "proxy\run_collector_watchdog.ps1" "%REA%\proxy\run_collector_watchdog.ps1" >nul
if errorlevel 1 set "ERR_COL=FEHLER"
copy /y "proxy\install_collector_task.ps1" "%REA%\proxy\install_collector_task.ps1" >nul
if errorlevel 1 set "ERR_COL=FEHLER"
copy /y "proxy\remove_collector_task.ps1" "%REA%\proxy\remove_collector_task.ps1" >nul
if errorlevel 1 set "ERR_COL=FEHLER"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\apply_build_token.ps1" -TokenFile "%~dp0token.txt" -PhpFile "%REA%\php\index.php" -IniFile "%REA%\proxy\ltu_collector.ini"
if errorlevel 1 (
    echo FEHLER: Das zentrale Token konnte nicht in PHP und Collector-INI geschrieben werden.
    exit /b 1
)

rem ---------------- 3) Collector-Exe (Server-PC) ----------------
echo.
echo [3/5] Collector ^(Proxy-Exe fuer Server-PC^) ...
set "CXX="
rem Optionaler Override fuer reproduzierbare Builds (z. B. LTU_BUILD_CXX=cl in CI).
if /I "%LTU_BUILD_CXX%"=="g++" set "CXX=gpp"
if /I "%LTU_BUILD_CXX%"=="cl" set "CXX=cl"
if not defined CXX (
    where g++ >nul 2>nul && set "CXX=gpp"
    if not defined CXX (where cl >nul 2>nul && set "CXX=cl")
)
if not defined CXX (
    echo   FEHLER: Kein C++-Compiler gefunden.
    echo   Installiere MinGW-w64 oder Visual Studio Build Tools mit C++-Workload.
    echo   Bei MSVC build_all.bat aus dem "x64 Native Tools Command Prompt" starten.
    set "ERR_COL=KEIN-COMPILER"
) else (
    if "%CXX%"=="gpp" (
        echo   Baue mit MinGW g++ ...
        g++ -O2 -static -pthread proxy\ltu_collector.cpp -o "%REA%\proxy\ltu_collector.exe" -lwinhttp
    ) else (
        echo   Baue mit MSVC cl ...
        pushd "%REA%\proxy"
        cl /nologo /O2 /EHsc /MT "%~dp0proxy\ltu_collector.cpp" /Fe:ltu_collector.exe /Fo:ltu_collector.obj /link winhttp.lib
        del /q ltu_collector.obj >nul 2>nul
        popd
    )
    if not exist "%REA%\proxy\ltu_collector.exe" (
        echo   FEHLER: Collector-Build fehlgeschlagen.
        set "ERR_COL=BUILD-FEHLER"
    )
)

rem ---------------- 4) Client-App (User-PCs) ----------------
echo.
echo [4/5] User-Client ^(MSFS Live Traffic Utility^) ...
set "PY="
where python >nul 2>nul && set "PY=1"
if not defined PY (
    echo   FEHLER: Python nicht gefunden - Client-Build uebersprungen.
    set "ERR_USER=KEIN-PYTHON"
) else (
    python -m pip install -r "%~dp0requirements.txt" pyinstaller
    if errorlevel 1 (
        echo   FEHLER: Python-Abhaengigkeiten konnten nicht installiert werden.
        set "ERR_USER=DEPENDENCY-FEHLER"
    ) else (
        rem PyInstaller legt die .spec unter rea\_build ab; daher Datenquellen absolut angeben.
        python -m PyInstaller --noconfirm --clean --windowed --name "MSFS Live Traffic Utility" ^
            --distpath "%REA%\user" --workpath "%REA%\_build" --specpath "%REA%\_build" ^
            --add-data "%~dp0gui_web;gui_web" ^
            --add-data "%~dp0colors.json;." ^
            --add-data "%~dp0modelmap.txt;." ^
            --add-data "%~dp0SimConnect.dll;." ^
            --add-data "%~dp0LICENSE;." ^
            --add-data "%~dp0THIRD_PARTY_NOTICES.txt;." ^
            "%~dp0main.py"
        if not exist "%REA%\user\MSFS Live Traffic Utility\MSFS Live Traffic Utility.exe" (
            echo   FEHLER: PyInstaller-Build fehlgeschlagen.
            set "ERR_USER=BUILD-FEHLER"
        ) else (
            python "%~dp0scripts\verify_map_bundle.py" "%REA%\user\MSFS Live Traffic Utility"
            if errorlevel 1 set "ERR_USER=MAP-ASSET-FEHLER"
            rem PyInstaller 6 legt Daten in _internal ab; Notices fuer Nutzer gut sichtbar ins Bundle kopieren.
            for %%N in (LICENSE THIRD_PARTY_NOTICES.txt) do (
                if not exist "%REA%\user\MSFS Live Traffic Utility\%%N" (
                    if exist "%REA%\user\MSFS Live Traffic Utility\_internal\%%N" copy /y "%REA%\user\MSFS Live Traffic Utility\_internal\%%N" "%REA%\user\MSFS Live Traffic Utility\%%N" >nul
                )
                if not exist "%REA%\user\MSFS Live Traffic Utility\%%N" set "ERR_USER=NOTICE-FEHLER"
            )
            rem page.txt mit der URL liegt dann schon fertig neben der exe:
            > "%REA%\user\MSFS Live Traffic Utility\page.txt" echo %LTU_URL%
        )
    )
    rmdir /s /q "%REA%\_build" >nul 2>nul
)

rem ---------------- 5) LIESMICH.txt ----------------
echo.
echo [5/5] LIESMICH.txt ...
(
    echo LIESMICH - was wohin?
    echo =====================
    echo.
    echo 1^) rea\php\index.php  ^(PHP + Statusseite^)
    echo    -^> Als index.php auf den Webserver hochladen, in den Ordner:
    echo       http://dnd.59-seconds.com/ltu/
    echo       Gemeinsames Token kommt aus token.txt im Projektordner und
    echo       ist schon in PHP und ltu_collector.ini eingetragen.
    echo       token.txt bleibt privat und wird nicht mit ausgeliefert.
    echo       Nach einer Token-Aenderung build_all.bat erneut ausfuehren.
    echo       Der Ordner muss beschreibbar sein. Test danach:
    echo       http://dnd.59-seconds.com/ltu/ oeffnen = Statusseite
    echo       ^(ohne ?action zeigt die PHP automatisch die Statusseite^).
    echo.
    echo 2^) rea\proxy\  ^(Server-PC^)
    echo    -^> Kompletten Ordner auf den Server-PC kopieren und
    echo       ltu_collector.exe DOPPELKLICKEN - startet ohne Argumente
    echo       ^(weltweit, alle 5 s^). Fuer ueberwachten Dauerbetrieb:
    echo       powershell -NoProfile -ExecutionPolicy Bypass -File run_collector_watchdog.ps1
    echo       Optionaler Autostart beim Anmelden:
    echo       powershell -NoProfile -ExecutionPolicy Bypass -File install_collector_task.ps1
    echo       Entfernen: powershell -NoProfile -ExecutionPolicy Bypass -File remove_collector_task.ps1
    echo       Pro Start ein Logpaar unter .\logs; alte Logs werden automatisch geloescht.
    echo       Die passende ltu_collector.ini mit gleichem Token liegt bei.
    echo       OpenSky OAuth2 ist optional und nur fuer diesen Server-PC:
    echo       credentials.example.json nach credentials.json kopieren und
    echo       einmalig die OpenSky clientId und clientSecret eintragen.
    echo       PlaneTrack ist die optionale vierte Quelle nach adsb.lol, adsb.fi
    echo       und OpenSky: planetrack_credentials.example.json nach
    echo       planetrack_credentials.json kopieren und den API-Key eintragen.
    echo       Beide Credential-Dateien nur lokal auf dem Collector-PC speichern,
    echo       Dateirechte beschraenken und niemals an Clients/Webspace verteilen
    echo       oder committen. PlaneTrack-Key bleibt aus Logs und Status heraus.
    echo       Token und OAuth-Zugangsdaten bleiben getrennt; Tokens erneuert
    echo       der Collector automatisch. OpenSky ist nur fuer Forschung und
    echo       nicht-kommerzielle Nutzung; mindestens 120 s globales Intervall.
    echo       adsb.lol und adsb.fi sind kostenlose Alternativen, aber limitiert.
    echo.
    echo 3^) rea\user\  ^(Client-PCs^)
    echo    -^> Kompletten Ordner "MSFS Live Traffic Utility" ^(z. B. als Zip^) auf jeden
    echo       Client-PC kopieren und MSFS Live Traffic Utility.exe starten.
    echo       page.txt ^(URL der Webseite^) liegt schon fertig dabei,
    echo       ein Token wird NICHT gebraucht.
    echo.
    echo Gebaut am %DATE% um %TIME%
) > "%REA%\LIESMICH.txt"

rem ---------------- Zusammenfassung ----------------
echo.
echo ============================================
echo   Fertig:  %REA%
echo --------------------------------------------
echo   php    : %ERR_PHP%    ^(index.php    -^> Webserver^)
echo   proxy  : %ERR_COL%    ^(ltu_collector.exe -^> Server-PC^)
echo   user   : %ERR_USER%   ^(MSFS Live Traffic Utility -^> Client-PCs^)
echo ============================================
echo.
if not "%ERR_PHP%"=="OK" exit /b 1
if not "%ERR_COL%"=="OK" exit /b 1
if not "%ERR_USER%"=="OK" exit /b 1
exit /b 0
