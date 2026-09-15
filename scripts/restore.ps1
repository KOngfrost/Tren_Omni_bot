# ============================================================
# Восстановление базы данных из резервной копии
# Скрипт для Windows (PowerShell)
# ============================================================
#
# Использование:
#   .\scripts\restore.ps1 [путь_к_файлу]
#
# Переменные окружения: POSTGRES_USER, POSTGRES_DB, POSTGRES_PASSWORD
# (обычно из .env)
#
# ============================================================

param(
    [string]$BackupFile = "",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $ProjectRoot ".env"
$COMPOSE_FILE = Join-Path $ProjectRoot "docker-compose.yml"

# Загрузка .env
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        if ($line -match "^\s*([^#=]+?)\s*=\s*(.*?)\s*$") {
            $k = $Matches[1].Trim()
            $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if ($k) { [Environment]::SetEnvironmentVariable($k, $v, "Process") }
        }
    }
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $c = switch ($Level) { "ERROR" { "Red" }; "WARNING" { "Yellow" }; default { "White" } }
    Write-Host "[$ts] [$Level] $Message" -ForegroundColor $c
}

# Проверка переменных
foreach ($v in @("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD")) {
    if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($v, "Process"))) {
        Write-Log "Нет переменной: $v" -Level "ERROR"; exit 1
    }
}
$PUSER = [Environment]::GetEnvironmentVariable("POSTGRES_USER", "Process")

if (-not (Test-Path $TARGET)) {
    Write-Log "Файл не найден: $TARGET" -Level "ERROR"; exit 1
}

# Подтверждение
if (-not $Force) {
    Write-Host ""
    Write-Host "!!! ВНИМАНИЕ !!!" -ForegroundColor Red
    Write-Host "Это действие перезапишет данные БД"

# Проверка целостности
Write-Log "Проверка целостности..."
try {
    try {
        gzip -t $TARGET 2>$null
        if ($LASTEXITCODE -ne 0) { throw "gzip не прошел" }
    } catch {
        $s = [System.IO.File]::OpenRead($TARGET)
        $g = New-Object System.IO.Compression.GzipStream($s, [System.IO.Compression.CompressionMode]::Decompress)
        $b = New-Object byte[] 1024
        $r = $g.Read($b, 0, 1024)
        $g.Close(); $s.Close()
        if ($r -eq 0) { throw "Файл поврежден" }
    }
    Write-Log "Архив цел"
} catch {
    Write-Log "Архив поврежден" -Level "ERROR"; exit 1
}

# Ожидание БД
Write-Log "Ожидание БД..."
$attempts = 0
while ($attempts -lt 30) {
    try {
        docker compose -f $COMPOSE_FILE exec -T db pg_isready -U $PUSER -d $PDB 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Log "БД готова"; break }
    } catch {}
    $attempts++
    if ($attempts -lt 30) { Start-Sleep -Seconds 2 }
}
if ($attempts -ge 30) {
    Write-Log "БД недоступна" -Level "ERROR"; exit 1
}

# Восстановление
Write-Log "Восстановление из $TARGET..."
$env:PGPASSWORD = $PASSWORD
try {
    $sql = if (Get-Command gzip -ErrorAction SilentlyContinue) {
        gzip -dc $TARGET 2>$null
    } else {
        $fs = [System.IO.File]::OpenRead($TARGET)
        $gs = New-Object System.IO.Compression.GzipStream($fs, [System.IO.Compression.CompressionMode]::Decompress)
        $ms = New-Object System.IO.MemoryStream
        $gs.CopyTo($ms)
        $gs.Close(); $fs.Close()
        [System.Text.Encoding]::UTF8.GetString($ms.ToArray())
    }
    
    $sql | docker compose -f $COMPOSE_FILE exec -T -e PGPASSWORD=$PASSWORD db `
        psql -U $PUSER -d $PDB -v ON_ERROR_STOP=1
    
    if ($LASTEXITCODE -ne 0) { throw "psql код выхода: $LASTEXITCODE" }
    Write-Log "Восстановление успешно"
} catch {
    Write-Log "Ошибка: $_" -Level "ERROR"; exit 1
}

# Проверка
Write-Log "Проверка..."
try {
    docker compose -f $COMPOSE_FILE exec -T db psql -U $PUSER -d $PDB `
        -c "SELECT current_database(), pg_postmaster_start_time(), now();"
    Write-Log "Проверка OK"
} catch {
    Write-Log "Проверка предупреждение" -Level "WARNING"
}

Write-Log "Готово"

    Write-Host "Из: $TARGET"
    Write-Host ""
    $c = Read-Host "Продолжить? (y/N)"
    if ($c -ne 'y' -and $c -ne 'Y') {
        Write-Log "Отменено"; exit 0
    }
}

# Проверка Docker
try { docker --version | Out-Null } catch {
    Write-Log "Docker не доступен" -Level "ERROR"; exit 1
}
try { docker compose version | Out-Null } catch {
    Write-Log "Docker Compose не доступен" -Level "ERROR"; exit 1
}

$PDB = [Environment]::GetEnvironmentVariable("POSTGRES_DB", "Process")
$PASSWORD = [Environment]::GetEnvironmentVariable("POSTGRES_PASSWORD", "Process")

# Файл для восстановления
if (-not [string]::IsNullOrEmpty($BackupFile)) {
    $TARGET = $BackupFile
    Write-Log "Используется: $TARGET"
} else {
    if (-not (Test-Path "$ProjectRoot\backups")) {
        Write-Log "Каталог backups не найден" -Level "ERROR"; exit 1
    }
    $files = Get-ChildItem "$ProjectRoot\backups" -Filter "oss_bot_*.sql.gz" | Sort-Object LastWriteTime -Descending
    if ($files.Count -eq 0) {
        Write-Log "Копии не найдены" -Level "ERROR"; exit 1
    }
    $TARGET = $files[0].FullName
    Write-Log "Последняя копия: $TARGET ($($files[0].LastWriteTime))"
}
