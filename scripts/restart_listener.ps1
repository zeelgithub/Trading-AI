# Restart the always-on Telegram listener (no admin needed):
#     powershell -ExecutionPolicy Bypass -File scripts\restart_listener.ps1
#
# Stops any running listener, then relaunches it the same way logon does --
# hidden, via the Startup-folder .vbs -> scripts\run_telegram.bat, logging to
# logs\telegram.log. Note: a venv python.exe is a launcher shim, so ONE
# listener shows as TWO python processes (shim + interpreter); that's normal.

$ErrorActionPreference = "Stop"

Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*run_telegram*" } |
    ForEach-Object {
        Write-Host "stopping listener PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force
    }

Start-Sleep -Seconds 1

$vbs = Join-Path ([Environment]::GetFolderPath("Startup")) "TradingTelegramListener.vbs"
if (-not (Test-Path $vbs)) {
    Write-Host "Startup launcher not found ($vbs) -- starting directly."
    Start-Process -WindowStyle Hidden -WorkingDirectory $PSScriptRoot\.. `
        -FilePath "cmd.exe" -ArgumentList "/c", "scripts\run_telegram.bat"
} else {
    wscript.exe $vbs
}

Start-Sleep -Seconds 5
$procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*run_telegram*" })
if ($procs.Count -gt 0) {
    Write-Host "listener running (PIDs: $($procs.ProcessId -join ', '))"
} else {
    Write-Host "listener did NOT start -- check logs\telegram.log"
    exit 1
}
