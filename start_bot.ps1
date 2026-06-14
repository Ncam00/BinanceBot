# BinanceBot startup launcher
# Runs on Windows login via Task Scheduler

$python  = "C:\python314\python.exe"
$botDir  = "C:\BinanceBot"

# Kill any stale instances
Stop-Process -Name "python","python3","python3.14" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# Start monitor (visible window so login prompt is seen if session expired)
Start-Process -FilePath $python `
    -ArgumentList "-u $botDir\copy_monitor.py" `
    -WorkingDirectory $botDir `
    -WindowStyle Normal

Start-Sleep -Seconds 2

# Start dashboard (minimised — accessible at http://localhost:8050)
Start-Process -FilePath $python `
    -ArgumentList "-u $botDir\dashboard.py" `
    -WorkingDirectory $botDir `
    -WindowStyle Minimized
