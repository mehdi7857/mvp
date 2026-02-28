Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
Write-Output '---NSSM_ENV_BEFORE---'
$n0 = (nssm get funding_basis_bot AppEnvironmentExtra) 2>$null
Write-Output $n0

$vars = @(
  'ENABLE_LIVE=1',
  'FUNDING_EDGE_MULTIPLIER=0.18',
  'ENFORCE_POST_FUNDING_VALIDATION=1',
  'ALLOW_LONG_CARRY=0'
)
$joined = [string]::Join(' ', $vars)
nssm set funding_basis_bot AppEnvironmentExtra $joined | Out-Null

Write-Output '---NSSM_ENV_AFTER---'
$n1 = (nssm get funding_basis_bot AppEnvironmentExtra) 2>$null
Write-Output $n1

Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 8
Write-Output '---SERVICE---'
Get-Service funding_basis_bot | Select-Object Name,Status
Write-Output '---LOG_TAIL---'
Get-Content .\service_out.log -Tail 220
