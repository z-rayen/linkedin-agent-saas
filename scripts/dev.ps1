# Windows equivalent of the Makefile. Usage (from the repository root):
#   .\scripts\dev.ps1 install
#   .\scripts\dev.ps1 test
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "db-up", "db-down", "migrate", "run", "test", "cov", "lint", "format", "typecheck", "check", "docker-up", "docker-down")]
    [string]$Task = "help"
)

$ErrorActionPreference = "Stop"
$Backend = Join-Path $PSScriptRoot "..\backend" | Resolve-Path

function Invoke-Uv {
    param([string[]]$Arguments)
    & uv --directory $Backend @Arguments
    if ($LASTEXITCODE -ne 0) { throw "uv $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

function Invoke-Compose {
    param([string[]]$Arguments)
    & docker compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

switch ($Task) {
    "install"     { Invoke-Uv @("sync", "--frozen") }
    "db-up"       { Invoke-Compose @("up", "-d", "db") }
    "db-down"     { Invoke-Compose @("down") }
    "migrate"     { Invoke-Uv @("run", "alembic", "upgrade", "head") }
    "run"         { Invoke-Uv @("run", "uvicorn", "linkedin_agent.main:app", "--reload", "--app-dir", "src") }
    "test"        { Invoke-Uv @("run", "pytest", "-q") }
    "cov"         { Invoke-Uv @("run", "pytest", "--cov") }
    "lint"        { Invoke-Uv @("run", "ruff", "check", "."); Invoke-Uv @("run", "ruff", "format", "--check", ".") }
    "format"      { Invoke-Uv @("run", "ruff", "format", "."); Invoke-Uv @("run", "ruff", "check", "--fix", ".") }
    "typecheck"   { Invoke-Uv @("run", "mypy") }
    "check"       { Invoke-Uv @("run", "ruff", "check", "."); Invoke-Uv @("run", "ruff", "format", "--check", "."); Invoke-Uv @("run", "mypy"); Invoke-Uv @("run", "pytest", "--cov") }
    "docker-up"   { Invoke-Compose @("up", "-d", "--build") }
    "docker-down" { Invoke-Compose @("down") }
    default {
        Write-Host "Tasks: install, db-up, db-down, migrate, run, test, cov, lint, format, typecheck, check, docker-up, docker-down"
    }
}
