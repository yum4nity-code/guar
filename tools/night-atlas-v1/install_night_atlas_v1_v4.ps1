$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/night-atlas-v1"
$Tmp = Join-Path $env:TEMP ("night_atlas_v1_v4_" + [guid]::NewGuid().ToString("N"))

$Expected = @{
    "night_atlas_v1.py"            = "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860"
    "night_atlas_analyze_v1.py"    = "8d03c60910bf4e7e747dc796b089a8a20d95c25f182f67078e073611908d81d1"
    "night_atlas_cross_scan_v1.py" = "292a4eb96f6a56a5528f8dfd5484063fdd648a08536f4cb071f5b9e3cfe9d9c0"
    "run_night_atlas_v1.ps1"       = "5b8f42030811033c8b7aec79e340fa2cc5bbcba440723dec62a9c4b8a224721b"
}

New-Item -ItemType Directory -Force -Path $Campaign | Out-Null
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

function Download-File([string]$Relative, [string]$TargetName) {
    Write-Host "DOWNLOAD $Relative"
    Invoke-WebRequest "$Base/$Relative" -OutFile (Join-Path $Tmp $TargetName)
}

function Append-Bytes([IO.Stream]$Stream, [string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $Stream.Write($bytes, 0, $bytes.Length)
}

try {
    foreach ($part in @("engine.part01.txt","engine.part02.txt","engine.part03.txt","engine.part04.txt")) {
        Download-File "chunks/$part" $part
    }

    $engineTmp = Join-Path $Tmp "night_atlas_v1.py"
    $stream = [IO.File]::Open($engineTmp, [IO.FileMode]::Create, [IO.FileAccess]::Write)
    try {
        Append-Bytes $stream (Join-Path $Tmp "engine.part01.txt")
        # The original arbitrary 7000-byte split ended after four indentation spaces.
        # GitHub text storage dropped those trailing spaces from part01, so restore them
        # explicitly before part02. Final SHA256 below proves exact reconstruction.
        $indent = [Text.Encoding]::ASCII.GetBytes("    ")
        $stream.Write($indent, 0, $indent.Length)
        Append-Bytes $stream (Join-Path $Tmp "engine.part02.txt")
        Append-Bytes $stream (Join-Path $Tmp "engine.part03.txt")
        Append-Bytes $stream (Join-Path $Tmp "engine.part04.txt")
    }
    finally {
        $stream.Dispose()
    }

    foreach ($part in @("analyze.part01.txt","analyze.part02.txt")) {
        Download-File "chunks/$part" $part
    }

    $analyzeTmp = Join-Path $Tmp "night_atlas_analyze_v1.py"
    $stream = [IO.File]::Open($analyzeTmp, [IO.FileMode]::Create, [IO.FileAccess]::Write)
    try {
        Append-Bytes $stream (Join-Path $Tmp "analyze.part01.txt")
        Append-Bytes $stream (Join-Path $Tmp "analyze.part02.txt")
    }
    finally {
        $stream.Dispose()
    }

    Download-File "night_atlas_cross_scan_v1.py" "night_atlas_cross_scan_v1.py"
    Download-File "run_night_atlas_v1.ps1" "run_night_atlas_v1.ps1"

    Write-Host ""
    Write-Host "===== SHA256 VERIFICATION ====="

    foreach ($name in $Expected.Keys) {
        $path = Join-Path $Tmp $name
        $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        $wanted = $Expected[$name].ToLower()
        Write-Host "$name"
        Write-Host "  expected: $wanted"
        Write-Host "  actual  : $actual"
        if ($actual -ne $wanted) {
            throw "HASH MISMATCH: $name"
        }
    }

    Write-Host "ALL FILE HASHES VERIFIED"

    foreach ($name in $Expected.Keys) {
        Copy-Item (Join-Path $Tmp $name) (Join-Path $Campaign $name) -Force
    }

    foreach ($name in @("night_atlas_v1.py","night_atlas_analyze_v1.py","night_atlas_cross_scan_v1.py")) {
        & python -m py_compile (Join-Path $Campaign $name)
        if ($LASTEXITCODE -ne 0) {
            throw "PYTHON SYNTAX FAIL: $name"
        }
    }

    Write-Host ""
    Write-Host "============================================================"
    Write-Host " GUARDIAN NIGHT ATLAS V1 INSTALLED AND VERIFIED"
    Write-Host "============================================================"
    Write-Host "17 families / 22 variants"
    Write-Host "2017-2022 DISCOVERY ONLY"
    Write-Host "2023+ CLOSED"
    Write-Host "2026 CLOSED"
    Write-Host "============================================================"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
