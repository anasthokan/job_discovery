# Restart Job Discovery on the IIS server so port 9001 answers again.
# Run in an elevated PowerShell ON 74.208.184.175:
#   cd C:\Projects\Job_Discovery
#   powershell -ExecutionPolicy Bypass -File .\deploy\restart-api.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$pm2 = Get-Command pm2 -ErrorAction SilentlyContinue
if ($pm2) {
  pm2 restart job-discovery --update-env
  if ($LASTEXITCODE -ne 0) {
    pm2 start (Join-Path $root "ecosystem.config.cjs")
  }
  pm2 save
} else {
  Get-NetTCPConnection -LocalPort 9001 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "start-job-discovery.bat" -WorkingDirectory $root -WindowStyle Minimized
}

Start-Sleep -Seconds 3
$health = Invoke-RestMethod -Uri "http://127.0.0.1:9001/api/health" -TimeoutSec 15
Write-Host ($health | ConvertTo-Json -Compress)
