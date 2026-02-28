Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item .\config.yaml ('.\config.yaml.bak_test_' + $ts)
$cfg = Get-Content .\config.yaml -Raw
$cfg = [regex]::Replace($cfg, '(?im)^(\s*ENABLE_LIVE:\s*).*$','$11')
$cfg = [regex]::Replace($cfg, '(?im)^(\s*FUNDING_EDGE_MULTIPLIER:\s*).*$','$10.18')
if ($cfg -match '(?im)^\s*ENFORCE_POST_FUNDING_VALIDATION:\s*') {
  $cfg = [regex]::Replace($cfg, '(?im)^(\s*ENFORCE_POST_FUNDING_VALIDATION:\s*).*$','$11')
} else {
  if ($cfg -notmatch '(?im)^\s*runtime:\s*$') {
    $cfg += "`r`nruntime:`r`n"
  }
  $cfg += "  ENFORCE_POST_FUNDING_VALIDATION: 1`r`n"
}
Set-Content -Path .\config.yaml -Value $cfg -Encoding UTF8
Write-Output '---CONFIG_CHECK---'
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|FUNDING_EDGE_MULTIPLIER|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY'
Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 8
Write-Output '---LOG_TAIL---'
Get-Content .\service_out.log -Tail 220
