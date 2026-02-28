Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'

$cfg = Get-Content .\config.yaml -Raw
$cfg = [regex]::Replace($cfg, '(?im)^(\s*ENABLE_LIVE:\s*).*$'                       , '${1}0')
$cfg = [regex]::Replace($cfg, '(?im)^(\s*TEST_FORCE_ENTRY_ONCE:\s*).*$'             , '${1}0')
$cfg = [regex]::Replace($cfg, '(?im)^(\s*FUNDING_EDGE_MULTIPLIER:\s*).*$'           , '${1}2.0')
$cfg = [regex]::Replace($cfg, '(?im)^(\s*ENFORCE_POST_FUNDING_VALIDATION:\s*).*$'   , '${1}1')
Set-Content -Path .\config.yaml -Value $cfg -Encoding UTF8

Write-Output '---CONFIG_KEYS_AFTER_EDIT---'
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|TEST_FORCE_ENTRY_ONCE|FUNDING_EDGE_MULTIPLIER|ENFORCE_POST_FUNDING_VALIDATION'

Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 4

Write-Output '---SERVICE---'
Get-Service funding_basis_bot | Select-Object Status,Name | Format-Table -AutoSize

Write-Output '---EFFECTIVE_CONFIG_TAIL---'
Select-String -Path .\service_out.log -Pattern 'EFFECTIVE_CONFIG|EFFECTIVE_CONFIG_DEBUG' | Select-Object -Last 4 -ExpandProperty Line
