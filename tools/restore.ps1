# กู้คืนฐานข้อมูลจากไฟล์สำรอง (ข้อมูลปัจจุบันจะถูกแทนที่)
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\ccid4\tools\restore.ps1 -File C:\ccid4-backup\summons_20260209_0200.dump
param(
  [Parameter(Mandatory = $true)][string]$File,
  [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = "Stop"
Set-Location $ProjectDir
$envFile = Get-Content .env | Where-Object { $_ -match "^\s*POSTGRES_(USER|DB)=" }
$user = ($envFile | Where-Object { $_ -match "POSTGRES_USER" }) -replace ".*=", ""
$db = ($envFile | Where-Object { $_ -match "POSTGRES_DB" }) -replace ".*=", ""
if (-not $user) { $user = "summons" }
if (-not $db) { $db = "summons" }
$answer = Read-Host "ข้อมูลปัจจุบันจะถูกแทนที่ด้วย $File พิมพ์ YES เพื่อยืนยัน"
if ($answer -ne "YES") { Write-Host "ยกเลิก"; exit 1 }
docker compose stop web worker
docker compose cp $File db:/tmp/restore.dump
docker compose exec -T db pg_restore -U $user -d $db --clean --if-exists /tmp/restore.dump
docker compose exec -T db rm -f /tmp/restore.dump
docker compose start web worker
Write-Host "กู้คืนเรียบร้อย"
