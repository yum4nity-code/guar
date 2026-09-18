$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Target = Join-Path $Campaign "f14_v2_oos_2023_2025_v2.py"
$Tmp = Join-Path $env:TEMP ("f14_v2_oos_v2_audit_" + [guid]::NewGuid().ToString("N"))

$ExpectedFinal = "bc083ac484f3f26c034fd4696fdc82605b83adb61c121d1d5f9899df47746da1"
$ExpectedDeps = @{
    "data_loader_v1.py" = "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b"
    "preflight_v1.py"   = "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354"
    "night_atlas_v1.py" = "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860"
}

$Parts = @(
    @{ Name = "part01.txt"; Blob = "6103e47a601fc30e44e119936f10380e9468e655" },
    @{ Name = "part02.txt"; Blob = "210d32f5338721449191d6ff98f04a9f055622a6" },
    @{ Name = "part03.txt"; Blob = "c14f64541f10dd976648e8df95b7b8ea496eed53" },
    @{ Name = "part04.txt"; Blob = "9bf4e63b9385b69a1e7c5e9a259cf47c69eac579" }
)

$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/night-atlas-v1/f14-v2-oos-2023-2025-v2"

function Get-GitBlobSha1([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $header = [Text.Encoding]::ASCII.GetBytes("blob $($bytes.Length)`0")
    $all = New-Object byte[] ($header.Length + $bytes.Length)
    [Array]::Copy($header, 0, $all, 0, $header.Length)
    [Array]::Copy($bytes, 0, $all, $header.Length, $bytes.Length)
    $sha1 = [Security.Cryptography.SHA1]::Create()
    try {
        return ([BitConverter]::ToString($sha1.ComputeHash($all))).Replace("-", "").ToLower()
    }
    finally {
        $sha1.Dispose()
    }
}

if (-not (Test-Path $Campaign)) {
    throw "Campaign directory missing: $Campaign"
}

New-Item -ItemType Directory -Path $Tmp | Out-Null

try {
    foreach ($p in $Parts) {
        $dest = Join-Path $Tmp $p.Name
        $url = "$Base/$($p.Name)"
        Invoke-WebRequest $url -OutFile $dest
        $actualBlob = Get-GitBlobSha1 $dest
        Write-Host "$($p.Name) expected blob: $($p.Blob)"
        Write-Host "$($p.Name) actual blob  : $actualBlob"
        if ($actualBlob -ne $p.Blob) {
            throw "Git blob mismatch: $($p.Name)"
        }
    }

    $assembled = Join-Path $Tmp "f14_v2_oos_2023_2025_v2.py"
    $stream = [IO.File]::Open($assembled, [IO.FileMode]::Create, [IO.FileAccess]::Write)
    try {
        foreach ($p in $Parts) {
            $bytes = [IO.File]::ReadAllBytes((Join-Path $Tmp $p.Name))
            $stream.Write($bytes, 0, $bytes.Length)
        }
    }
    finally {
        $stream.Dispose()
    }

    $actualFinal = (Get-FileHash $assembled -Algorithm SHA256).Hash.ToLower()
    Write-Host "FINAL expected SHA256: $ExpectedFinal"
    Write-Host "FINAL actual SHA256  : $actualFinal"
    if ($actualFinal -ne $ExpectedFinal) {
        throw "Final script hash mismatch"
    }

    & python -m py_compile $assembled
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax check failed"
    }

    foreach ($name in $ExpectedDeps.Keys) {
        $path = Join-Path $Campaign $name
        if (-not (Test-Path $path)) {
            throw "Required audited dependency missing: $path"
        }
        $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        Write-Host "$name expected SHA256: $($ExpectedDeps[$name])"
        Write-Host "$name actual SHA256  : $actual"
        if ($actual -ne $ExpectedDeps[$name]) {
            throw "Dependency/reference hash mismatch: $name"
        }
    }

    if (Test-Path $Target) {
        $existing = (Get-FileHash $Target -Algorithm SHA256).Hash.ToLower()
        if ($existing -ne $ExpectedFinal) {
            throw "Existing audit target differs: $Target"
        }
    }
    else {
        Copy-Item $assembled $Target
    }

    Write-Host ""
    Write-Host "F14-V2 OOS V2 STAGED FOR CODEX AUDIT"
    Write-Host "TARGET: $Target"
    Write-Host "SHA256: $ExpectedFinal"
    Write-Host "NO PREFLIGHT RUN"
    Write-Host "NO --execute RUN"
    Write-Host "NO OOS LOCK CREATED"
    Write-Host "NO MARKET PAYLOAD READ"
    Write-Host "2026 UNTOUCHED"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
