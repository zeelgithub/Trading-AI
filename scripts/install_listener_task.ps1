# Install the always-on Telegram listener as a Windows Scheduled Task.
# Run this ONCE from an ELEVATED (Administrator) PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\install_listener_task.ps1
#
# Registers a task that starts the listener at logon and restarts it on failure.
# This is an alternative to the no-admin Startup-folder launcher
# (TradingTelegramListener.vbs); use ONE or the other, not both.

$proj = "C:\Users\zeela\Downloads\Claude-livetradingbot"
$py   = "$proj\.venv\Scripts\python.exe"
$log  = "$proj\logs\telegram.log"
$arg  = "/c `"`"$py`" -m scripts.run_telegram >> `"$log`" 2>&1`""

$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arg -WorkingDirectory $proj
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

Register-ScheduledTask -TaskName "ClaudeTradingTelegramListener" -Action $action `
    -Trigger $trigger -Settings $settings -RunLevel Highest -Force `
    -Description "Always-on Telegram listener for the trading bot"

Start-ScheduledTask -TaskName "ClaudeTradingTelegramListener"
Write-Host "Installed and started. State:" (Get-ScheduledTask -TaskName "ClaudeTradingTelegramListener").State
