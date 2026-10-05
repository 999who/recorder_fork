@echo off
rem ==============================================================================
rem Skrypt budowania aplikacji EMANAGER Signal do pliku EXE
rem ==============================================================================

set "PYTHON_CMD="
if exist "env\Scripts\python.exe" (
    set "PYTHON_CMD=env\Scripts\python.exe"
) else (
    set "PYTHON_CMD=python"
)

echo [INFO] Uzywanie interpretera: %PYTHON_CMD%
echo [INFO] Rozpoczynanie kompilacji PyInstaller...

%PYTHON_CMD% scripts\build_exe.py

if %ERRORLEVEL% equ 0 (
    echo.
    echo [SUKCES] Gotowe! Plik wykonywalny znajduje sie w folderze: dist\EMANAGER-Signal\
    echo [INFO] Pelny log z budowania zapisano w: build_log.txt
) else (
    echo.
    echo [BLAD] Wystapil blad podczas budowania. Kod bledu: %ERRORLEVEL%
    echo [INFO] Raport bledu zostal zapisany w: build_log.txt
)

pause
