param([string]$VivadoBin='C:\Xilinx\Vivado\2022.2\bin', [string]$Python='python')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$pythonPath = $Python
& $pythonPath (Join-Path $PSScriptRoot 'prepare_cases.py')
if($LASTEXITCODE -ne 0){throw 'Case generation failed'}
$simulationWork=Join-Path $projectRoot 'output\rtl_validation\xsim'
Push-Location -LiteralPath $simulationWork
try {
    $rtlFiles=Get-ChildItem -LiteralPath (Join-Path $projectRoot 'rtl') -Filter '*.v' | Select-Object -ExpandProperty FullName
    & (Join-Path $VivadoBin 'xvlog.bat') --sv @rtlFiles (Join-Path $PSScriptRoot 'tb_video_filter.sv') --log xvlog.log
    if($LASTEXITCODE -ne 0){throw 'xvlog failed'}
    & (Join-Path $VivadoBin 'xelab.bat') tb_video_filter -s video_filter_test --debug typical --log xelab.log
    if($LASTEXITCODE -ne 0){throw 'xelab failed'}
    & (Join-Path $VivadoBin 'xsim.bat') video_filter_test -runall --log xsim.log
    if($LASTEXITCODE -ne 0){throw 'xsim failed'}
    $logText=Get-Content -LiteralPath 'xsim.log' -Raw
    if($logText -notmatch 'ALL RTL TESTS PASS'){throw 'Simulation did not reach PASS'}
    $hashes=[ordered]@{}
    foreach($rtlFile in $rtlFiles){$hashes['rtl/'+(Split-Path -Leaf $rtlFile)]=(Get-FileHash -LiteralPath $rtlFile -Algorithm SHA256).Hash.ToLowerInvariant()}
    $passRecord=[ordered]@{status='PASS';case_mode_comparisons=101;protocol_error_reset_tests=21;actual_board_test=$false;source_sha256=$hashes;log='output/rtl_validation/xsim/xsim.log'}
    $passRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'output\rtl_validation\rtl_pass.json') -Encoding utf8
} finally {Pop-Location}
