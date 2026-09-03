[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$ExpectedAlembicRevision = "e3f4a5b6c7d8",
    [ValidateRange(1, 10000)]
    [int]$MinimumPublicTableCount = 31
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$incompleteMarker = Join-Path $resolvedBackup ".incomplete"
if (Test-Path -LiteralPath $incompleteMarker) {
    throw "Refusing to verify an incomplete backup: $resolvedBackup"
}
$manifestPath = Join-Path $resolvedBackup "manifest.json"
$databaseDump = Join-Path $resolvedBackup "contentflow.dump"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Backup manifest is missing: $manifestPath"
}
if (-not (Test-Path -LiteralPath $databaseDump -PathType Leaf)) {
    throw "Database dump is missing: $databaseDump"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
$manifestVersion = [int]$manifest.format_version
if ($manifestVersion -notin @(1, 2)) {
    throw "Unsupported backup manifest version: $manifestVersion"
}
$actualHash = (Get-FileHash -LiteralPath $databaseDump -Algorithm SHA256).Hash
if ($actualHash -ne $manifest.database.sha256) {
    throw "Database dump SHA-256 does not match manifest"
}
if (
    $manifestVersion -ge 2 -and
    [string]$manifest.database.alembic_revision -ne $ExpectedAlembicRevision
) {
    throw (
        "Backup Alembic revision differs from the expected revision: " +
        "$($manifest.database.alembic_revision) " +
        "(expected $ExpectedAlembicRevision)"
    )
}

$objectRoot = $null
$expectedObjectEntries = @()
if ($manifest.objects.included) {
    if ([string]$manifest.objects.directory -ne "objects") {
        throw "Unsupported or unsafe object backup directory"
    }
    $objectRoot = Join-Path $resolvedBackup $manifest.objects.directory
    if (-not (Test-Path -LiteralPath $objectRoot -PathType Container)) {
        throw "Backup object directory is missing: $objectRoot"
    }
    $objectRoot = [System.IO.Path]::GetFullPath($objectRoot)
    $actualObjectFiles = @(
        Get-ChildItem -LiteralPath $objectRoot -Recurse -File
    )
    $actualObjectCount = $actualObjectFiles.Count
    if ($actualObjectCount -ne $manifest.objects.file_count) {
        throw "Object count does not match manifest"
    }
    $actualObjectSize = [long]0
    if ($actualObjectCount -gt 0) {
        $actualObjectSize = [long](
            $actualObjectFiles | Measure-Object -Property Length -Sum
        ).Sum
    }
    if (
        $manifestVersion -ge 2 -and
        $actualObjectSize -ne [long]$manifest.objects.size_bytes
    ) {
        throw "Object byte size does not match manifest"
    }

    if ($manifestVersion -ge 2) {
        $expectedObjectEntries = @($manifest.objects.files)
        if ($expectedObjectEntries.Count -ne $actualObjectCount) {
            throw "Object entry count does not match manifest"
        }
        $objectRootPrefix = $objectRoot
        if (
            -not $objectRootPrefix.EndsWith(
                [string][System.IO.Path]::DirectorySeparatorChar
            )
        ) {
            $objectRootPrefix += [System.IO.Path]::DirectorySeparatorChar
        }
        foreach ($entry in $expectedObjectEntries) {
            if (
                -not $entry.path -or
                [System.IO.Path]::IsPathRooted([string]$entry.path)
            ) {
                throw "Manifest contains an unsafe object path"
            }
            $relativePath = ([string]$entry.path).Replace(
                [char]"/",
                [System.IO.Path]::DirectorySeparatorChar
            )
            $candidate = [System.IO.Path]::GetFullPath(
                (Join-Path $objectRoot $relativePath)
            )
            if (
                -not $candidate.StartsWith(
                    $objectRootPrefix,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            ) {
                throw "Manifest object path escapes the backup directory"
            }
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                throw "Manifest object is missing: $($entry.path)"
            }
            $candidateInfo = Get-Item -LiteralPath $candidate
            if ($candidateInfo.Length -ne [long]$entry.size_bytes) {
                throw "Object size does not match manifest: $($entry.path)"
            }
            $candidateHash = (
                Get-FileHash -LiteralPath $candidate -Algorithm SHA256
            ).Hash
            if ($candidateHash -ne $entry.sha256) {
                throw "Object SHA-256 does not match manifest: $($entry.path)"
            }
        }
    }
}

function Invoke-DockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker command failed: docker $($Arguments -join ' ')"
    }
    return $output
}

$verificationId = [guid]::NewGuid().ToString("N").Substring(0, 16)
$databaseName = "contentflow_verify_$verificationId"
$bucketName = "contentflow-verify-$verificationId"
$containerDump = "contentflow-verify-$([guid]::NewGuid().ToString('N')).dump"
$verificationParent = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot ".contentflow\restore-verification")
)
$verificationRoot = Join-Path $verificationParent $verificationId
$bucketCleanupRequired = $false
$restoredObjectCount = 0
try {
    Invoke-DockerCommand @(
        "compose", "cp",
        $databaseDump,
        "postgres:/tmp/$containerDump"
    ) | Out-Null
    Invoke-DockerCommand @(
        "compose", "exec", "-T", "postgres",
        "createdb", "-U", "contentflow", $databaseName
    ) | Out-Null
    Invoke-DockerCommand @(
        "compose", "exec", "-T", "postgres",
        "pg_restore", "-U", "contentflow",
        "--exit-on-error", "--dbname", $databaseName,
        "/tmp/$containerDump"
    ) | Out-Null

    $tableCount = [int](
        Invoke-DockerCommand @(
            "compose", "exec", "-T", "postgres",
            "psql", "-U", "contentflow", "-d", $databaseName,
            "-tA", "-c",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
        )
    )
    $revision = ((
        Invoke-DockerCommand @(
            "compose", "exec", "-T", "postgres",
            "psql", "-U", "contentflow", "-d", $databaseName,
            "-tA", "-c", "SELECT version_num FROM alembic_version;"
        )
    ) | Out-String).Trim()

    if ($tableCount -lt $MinimumPublicTableCount) {
        throw "Restored database has too few public tables: $tableCount"
    }
    if ($revision -ne $ExpectedAlembicRevision) {
        throw (
            "Restored database is not at the expected Alembic revision: " +
            "$revision (expected $ExpectedAlembicRevision)"
        )
    }
    if ($manifestVersion -ge 2) {
        if ($revision -ne [string]$manifest.database.alembic_revision) {
            throw "Restored Alembic revision differs from the manifest"
        }
        if ($tableCount -ne [int]$manifest.database.public_table_count) {
            throw "Restored table count differs from the manifest"
        }
    }

    if ($manifest.objects.included) {
        New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
        $downloadRoot = Join-Path $verificationRoot "objects"
        New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
        $backupMount = [string]::Concat($resolvedBackup, ":/backup:ro")
        $verificationMount = [string]::Concat(
            $verificationRoot,
            ":/verify"
        )
        $aliasCommand = 'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"'
        $restoreCommand = (
            $aliasCommand +
            " && mc mb --ignore-existing local/$bucketName" +
            " && mc mirror --overwrite /backup/objects local/$bucketName" +
            " && mc mirror --overwrite local/$bucketName /verify/objects"
        )
        $bucketCleanupRequired = $true
        Invoke-DockerCommand @(
            "compose", "run", "--rm", "--no-deps",
            "-v", $backupMount,
            "-v", $verificationMount,
            "--entrypoint", "/bin/sh",
            "minio-init", "-c", $restoreCommand
        ) | Out-Null

        $restoredObjectFiles = @(
            Get-ChildItem -LiteralPath $downloadRoot -Recurse -File
        )
        $restoredObjectCount = $restoredObjectFiles.Count
        if ($restoredObjectCount -ne [int]$manifest.objects.file_count) {
            throw "Restored object count does not match manifest"
        }
        if ($manifestVersion -ge 2) {
            $downloadRootFull = [System.IO.Path]::GetFullPath($downloadRoot)
            $downloadRootPrefix = $downloadRootFull
            if (
                -not $downloadRootPrefix.EndsWith(
                    [string][System.IO.Path]::DirectorySeparatorChar
                )
            ) {
                $downloadRootPrefix += [System.IO.Path]::DirectorySeparatorChar
            }
            foreach ($entry in $expectedObjectEntries) {
                $relativePath = ([string]$entry.path).Replace(
                    [char]"/",
                    [System.IO.Path]::DirectorySeparatorChar
                )
                $candidate = [System.IO.Path]::GetFullPath(
                    (Join-Path $downloadRootFull $relativePath)
                )
                if (
                    -not $candidate.StartsWith(
                        $downloadRootPrefix,
                        [System.StringComparison]::OrdinalIgnoreCase
                    )
                ) {
                    throw "Restored object path escapes verification directory"
                }
                if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                    throw "Restored object is missing: $($entry.path)"
                }
                $candidateInfo = Get-Item -LiteralPath $candidate
                if ($candidateInfo.Length -ne [long]$entry.size_bytes) {
                    throw "Restored object size mismatch: $($entry.path)"
                }
                $candidateHash = (
                    Get-FileHash -LiteralPath $candidate -Algorithm SHA256
                ).Hash
                if ($candidateHash -ne $entry.sha256) {
                    throw "Restored object SHA-256 mismatch: $($entry.path)"
                }
            }
        }
    }

    Write-Output (
        "ContentFlow backup restore verification passed: " +
        "tables=$tableCount alembic=$revision objects=$restoredObjectCount"
    )
}
finally {
    & docker compose exec -T postgres dropdb -U contentflow --if-exists --force $databaseName 2>$null
    & docker compose exec -T postgres rm -f "/tmp/$containerDump" 2>$null
    if ($bucketCleanupRequired) {
        $cleanupCommand = (
            'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"' +
            " && mc rb --force local/$bucketName"
        )
        & docker compose run --rm --no-deps `
            --entrypoint /bin/sh `
            minio-init -c $cleanupCommand 2>$null | Out-Null
    }
    if (Test-Path -LiteralPath $verificationRoot) {
        $verificationRootFull = [System.IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $verificationRoot).Path
        )
        $verificationParentPrefix = $verificationParent
        if (
            -not $verificationParentPrefix.EndsWith(
                [string][System.IO.Path]::DirectorySeparatorChar
            )
        ) {
            $verificationParentPrefix += (
                [System.IO.Path]::DirectorySeparatorChar
            )
        }
        if (
            -not $verificationRootFull.StartsWith(
                $verificationParentPrefix,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Refusing to remove an unexpected verification directory"
        }
        Remove-Item -LiteralPath $verificationRootFull -Recurse -Force
    }
}
