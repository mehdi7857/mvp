Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
& git pull | Write-Output

$lines = Get-Content .\config.yaml
for ($i = 0; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s*ENABLE_LIVE\s*:') { $lines[$i] = '  ENABLE_LIVE: 1'; continue }
  if ($lines[$i] -match '^\s*ENFORCE_POST_FUNDING_VALIDATION\s*:') { $lines[$i] = '  ENFORCE_POST_FUNDING_VALIDATION: 1'; continue }
  if ($lines[$i] -match '^\s*ALLOW_LONG_CARRY\s*:') { $lines[$i] = '  ALLOW_LONG_CARRY: false'; continue }
  if ($lines[$i] -match '^\s*TEST_FORCE_ENTRY_ONCE\s*:') { $lines[$i] = '  TEST_FORCE_ENTRY_ONCE: 1'; continue }
  if ($lines[$i] -match '^\s*FUNDING_EDGE_MULTIPLIER\s*:') { $lines[$i] = '  FUNDING_EDGE_MULTIPLIER: 0.18'; continue }
}
Set-Content -Path .\config.yaml -Value $lines -Encoding UTF8

$py = '.\\venv\\Scripts\\python.exe'
if (-not (Test-Path $py)) { $py = '.\\.venv\\Scripts\\python.exe' }
if (Test-Path $py) {
  & $py -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('config.yaml').read_text(encoding='utf-8')); print('YAML_OK')"
}

Write-Output '---CONFIG_KEYS---'
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY|TEST_FORCE_ENTRY_ONCE|FUNDING_EDGE_MULTIPLIER'

Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 8
Write-Output '---MARKERS---'
Select-String -Path .\service_out.log -Pattern '\[BRANCH_GUARD\]|\[PRECHECK_FUNDING_DIRECTION\]|\[POST_FUNDING_VALIDATION\]|\[FUNDING_SRC\]' | Select-Object -Last 30
