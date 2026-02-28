Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item .\config.yaml ('.\config.yaml.bak_option1_' + $ts)
$lines = Get-Content .\config.yaml

for ($i = 0; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s*ALLOW_LONG_CARRY\s*:') { $lines[$i] = '  ALLOW_LONG_CARRY: true'; continue }
}
Set-Content -Path .\config.yaml -Value $lines -Encoding UTF8

$py = '.\\venv\\Scripts\\python.exe'
if (-not (Test-Path $py)) { $py = '.\\.venv\\Scripts\\python.exe' }
if (Test-Path $py) {
  & $py -c "import yaml, pathlib; p=pathlib.Path('config.yaml'); yaml.safe_load(p.read_text(encoding='utf-8')); print('YAML_OK')"
}

Write-Output '---CONFIG_EFFECTIVE_KEYS---'
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|FUNDING_EDGE_MULTIPLIER|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY'

Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 8

$log = '.\\service_out.log'
$deadline = (Get-Date).AddMinutes(80)
$pre = $null
$post = $null
Write-Output '---MONITOR_START---'
Write-Output ('monitor_until=' + $deadline.ToString('u'))

while ((Get-Date) -lt $deadline) {
  if (Test-Path $log) {
    $recent = Select-String -Path $log -Pattern '\[PRECHECK_FUNDING_DIRECTION\]|\[POST_FUNDING_VALIDATION\]|UNSUPPORTED_BRANCH|OPEN \| OPENED|CLOSE \| CLOSED' | Select-Object -Last 60
    foreach ($m in $recent) {
      if ($m.Line -match '\[PRECHECK_FUNDING_DIRECTION\]') { $pre = $m.Line }
      if ($m.Line -match '\[POST_FUNDING_VALIDATION\]') { $post = $m.Line }
    }
  }

  if ($pre -or $post) {
    Write-Output '---SNAPSHOT---'
    if ($pre) { Write-Output ('PRE=' + $pre) } else { Write-Output 'PRE=<none yet>' }
    if ($post) { Write-Output ('POST=' + $post) } else { Write-Output 'POST=<none yet>' }
  }

  if ($pre -and $post) { break }
  Start-Sleep -Seconds 20
}

Write-Output '---FINAL_MARKERS---'
if ($pre) { Write-Output ('PRE=' + $pre) } else { Write-Output 'PRE=<none>' }
if ($post) { Write-Output ('POST=' + $post) } else { Write-Output 'POST=<none>' }

# Always revert ALLOW_LONG_CARRY after the monitoring window
$lines2 = Get-Content .\config.yaml
for ($j = 0; $j -lt $lines2.Count; $j++) {
  if ($lines2[$j] -match '^\s*ALLOW_LONG_CARRY\s*:') { $lines2[$j] = '  ALLOW_LONG_CARRY: false'; continue }
}
Set-Content -Path .\config.yaml -Value $lines2 -Encoding UTF8
Write-Output '---REVERTED_ALLOW_LONG_CARRY---'
Select-String -Path .\config.yaml -Pattern 'ALLOW_LONG_CARRY'
Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 5
Write-Output '---SERVICE_AFTER_REVERT---'
Get-Service funding_basis_bot | Select-Object Name,Status
