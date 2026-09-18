$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"

if (-not (Test-Path $Campaign)) {
    throw "Campaign directory missing: $Campaign"
}

$names = @("data_loader_v1.py", "preflight_v1.py")

foreach ($name in $names) {
    $hits = @(Get-ChildItem -Path $Campaign -Filter $name -File -Recurse -ErrorAction Stop)

    if ($hits.Count -eq 0) {
        throw "Dependency not found under campaign: $name"
    }

    if ($hits.Count -ne 1) {
        Write-Host "AMBIGUOUS DEPENDENCY: $name"
        foreach ($hit in $hits) {
            $h = (Get-FileHash $hit.FullName -Algorithm SHA256).Hash.ToLower()
            Write-Host ("  PATH   : " + $hit.FullName)
            Write-Host ("  SHA256 : " + $h)
        }
        throw "Multiple dependency candidates found. Abort."
    }

    $path = $hits[0].FullName
    $hash = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()

    Write-Host ("DEPENDENCY : " + $name)
    Write-Host ("PATH       : " + $path)
    Write-Host ("SHA256     : " + $hash)
    Write-Host ""
}

Write-Host "NO MARKET DATA READ"
Write-Host "NO OOS OPENED"
Write-Host "2026 UNTOUCHED"
