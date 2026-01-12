# -------- CONFIG --------
$LocalBackupRoot = "D:\CarLogsBackup"
$TempPullDir     = "C:\AdayoLog_temp"
$DeviceLogPath   = "/mnt/sdcard/AdayoLog"
$KeepDays        = 30

# -------- CHECK ADB --------
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Write-Host "adb not found in PATH." -ForegroundColor Red
    exit 1
}

# -------- PREPARE PATHS --------
$today = (Get-Date).ToString("yyyy-MM-dd")
$BackupDir = Join-Path $LocalBackupRoot $today

if (Test-Path $TempPullDir) { Remove-Item -Recurse -Force $TempPullDir }
New-Item -ItemType Directory -Path $TempPullDir | Out-Null

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}

# -------- ADB PULL --------
Write-Host "Pulling logs from device..." -ForegroundColor Cyan
adb pull $DeviceLogPath $TempPullDir

# -------- ROBOCOPY --------
Write-Host "Copying logs to backup folder..." -ForegroundColor Cyan
$robocopyLog = Join-Path $BackupDir "robocopy.log"

$robocopyArgs = @(
    $TempPullDir, $BackupDir,
    "/E",
    "/Z",
    "/MT:16",
    "/R:2",
    "/W:1",
    "/LOG:$robocopyLog"
)

Start-Process -FilePath robocopy -ArgumentList $robocopyArgs -NoNewWindow -Wait

# -------- ANALYSIS --------
$summaryFile = Join-Path $BackupDir "analysis_summary.txt"
"==== Crash Analysis ($today) ====" | Out-File -FilePath $summaryFile -Encoding UTF8

$patterns = @{
    "TOMBSTONE" = "tombstone";
    "ANR" = "ANR";
    "FATAL" = "FATAL EXCEPTION";
    "SIG" = "SIGSEGV|SIGABRT|SIGKILL";
}

foreach ($key in $patterns.Keys) {
    Add-Content -Path $summaryFile -Value "`n[$key] matches:`n"
    $matches = Select-String -Path "$BackupDir\*" -Pattern $patterns[$key] -CaseSensitive:$false -ErrorAction SilentlyContinue
    if ($matches) {
        $matches | Select-Object -First 20 | ForEach-Object {
            Add-Content -Path $summaryFile -Value "$($_.Path):$($_.LineNumber): $($_.Line)"
        }
    } else {
        Add-Content -Path $summaryFile -Value "None found.`n"
    }
}

# -------- ZIP --------
$zipFile = Join-Path $LocalBackupRoot ($today + ".zip")
if (Test-Path $zipFile) { Remove-Item $zipFile -Force }
Compress-Archive -LiteralPath $BackupDir -DestinationPath $zipFile

# -------- DELETE OLD --------
Get-ChildItem $LocalBackupRoot -Directory |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName }

Get-ChildItem $LocalBackupRoot -Filter "*.zip" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
    ForEach-Object { Remove-Item -Force $_.FullName }

# -------- DONE --------
Write-Host "All done. Summary saved to: $summaryFile" -ForegroundColor Green
