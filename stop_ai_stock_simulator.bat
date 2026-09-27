@echo off
setlocal

echo Stopping AI stock simulator processes...

for %%P in (simulation_server.py web_monitor.py) do (
    powershell -NoProfile -Command ^
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '%%P' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)

echo.
echo Stopped:
echo - simulation_server.py
echo - web_monitor.py
echo.
pause
