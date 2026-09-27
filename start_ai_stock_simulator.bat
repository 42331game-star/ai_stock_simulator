@echo off
setlocal

cd /d "%~dp0"

if "%MIMO_API_KEY%"=="" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('MIMO_API_KEY','User')"`) do set "MIMO_API_KEY=%%A"
)
if "%MIMO_BASE_URL%"=="" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('MIMO_BASE_URL','User')"`) do set "MIMO_BASE_URL=%%A"
)
if "%MIMO_MODEL%"=="" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('MIMO_MODEL','User')"`) do set "MIMO_MODEL=%%A"
)
if "%LLM_PROVIDER%"=="" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('LLM_PROVIDER','User')"`) do set "LLM_PROVIDER=%%A"
)
if "%LLAMA_BASE_URL%"=="" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('LLAMA_BASE_URL','User')"`) do set "LLAMA_BASE_URL=%%A"
)
if "%LLAMA_MODEL%"=="" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('LLAMA_MODEL','User')"`) do set "LLAMA_MODEL=%%A"
)
if "%MIMO_BASE_URL%"=="" set "MIMO_BASE_URL=https://token-plan-sgp.xiaomimimo.com/v1"
if "%MIMO_MODEL%"=="" set "MIMO_MODEL=mimo-v2.5-pro"
rem Only fill the llama.cpp endpoint when that provider was requested, so an
rem unconfigured machine does not silently claim a local server is in use.
if /I "%LLM_PROVIDER%"=="llama_cpp" if "%LLAMA_BASE_URL%"=="" set "LLAMA_BASE_URL=http://127.0.0.1:8080/v1"
if /I "%LLM_PROVIDER%"=="llama_cpp" if "%LLAMA_MODEL%"=="" set "LLAMA_MODEL=local-model"

set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    echo Python not found. Please install Python or add it to PATH.
    pause
    exit /b 1
)

echo Starting AI stock simulator from:
echo %cd%
echo.

if not "%MIMO_API_KEY%"=="" (
    echo Using Xiaomi Token Plan provider.
    echo Base URL: %MIMO_BASE_URL%
    echo Model: %MIMO_MODEL%
    echo.
) else if /I "%LLM_PROVIDER%"=="llama_cpp" (
    echo Using local llama.cpp server.
    echo Base URL: %LLAMA_BASE_URL%
    echo Model: %LLAMA_MODEL%
    echo.
) else if not "%LLAMA_BASE_URL%"=="" (
    echo Using local llama.cpp server.
    echo Base URL: %LLAMA_BASE_URL%
    echo Model: %LLAMA_MODEL%
    echo.
) else if "%GEMINI_API_KEY%"=="" (
    echo Warning: no LLM provider configured.
    echo Set MIMO_API_KEY or GEMINI_API_KEY, or run a local llama.cpp server:
    echo   set LLM_PROVIDER=llama_cpp   and start llama-server on port 8080.
    echo.
) else (
    echo Using Google Gemini provider.
    echo.
)

echo Launching simulation server...
rem Secrets stay in this process environment only: children inherit them, so
rem they never appear on a child command line (visible to other local users).
start "AI Stock Simulator - Simulation" /min cmd /c "%PY_CMD% simulation_server.py 1>simulation_stdout.log 2>simulation_stderr.log"

echo Launching web monitor...
start "AI Stock Simulator - Monitor" /min cmd /c "%PY_CMD% web_monitor.py 1>web_stdout.log 2>web_stderr.log"

echo Waiting for monitor to boot...
timeout /t 3 /nobreak >nul

echo Opening dashboard...
start "" "http://127.0.0.1:5000"

echo.
echo Started:
echo - simulation_server.py
echo - web_monitor.py
echo.
echo Logs:
echo - simulation_stdout.log
echo - simulation_stderr.log
echo - web_stdout.log
echo - web_stderr.log
echo.
echo Dashboard:
echo - http://127.0.0.1:5000
echo.
pause
