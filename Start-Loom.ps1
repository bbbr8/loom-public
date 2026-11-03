$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Install and start Docker Desktop with Linux containers, then run this launcher again.'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, then run this launcher again.' }
$configPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $configPath)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $key = [Convert]::ToBase64String($bytes)
    $stream = [IO.File]::Open($configPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $writer = New-Object IO.StreamWriter($stream)
        try { $writer.WriteLine('LOOM_API_KEY=' + $key) } finally { $writer.Dispose() }
    } finally { $stream.Dispose() }
}
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw 'Loom failed to start. Inspect docker compose logs.' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:10000/health' -TimeoutSec 2
        if ($health.ok -and $health.service -eq 'loom') { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'Loom has not become healthy. Inspect docker compose logs.' }
docker compose exec -T loom python -B check_install.py
if ($LASTEXITCODE -ne 0) { throw 'Loom started, but the synthetic analysis check failed. Inspect docker compose logs.' }
$keyLine = Get-Content -LiteralPath $configPath | Where-Object { $_ -match '^LOOM_API_KEY=' } | Select-Object -First 1
if (-not $keyLine) { throw 'The .env file needs a LOOM_API_KEY value.' }
Set-Clipboard -Value $keyLine.Substring(13)
Write-Host 'Loom is ready. Your access key is on the clipboard. Paste it into the Access key field. Copy something else afterward to clear the key from your clipboard.'
Start-Process 'http://127.0.0.1:10000'
