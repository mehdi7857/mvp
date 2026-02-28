Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item .\config.yaml ('.\config.yaml.bak_safeedit_' + $ts)
$lines = Get-Content .\config.yaml

for ($i = 0; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s*ENABLE_LIVE\s*:') { $lines[$i] = '  ENABLE_LIVE: 1'; continue }
  if ($lines[$i] -match '^\s*FUNDING_EDGE_MULTIPLIER\s*:') { $lines[$i] = '  FUNDING_EDGE_MULTIPLIER: 0.18'; continue }
  if ($lines[$i] -match '^\s*ALLOW_LONG_CARRY\s*:') { $lines[$i] = '  ALLOW_LONG_CARRY: false'; continue }
  if ($lines[$i] -match '^\s*ENFORCE_POST_FUNDING_VALIDATION\s*:') { $lines[$i] = '  ENFORCE_POST_FUNDING_VALIDATION: 1'; continue }
}

$hasEnforce = $false
foreach ($ln in $lines) {
  if ($ln -match '^\s*ENFORCE_POST_FUNDING_VALIDATION\s*:') { $hasEnforce = $true; break }
}
if (-not $hasEnforce) {
  $inserted = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*ENABLE_LIVE\s*:') {
      $before = @()
      if ($i -gt 0) { $before = $lines[0..$i] } else { $before = @($lines[0]) }
      $after = @()
      if ($i + 1 -le $lines.Count - 1) { $after = $lines[($i+1)..($lines.Count-1)] }
      $lines = @($before + '  ENFORCE_POST_FUNDING_VALIDATION: 1' + $after)
      $inserted = $true
      break
    }
  }
  if (-not $inserted) {
    $lines += ''
    $lines += 'runtime:'
    $lines += '  ENABLE_LIVE: 1'
    $lines += '  ENFORCE_POST_FUNDING_VALIDATION: 1'
  }
}

Set-Content -Path .\config.yaml -Value $lines -Encoding UTF8

Write-Output '---CONFIG_CHECK---'
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|FUNDING_EDGE_MULTIPLIER|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY'

Write-Output '---YAML_VALIDATE---'
$py = '.\\venv\\Scripts\\python.exe'
if (-not (Test-Path $py)) { $py = '.\\.venv\\Scripts\\python.exe' }
if (Test-Path $py) {
  & $py -c "import yaml, pathlib; p=pathlib.Path('config.yaml'); yaml.safe_load(p.read_text(encoding='utf-8')); print('YAML_OK')"
} else {
  Write-Output 'PYTHON_NOT_FOUND'
}

Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 8
Write-Output '---SERVICE---'
Get-Service funding_basis_bot | Select-Object Name,Status
Write-Output '---LOG_TAIL---'
Get-Content .\service_out.log -Tail 220
