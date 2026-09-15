# ============================================================
# Ежедневное резервное копирование базы данных PostgreSQL
# Скрипт для Windows (PowerShell)
# ============================================================
#
# Использование (на сервере):
#   .\scripts\backup.ps1
#
# Переменные окружения:
#   BACKUP_DIR      -- каталог для копий (по умолчанию .\backups)
#   DAILY_KEEP      -- сколько дневных копий хранить (по умолчанию 7)
#   WEEKLY_KEEP     -- сколько недельных копий хранить (по умолчанию 4)
#   MONTHLY_KEEP    -- сколько месячных копий хранить (по умолчанию 3)
#
# Удаленная выгрузка (ОБЯЗАТЕЛЬНА для production):
#   BACKUP_REMOTE=RCLONE_REMOTE:path, например BACKUP_REMOTE=myb2:oss_bot_backups
#   Для этого нужен настроенный rclone (https://rclone.org). Если переменная
#   не задана, скрипт только предупреждает: локальная копия не защищает
#   от потери сервера.
#
# Настройка Task Scheduler (ежедневно в 03:00):
#   schtasks /create /tn "OSS_Bot_Backup" /tr "powershell -ExecutionPolicy Bypass -File C:\path\to\student_bot\scripts\backup.ps1" /sc daily /st 03:00
#
# ============================================================

param(
    [string]$BackupDirOverride = "",
    [int]$DailyKeepOverride = 0,

# Настройки резервного копирования
$BACKUP_DIR = if (-not [string]::IsNullOrEmpty($BackupDirOverride)) { $BackupDirOverride } else {
    [Environment]::GetEnvironmentVariable("BACKUP_DIR", "Process")
}
if ([string]::IsNullOrEmpty($BACKUP_DIR)) { $BACKUP_DIR = Join-Path $ProjectRoot "backups" }

$DAILY_KEEP = if ($DailyKeepOverride -gt 0) { $DailyKeepOverride } else {
    [int]([Environment]::GetEnvironmentVariable("DAILY_KEEP", "Process") ?? "7")
}
$WEEKLY_KEEP = if ($WeeklyKeepOverride -gt 0) { $WeeklyKeepOverride } else {
    [int]([Environment]::GetEnvironmentVariable("WEEKLY_KEEP", "Process") ?? "4")
}
$MONTHLY_KEEP = if ($MonthlyKeepOverride -gt 0) { $MonthlyKeepOverride } else {
    [int]([Environment]::GetEnvironmentVariable("MONTHLY_KEEP", "Process") ?? "3")
}
$BACKUP_REMOTE = [Environment]::GetEnvironmentVariable("BACKUP_REMOTE", "Process")

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $color = switch ($Level) { "ERROR" { "Red" }; "WARNING" { "Yellow" }; default { "White" } }
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $color
}

# Создание каталога
if (-not (Test-Path $BACKUP_DIR)) {
    try { New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null } catch {
        Write-Log "Не удалось создать каталог: $BACKUP_DIR" -Level "ERROR"; exit 1

# Ожидание готовности БД (до 60 сек)
Write-Log "Ожидание готовности БД..."
$maxAttempts = 30
$attempt = 0
while ($attempt -lt $maxAttempts) {
    try {
        docker compose -f $COMPOSE_FILE exec -T db pg_isready -U $POSTGRES_USER -d $POSTGRES_DB 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Log "База данных готова"; break }
    } catch {}
    $attempt++
    if ($attempt -lt $maxAttempts) { Start-Sleep -Seconds 2 }
}
if ($attempt -eq $maxAttempts) {
    Write-Log "БД не стала доступной" -Level "ERROR"; exit 1
}

# Создание резервной копии
Write-Log "Создание резервной копии..."
$env:PGPASSWORD = $POSTGRES_PASSWORD
$tmpFile = "$FILE.tmp"
try {
    docker compose -f $COMPOSE_FILE exec -T -e PGPASSWORD=$POSTGRES_PASSWORD db `
        pg_dump -U $POSTGRES_USER -d $POSTGRES_DB --no-owner --clean --if-exists > $tmpFile
    
    if ($LASTEXITCODE -ne 0) { throw "pg_dump завершился с кодом $LASTEXITCODE" }
    
    # Сжатие
    try {
        gzip -c $tmpFile > $FILE 2>$null
        if ($LASTEXITCODE -ne 0) { throw "gzip не удалось" }
    } catch {
        # Сжатие через .NET если gzip недоступен
        $inStream = [System.IO.File]::OpenRead($tmpFile)

# Проверка целостности
Write-Log "Проверка целостности архива..."
try {
    try {
        gzip -t $FILE 2>$null
        if ($LASTEXITCODE -ne 0) { throw "gzip -t не прошел" }
    } catch {
        # Проверка через .NET
        $stream = [System.IO.File]::OpenRead($FILE)
        $gzip = New-Object System.IO.Compression.GzipStream($stream, [System.IO.Compression.CompressionMode]::Decompress)
        $buf = New-Object byte[] 1024
        $read = $gzip.Read($buf, 0, 1024)
        $gzip.Close(); $stream.Close()
        if ($read -eq 0) { throw "Файл поврежден" }
    }
    Write-Log "Архив цел"
} catch {
    Write-Log "Архив поврежден: $FILE" -Level "ERROR"
    if (Test-Path $FILE) { Remove-Item $FILE -Force }
    exit 1
}

# Размер файла
$size = (Get-Item $FILE).Length
$sizeStr = if ($size -ge 1GB) { "{0:N2} GB" -f ($size/1GB) } elseif ($size -ge 1MB) { "{0:N2} MB" -f ($size/1MB) } else { "$size bytes" }
Write-Log "Резервная копия готова: $sizeStr"

# Ротация
Write-Log "Ротация (дневные=$DAILY_KEEP, недельные=$WEEKLY_KEEP, месячные=$MONTHLY_KEEP)..."
$cutoff = (Get-Date).AddDays(-$DAILY_KEEP)

foreach ($f in Get-ChildItem $BACKUP_DIR -Filter "oss_bot_*.sql.gz") {
    $m = [regex]::Match($f.Name, "oss_bot_(\d{4}-\d{2}-\d{2})\.sql\.gz")
    if ($m.Success) {
        $d = [DateTime]::Parse($m.Groups[1].Value)
        $dow = $d.DayOfWeek
        $dom = $d.Day
        $age = (New-TimeSpan -Start $d -End (Get-Date)).Days
        
        $isWeekly = ($dow -eq [DayOfWeek]::Sunday -and $age -le ($WEEKLY_KEEP * 7))
        $isMonthly = ($dom -eq 1 -and $age -le ($MONTHLY_KEEP * 30))
        
        if ($isWeekly -or $isMonthly) {
            $f.LastWriteTime = (Get-Date)  # Защита от удаления
        } elseif ($f.LastWriteTime -lt $cutoff) {
            Write-Log "Удаление: $($f.Name)"
            Remove-Item $f.FullName -Force
        }
    }
}
Write-Log "Ротация завершена"

# Удаленная выгрузка (rclone)
if (-not [string]::IsNullOrEmpty($BACKUP_REMOTE)) {
    Write-Log "Выгрузка в $BACKUP_REMOTE..."
    if (Get-Command rclone -ErrorAction SilentlyContinue) {
        rclone copy --progress --max-age "${DAILY_KEEP}d" $BACKUP_DIR $BACKUP_REMOTE
        Write-Log "Выгрузка завершена"
    } else {
        Write-Log "rclone не установлен - выгрузка пропущена" -Level "WARNING"
    }
} else {
    Write-Log "BACKUP_REMOTE не задан - удаленной копии нет. Рекомендация: настройте rclone" -Level "WARNING"
}

Write-Log "Сценарий завершен"

        $outStream = [System.IO.File]::Create($FILE)
        $gzipStream = New-Object System.IO.Compression.GzipStream($outStream, [System.IO.Compression.CompressionMode]::Compress)
        $inStream.CopyTo($gzipStream)
        $gzipStream.Close(); $outStream.Close(); $inStream.Close()
    }
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    Write-Log "Резервная копия создана"
} catch {
    Write-Log "Ошибка создания копии: $_" -Level "ERROR"
    if (Test-Path $FILE) { Remove-Item $FILE -Force }
    exit 1
}

    }
}

$STAMP = Get-Date -Format "yyyy-MM-dd"
$FILE = Join-Path $BACKUP_DIR "oss_bot_${STAMP}.sql.gz"
Write-Log "Начало резервного копирования -> $FILE"

# Проверка docker-compose.yml
if (-not (Test-Path $COMPOSE_FILE)) {
    Write-Log "docker-compose.yml не найден: $COMPOSE_FILE" -Level "ERROR"; exit 1
}

# Проверка Docker
try { $null = docker --version } catch {
    Write-Log "Docker не доступен" -Level "ERROR"; exit 1
}
try { $null = docker compose version } catch {
    Write-Log "Docker Compose не доступен" -Level "ERROR"; exit 1
}

    [int]$WeeklyKeepOverride = 0,
    [int]$MonthlyKeepOverride = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Пути
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $ProjectRoot ".env"

# Загрузка переменных из .env
if (Test-Path $EnvFile) {
    $envContent = Get-Content $EnvFile -Encoding UTF8
    foreach ($line in $envContent) {
        if ($line -match "^\s*([^#=]+?)\s*=\s*(.*?)\s*$") {
            $key = $Matches[1].Trim()
            $value = $Matches[2].Trim()
            if ($value -match '^"(.*)"$') { $value = $Matches[1] }
            if (-not [string]::IsNullOrEmpty($key)) {
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
    }
}

# Проверка обязательных переменных
$requiredVars = @("POSTGRES_USER", "POSTGRES_DB")
foreach ($var in $requiredVars) {
    if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($var, "Process"))) {
        Write-Host "Ошибка: $var не задана" -ForegroundColor Red
        exit 1
    }
}

$POSTGRES_USER = [Environment]::GetEnvironmentVariable("POSTGRES_USER", "Process")
$POSTGRES_DB = [Environment]::GetEnvironmentVariable("POSTGRES_DB", "Process")
$POSTGRES_PASSWORD = [Environment]::GetEnvironmentVariable("POSTGRES_PASSWORD", "Process")
$COMPOSE_FILE = Join-Path $ProjectRoot "docker-compose.yml"
