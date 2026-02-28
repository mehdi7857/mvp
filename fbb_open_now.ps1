Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'
$env:PYTHONPATH = '.'
$py = '.\\venv\\Scripts\\python.exe'
if (-not (Test-Path $py)) { $py = '.\\.venv\\Scripts\\python.exe' }
& $py .\scripts\manual_rehedge_now.py --coin BTC --quote USDC --notional 12
