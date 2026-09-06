$ErrorActionPreference = "Stop"

# Настройка доступа к веб-панели oss-web-panel через Tailscale (Windows).
# Аналог scripts/setup_tailscale.sh для серверов без bash.

$projectDir = Split-Path -Parent $PSScriptRoot
$compose = @("compose", "-f", (Join-Path $projectDir "docker-compose.yml"))

Push-Location $projectDir
try {
    if (-not (Test-Path ".env")) {
        throw ".env не найден. Создайте его из .env.example и задайте TAILSCALE_AUTH_KEY."
    }
    if (-not (Select-String -Path ".env" -Pattern '^TAILSCALE_AUTH_KEY=.+')) {
        throw "TAILSCALE_AUTH_KEY не задан в .env. Получите ключ: https://login.tailscale.com/admin/settings/keys"
    }

    Write-Host "Запускаем стек..."
    docker @compose up -d --build

    Write-Host "Ждём готовности Tailscale..."
    $ready = $false
    1..30 | ForEach-Object {
        if (-not $ready) {
            docker @compose exec -T tailscale tailscale status *> $null
            $ready = ($LASTEXITCODE -eq 0)
            if (-not $ready) { Start-Sleep -Seconds 2 }
        }
    }
    if (-not $ready) {
        throw "Tailscale не стал активным за 60 секунд. Выполните: docker compose logs tailscale"
    }

    Write-Host "Включаем HTTPS для панели..."
    # Сначала удаляем старую настройку serve (если была), затем настраиваем корректно
    try { docker @compose exec -T tailscale tailscale serve --delete http://web-admin:8000 } catch {}
    docker @compose exec -T tailscale tailscale serve http://web-admin:8000

    $status = (docker @compose exec -T tailscale tailscale status --json | Out-String) | ConvertFrom-Json
    $dns = if ($status.Self.DNSName) { $status.Self.DNSName.TrimEnd('.') } else { "oss-web-panel.<tailnet>.ts.net" }
    Write-Host "Готово. Панель доступна внутри tailnet: https://$dns/"
}
finally {
    Pop-Location
}