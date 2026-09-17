$ErrorActionPreference = "Stop"
$Campaign="D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest=Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards="D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$ResearchRoot="D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$KnowledgeRoot="D:\MT5_Backtests\Research\KnowledgeBase\observations\1.0.0"
$Engine=Join-Path $Campaign "night_atlas_v1.py"
$Analyze=Join-Path $Campaign "night_atlas_analyze_v1.py"
$Cross=Join-Path $Campaign "night_atlas_cross_scan_v1.py"
$Stamp=Get-Date -Format "yyyyMMdd-HHmmss"
$Out=Join-Path $ResearchRoot ("NIGHT-ATLAS-V1-"+$Stamp)
$Data=Join-Path $Out "data";$Analysis=Join-Path $Out "analysis";$Log=Join-Path $Out "night_run.log"
foreach($p in @($Manifest,$Engine,$Analyze,$Cross)){if(-not(Test-Path $p)){throw "Missing: $p"}}
if(-not(Test-Path $Standards)){throw "Standards missing: $Standards"}
New-Item -ItemType Directory -Force -Path $Out|Out-Null
"=== GUARDIAN NIGHT ATLAS V1 ==="|Tee-Object -FilePath $Log
"OUTPUT: $Out"|Tee-Object -FilePath $Log -Append
"2017-2022 discovery only. 2023+ CLOSED. 2026 CLOSED."|Tee-Object -FilePath $Log -Append
Push-Location $Campaign
try{
foreach($s in @($Engine,$Analyze,$Cross)){python -m py_compile $s;if($LASTEXITCODE-ne 0){throw "Syntax fail: $s"}}
python $Engine --manifest $Manifest --standards $Standards --output-dir $Data 2>&1|Tee-Object -FilePath $Log -Append
if($LASTEXITCODE-ne 0){throw "Preflight failed"}
python $Engine --manifest $Manifest --standards $Standards --output-dir $Data --execute 2>&1|Tee-Object -FilePath $Log -Append
if($LASTEXITCODE-ne 0){throw "Engine failed"}
python $Analyze --input (Join-Path $Data "signals.csv") --output-dir $Analysis 2>&1|Tee-Object -FilePath $Log -Append
if($LASTEXITCODE-ne 0){throw "Analyzer failed"}
python $Cross --input (Join-Path $Data "signals.csv") --output (Join-Path $Analysis "cross_family_scan.json") 2>&1|Tee-Object -FilePath $Log -Append
if($LASTEXITCODE-ne 0){throw "Cross scan failed"}
$Kb=Join-Path $KnowledgeRoot ("NIGHT_ATLAS_V1\"+(Split-Path $Out -Leaf));New-Item -ItemType Directory -Force -Path $Kb|Out-Null
foreach($n in @("family_specs.json","summary.json","signals.csv")){Copy-Item (Join-Path $Data $n) $Kb}
foreach($n in @("leaderboard.csv","yearly.csv","shortlist.json","cross_family_scan.json")){Copy-Item (Join-Path $Analysis $n) $Kb}
"=== NIGHT ATLAS COMPLETE ==="|Tee-Object -FilePath $Log -Append
"OUTPUT: $Out"|Tee-Object -FilePath $Log -Append
"KNOWLEDGE: $Kb"|Tee-Object -FilePath $Log -Append
"2023+ OPENED: FALSE"|Tee-Object -FilePath $Log -Append
"2026 OPENED: FALSE"|Tee-Object -FilePath $Log -Append
}finally{Pop-Location}
