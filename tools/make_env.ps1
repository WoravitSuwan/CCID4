# Create .env for Docker with random secrets (ASCII-only script; .env keeps UTF-8 Thai comments).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"
if (Test-Path $envPath) { exit 0 }
function New-Secret([int]$n) {
  $chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789".ToCharArray()
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $bytes = New-Object byte[] $n
  $rng.GetBytes($bytes)
  -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
$text = [System.IO.File]::ReadAllText((Join-Path $root ".env.example"), $utf8)
$adminPw = New-Secret 12
$text = $text -replace "(?m)^DJANGO_SECRET_KEY=.*$", ("DJANGO_SECRET_KEY=" + (New-Secret 60))
$text = $text -replace "(?m)^POSTGRES_PASSWORD=.*$", ("POSTGRES_PASSWORD=" + (New-Secret 24))
$text = $text -replace "(?m)^DJANGO_SUPERUSER_PASSWORD=.*$", ("DJANGO_SUPERUSER_PASSWORD=" + $adminPw)
[System.IO.File]::WriteAllText($envPath, $text, $utf8)
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null
[System.IO.File]::WriteAllText((Join-Path $root "data\admin-password.txt"), "admin / $adminPw`r`n", $utf8)
Write-Host "Created .env  (administrator: admin / $adminPw  - also saved in data\admin-password.txt)"
