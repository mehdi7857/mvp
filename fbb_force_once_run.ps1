Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'

# Pull latest
& git pull | Write-Output

# Backup
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item .\config.yaml ('.\config.yaml.bak_forceonce_' + $ts)

# Safe line-based config edits
$lines = Get-Content .\config.yaml
for ($i = 0; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s*ENABLE_LIVE\s*:') { $lines[$i] = '  ENABLE_LIVE: 1'; continue }
  if ($lines[$i] -match '^\s*ENFORCE_POST_FUNDING_VALIDATION\s*:') { $lines[$i] = '  ENFORCE_POST_FUNDING_VALIDATION: 1'; continue }
  if ($lines[$i] -match '^\s*ALLOW_LONG_CARRY\s*:') { $lines[$i] = '  ALLOW_LONG_CARRY: true'; continue }
  if ($lines[$i] -match '^\s*FUNDING_EDGE_MULTIPLIER\s*:') { $lines[$i] = '  FUNDING_EDGE_MULTIPLIER: 0.18'; continue }
  if ($lines[$i] -match '^\s*TEST_FORCE_ENTRY_ONCE\s*:') { $lines[$i] = '  TEST_FORCE_ENTRY_ONCE: 1'; continue }
}
$hasForce = $false
foreach ($ln in $lines) { if ($ln -match '^\s*TEST_FORCE_ENTRY_ONCE\s*:') { $hasForce = $true; break } }
if (-not $hasForce) {
  # insert under runtime after ENABLE_LIVE line
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*ENABLE_LIVE\s*:') {
      $before = @(); if ($i -gt 0) { $before = $lines[0..$i] } else { $before = @($lines[0]) }
      $after = @(); if ($i + 1 -le $lines.Count - 1) { $after = $lines[($i+1)..($lines.Count-1)] }
      $lines = @($before + '  TEST_FORCE_ENTRY_ONCE: 1' + $after)
      break
    }
  }
}
Set-Content -Path .\config.yaml -Value $lines -Encoding UTF8

# YAML validate
$py = '.\\venv\\Scripts\\python.exe'
if (-not (Test-Path $py)) { $py = '.\\.venv\\Scripts\\python.exe' }
if (Test-Path $py) {
  & $py -c "import yaml, pathlib; p=pathlib.Path('config.yaml'); yaml.safe_load(p.read_text(encoding='utf-8')); print('YAML_OK')"
}

Write-Output '---CONFIG_KEYS---'
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY|FUNDING_EDGE_MULTIPLIER|TEST_FORCE_ENTRY_ONCE'

Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 8

Write-Output '---WAIT_FOR_MARKERS---'
$deadline = (Get-Date).AddMinutes(90)
$pre = $null
$post = $null
while ((Get-Date) -lt $deadline) {
  $hits = Select-String -Path .\service_out.log -Pattern '\[PRECHECK_FUNDING_DIRECTION\]|\[POST_FUNDING_VALIDATION\]' | Select-Object -Last 120
  foreach ($h in $hits) {
    if ($h.Line -match '\[PRECHECK_FUNDING_DIRECTION\]') { $pre = $h.Line }
    if ($h.Line -match '\[POST_FUNDING_VALIDATION\]') { $post = $h.Line }
  }
  if ($pre -or $post) {
    Write-Output '---SNAPSHOT---'
    if ($pre) { Write-Output ('PRE=' + $pre) } else { Write-Output 'PRE=<none yet>' }
    if ($post) { Write-Output ('POST=' + $post) } else { Write-Output 'POST=<none yet>' }
  }
  if ($pre -and $post) { break }
  Start-Sleep -Seconds 20
}

Write-Output '---FINAL_TWO---'
if ($pre) { Write-Output $pre } else { Write-Output '[PRECHECK_FUNDING_DIRECTION] <none>' }
if ($post) { Write-Output $post } else { Write-Output '[POST_FUNDING_VALIDATION] <none>' }
