@echo off
rem Baut ltu_collector.exe – nimmt MinGW (g++) oder MSVC (cl), je nachdem was da ist.
cd /d "%~dp0"

where g++ >nul 2>nul
if %errorlevel%==0 (
    echo Baue mit MinGW g++ ...
    g++ -O2 -static -pthread ltu_collector.cpp -o ltu_collector.exe -lwinhttp
    if exist ltu_collector.exe echo OK: ltu_collector.exe gebaut. & goto :eof
)

where cl >nul 2>nul
if %errorlevel%==0 (
    echo Baue mit MSVC cl ...
    cl /O2 /EHsc /MT ltu_collector.cpp /Fe:ltu_collector.exe /link winhttp.lib
    if exist ltu_collector.exe echo OK: ltu_collector.exe gebaut. & goto :eof
)

echo Kein Compiler gefunden. Bitte MinGW (g++) oder MSVC (cl) installieren.
exit /b 1
