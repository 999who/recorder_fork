@echo off
rem Wersja testowa Parakeet (obok głównej): dist\EMANAGER-Signal-ParakeetTest\
set "PYTHON_CMD=python"
if exist "env\Scripts\python.exe" set "PYTHON_CMD=env\Scripts\python.exe"
%PYTHON_CMD% scripts\build_exe.py --variant parakeet-test
