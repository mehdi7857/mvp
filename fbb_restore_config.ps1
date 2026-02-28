Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
$b = Get-ChildItem .\config.yaml.bak_test_* | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $b) {
  Write-Error 'No backup found'
  exit 1
}
Copy-Item $b.FullName .\config.yaml -Force
Write-Output ('RESTORED_FROM=' + $b.Name)
Restart-Service funding_basis_bot -Force
Start-Sleep -Seconds 5
Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|FUNDING_EDGE_MULTIPLIER|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY'
Write-Output '---TAIL---'
Get-Content .\service_out.log -Tail 100
