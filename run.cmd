@echo off
REM Windows entry point. Mirrors ./run exactly.
setlocal
cd /d "%~dp0"
if defined PYTHON (set "PY=%PYTHON%") else (set "PY=python")
"%PY%" -m python_pipeline.main %*
exit /b %ERRORLEVEL%
