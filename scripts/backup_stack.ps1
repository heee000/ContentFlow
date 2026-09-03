[CmdletBinding()]
param(
    [string]$Destination = "",
    [switch]$SkipObjects,
    [switch]$AllowLiveWrites,
    [string]$ExpectedAlembicRevision = "e3f4a5b6c7d8"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Destination) {
    $Destination = Join-Path $projectRoot ".contentflow\backups"
}
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = Join-Path $destinationRoot $timestamp

function Invoke-DockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker command failed: docker $($Arguments -join ' ')"
    }
}

$runningServices = @(& docker compose ps --status running --services)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect running Compose services"
}
$runningWriters = @(
    $runningServices | Where-Object { $_ -in @("api", "worker") }
)
if ($runningWriters.Count -gt 0 -and -not $AllowLiveWrites) {
    throw (
        "Refusing a non-quiesced backup while application writers are " +
        "running: $($runningWriters -join ', '). Stop API/Worker first or " +
        "pass -AllowLiveWrites to create an explicitly best-effort backup."
    )
}

$databaseRevision = (
    Invoke-DockerCommand @(
        "compose", "exec", "-T", "postgres",
        "psql", "-U", "contentflow", "-d", "contentflow",
        "-tA", "-c", "SELECT version_num FROM alembic_version;"
    ) | Out-String
).Trim()
$publicTableCount = [int](
    Invoke-DockerCommand @(
        "compose", "exec", "-T", "postgres",
        "psql", "-U", "contentflow", "-d", "contentflow",
        "-tA", "-c",
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
    )
)
if ($databaseRevision -ne $ExpectedAlembicRevision) {
    throw (
        "Refusing to back up unexpected Alembic revision: " +
        "$databaseRevision (expected $ExpectedAlembicRevision)"
    )
}

if (Test-Path -LiteralPath $backupPath) {
    throw "Refusing to reuse an existing backup path: $backupPath"
}
New-Item -ItemType Directory -Path $backupPath | Out-Null
$incompleteMarker = Join-Path $backupPath ".incomplete"
Set-Content -LiteralPath $incompleteMarker -Value "Backup creation has not completed." -Encoding utf8

$databaseDump = Join-Path $backupPath "contentflow.dump"
$containerDump = "contentflow-$([guid]::NewGuid().ToString('N')).dump"
try {
    Invoke-DockerCommand @(
        "compose", "exec", "-T", "postgres",
        "pg_dump", "-U", "contentflow", "-d", "contentflow",
        "--format=custom", "--file=/tmp/$containerDump"
    )
    Invoke-DockerCommand @(
        "compose", "exec", "-T", "postgres",
        "pg_restore", "--list", "/tmp/$containerDump"
    ) | Out-Null
    Invoke-DockerCommand @(
        "compose", "cp",
        "postgres:/tmp/$containerDump",
        $databaseDump
    )
}
finally {
    & docker compose exec -T postgres rm -f "/tmp/$containerDump" 2>$null
}

$objectEntries = @()
$objectCount = 0
$objectSizeBytes = [long]0
if (-not $SkipObjects) {
    $objectRoot = Join-Path $backupPath "objects"
    New-Item -ItemType Directory -Path $objectRoot -Force | Out-Null
    $mount = [string]::Concat($backupPath, ":/backup")
    $mirrorCommand = 'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc mirror --overwrite local/contentflow /backup/objects'
    Invoke-DockerCommand @(
        "compose", "run", "--rm", "--no-deps",
        "-v", $mount,
        "--entrypoint", "/bin/sh",
        "minio-init", "-c", $mirrorCommand
    )
    $objectFiles = @(
        Get-ChildItem -LiteralPath $objectRoot -Recurse -File |
            Sort-Object FullName
    )
    $objectCount = $objectFiles.Count
    if ($objectCount -gt 0) {
        $objectSizeBytes = [long](
            $objectFiles | Measure-Object -Property Length -Sum
        ).Sum
    }
    $objectEntries = @(
        foreach ($objectFile in $objectFiles) {
            $relativePath = (
                $objectFile.FullName.Substring($objectRoot.Length) -replace "\\", "/"
            ).TrimStart("/")
            [ordered]@{
                path = $relativePath
                sha256 = (
                    Get-FileHash -LiteralPath $objectFile.FullName -Algorithm SHA256
                ).Hash
                size_bytes = $objectFile.Length
            }
        }
    )
}

$databaseHash = (Get-FileHash -LiteralPath $databaseDump -Algorithm SHA256).Hash
$manifest = [ordered]@{
    format_version = 2
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    consistency = [ordered]@{
        mode = if ($AllowLiveWrites) {
            "best-effort-live-writes"
        }
        else {
            "compose-application-writers-stopped"
        }
        running_services = $runningServices
    }
    database = [ordered]@{
        filename = "contentflow.dump"
        format = "postgresql-custom"
        sha256 = $databaseHash
        size_bytes = (Get-Item -LiteralPath $databaseDump).Length
        alembic_revision = $databaseRevision
        public_table_count = $publicTableCount
    }
    objects = [ordered]@{
        included = (-not $SkipObjects)
        directory = if ($SkipObjects) { $null } else { "objects" }
        file_count = $objectCount
        size_bytes = $objectSizeBytes
        files = $objectEntries
    }
    restore_warning = "Restore is destructive. Validate this backup in an isolated database first."
}
$manifest |
    ConvertTo-Json -Depth 7 |
    Set-Content -LiteralPath (Join-Path $backupPath "manifest.json") -Encoding utf8
Remove-Item -LiteralPath $incompleteMarker -Force

Write-Output "ContentFlow backup created: $backupPath"
