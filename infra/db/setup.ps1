param(
    [ValidateSet('Check', 'Apply')]
    [string]$Action = 'Check',
    [ValidatePattern('^\d+_[a-z_]+\.sql$')]
    [string]$Migration = '001_initial.sql'
)

$ErrorActionPreference = 'Stop'
function Get-DatabaseUrl {
    param([string]$EnvPath)

    # Prefer a value supplied by the shell over the project's .env file.
    if ($env:DATABASE_URL) {
        return $env:DATABASE_URL
    }
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return $null
    }

    $value = $null
    foreach ($line in Get-Content -LiteralPath $EnvPath) {
        if ($line -notmatch '^\s*DATABASE_URL\s*=\s*(.*?)\s*$') {
            continue
        }

        $value = $Matches[1]
        $hasDoubleQuotes = $value.StartsWith('"') -and $value.EndsWith('"')
        $hasSingleQuotes = $value.StartsWith("'") -and $value.EndsWith("'")
        if ($value.Length -ge 2 -and ($hasDoubleQuotes -or $hasSingleQuotes)) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }
    return $value
}

$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$envPath = Join-Path $projectRoot '.env'
$databaseUrl = Get-DatabaseUrl -EnvPath $envPath

if (-not $databaseUrl) {
    throw 'Set DATABASE_URL in the project-root .env file or your environment first.'
}
if ($databaseUrl -notmatch '^postgres(ql)?://') {
    throw 'DATABASE_URL must be a PostgreSQL connection URI.'
}

$psql = Get-Command psql -ErrorAction Stop
$connectionUri = $null
if (-not [uri]::TryCreate($databaseUrl, [System.UriKind]::Absolute, [ref]$connectionUri)) {
    throw 'DATABASE_URL is malformed. Expected postgresql://username:password@host:port/database?sslmode=require.'
}
# Keep credentials out of process arguments and restore the environment afterward.
$previousPassword = $env:PGPASSWORD
try {
    $connectionWithoutPassword = $databaseUrl
    if ($connectionUri.UserInfo.Contains(':')) {
        $credentials = $connectionUri.UserInfo -split ':', 2
        $env:PGPASSWORD = [uri]::UnescapeDataString($credentials[1])
        $connectionWithoutPassword = $databaseUrl -replace '^(postgres(?:ql)?://[^:/@]+):[^@]*@', '$1@'
    }

    $psqlArguments = @(
        "--dbname=$connectionWithoutPassword"
        '-X'  # Ignore local psql startup files.
        '-w'  # Report missing credentials instead of prompting.
        '--set=ON_ERROR_STOP=1'
    )

    if ($Action -eq 'Apply') {
        $migrationPath = Join-Path $PSScriptRoot $Migration
        $psqlArguments += "--file=$migrationPath"
    } else {
        $checkQuery = @'
SELECT current_database(), current_user, version();

SELECT extversion AS timescaledb_version
FROM pg_extension
WHERE extname = 'timescaledb';

SELECT tablename
FROM pg_tables
WHERE schemaname = 'backlogger'
ORDER BY tablename;

SELECT hypertable_schema, hypertable_name
FROM timescaledb_information.hypertables
WHERE hypertable_schema = 'backlogger';
'@
        $psqlArguments += "--command=$checkQuery"
    }

    & $psql.Source @psqlArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Database command failed. Check the TigerData connection details and credentials.'
    }
} finally {
    $env:PGPASSWORD = $previousPassword
}
