# สำรองฐานข้อมูล + ไฟล์แนบการโทร ของระบบออกหมายเรียก (รันใน PowerShell ที่โฟลเดอร์โปรเจกต์ หรือจาก Task Scheduler)
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\ccid4\tools\backup.ps1
param(
  [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
  [string]$BackupDir = "C:\ccid4-backup",
  [int]$KeepDays = 30
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectDir
$envFile = Get-Content .env | Where-Object { $_ -match "^\s*POSTGRES_(USER|DB)=" }
$user = ($envFile | Where-Object { $_ -match "POSTGRES_USER" }) -replace ".*=", ""
$db = ($envFile | Where-Object { $_ -match "POSTGRES_DB" }) -replace ".*=", ""
if (-not $user) { $user = "summons" }
if (-not $db) { $db = "summons" }
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$target = Join-Path $BackupDir "summons_$stamp.dump"
# dump inside the container, then copy out (avoids PowerShell re-encoding binary output)
docker compose exec -T db pg_dump -U $user -Fc -f /tmp/backup.dump $db
docker compose cp db:/tmp/backup.dump $target
docker compose exec -T db rm -f /tmp/backup.dump
# files attached to call records (exports/summons files are re-created by the system, so not needed)
$files = Join-Path $BackupDir "summons_$($stamp)_files.tgz"
docker compose exec -T web sh -c "mkdir -p /app/media/call_attachments && tar -czf /tmp/files.tgz -C /app/media call_attachments"
docker compose cp web:/tmp/files.tgz $files
docker compose exec -T web rm -f /tmp/files.tgz
Write-Host "สำรองแล้ว: $target และ $files"
Get-ChildItem $BackupDir -Filter "summons_*" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } | Remove-Item
