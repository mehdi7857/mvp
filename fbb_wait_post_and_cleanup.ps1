Set-Location 'C:\Users\Administrator\MVP\funding_basis_bot'

$log = '.\service_out.log'
$deadline = (Get-Date).AddHours(2)
$pre = $null
$post = $null

Write-Output ('START_UTC=' + (Get-Date).ToUniversalTime().ToString('u'))

while ((Get-Date) -lt $deadline) {
  if (Test-Path $log) {
    $preHit = Select-String -Path $log -Pattern '\[PRECHECK_FUNDING_DIRECTION\].*side=SHORT_PERP' | Select-Object -Last 1
    if ($preHit) { $pre = $preHit.Line }

    $postHit = Select-String -Path $log -Pattern '\[POST_FUNDING_VALIDATION\]' | Select-Object -Last 1
    if ($postHit) {
      $post = $postHit.Line
      break
    }
  }
  Start-Sleep -Seconds 15
}

Write-Output '---REQUIRED_LOGS---'
if ($pre) { Write-Output $pre } else { Write-Output '[PRECHECK_FUNDING_DIRECTION] <none for SHORT_PERP in window>' }
if ($post) { Write-Output $post } else { Write-Output '[POST_FUNDING_VALIDATION] <none>' }

if ($post -and $post -match 'VALIDATED_OK') {
  Write-Output '---ACTION---'
  Write-Output 'POST_VALIDATED_OK -> clean close both legs + revert test profile'

  $env:PYTHONPATH = '.'
  $py = '.\\venv\\Scripts\\python.exe'
  if (-not (Test-Path $py)) { $py = '.\\.venv\\Scripts\\python.exe' }

  # Clean close both legs via existing script (it closes perp if any, then opens/reopens if kept as-is).
  # For strict close-only, use direct close calls below.
  & $py - <<'PY'
from src.hyperliquid_trade_client import HyperliquidTradeClient
c = HyperliquidTradeClient()
coin='BTC'
# close perp if open
pos = c.get_positions(coin=coin)
if pos:
    szi = float(pos[0].get('szi') or 0.0)
    if szi != 0.0:
        side = 'BUY' if szi < 0 else 'SELL'
        r = c.place_perp_order(coin=coin, side=side, notional_usd=12.0, reduce_only=True)
        print('CLOSE_PERP', {'ok': r.ok, 'verified': r.verified, 'reason': r.verify_reason})
# close spot base if exists
b = c.get_spot_balances()
base = float(b.get('UBTC',0.0) or 0.0) + float(b.get('BTC',0.0) or 0.0) + float(b.get('WBTC',0.0) or 0.0)
if base > 0:
    r2 = c.place_spot_order(base_coin='BTC', quote_coin='USDC', side='SELL', notional_usd=12.0, use_available_base_size_for_sell=True)
    print('CLOSE_SPOT', {'ok': r2.ok, 'verified': r2.verified, 'reason': r2.verify_reason})
print('AFTER_POS', c.get_positions(coin=coin))
bb = c.get_spot_balances()
print('AFTER_SPOT', {'UBTC': bb.get('UBTC',0.0), 'BTC': bb.get('BTC',0.0), 'WBTC': bb.get('WBTC',0.0), 'USDC': bb.get('USDC',0.0)})
PY

  # Revert test profile
  $lines = Get-Content .\config.yaml
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*ENABLE_LIVE\s*:') { $lines[$i] = '  ENABLE_LIVE: 0'; continue }
    if ($lines[$i] -match '^\s*FUNDING_EDGE_MULTIPLIER\s*:') { $lines[$i] = '  FUNDING_EDGE_MULTIPLIER: 2.0'; continue }
    if ($lines[$i] -match '^\s*TEST_FORCE_ENTRY_ONCE\s*:') { $lines[$i] = '  TEST_FORCE_ENTRY_ONCE: 0'; continue }
    if ($lines[$i] -match '^\s*ALLOW_LONG_CARRY\s*:') { $lines[$i] = '  ALLOW_LONG_CARRY: false'; continue }
    if ($lines[$i] -match '^\s*ENFORCE_POST_FUNDING_VALIDATION\s*:') { $lines[$i] = '  ENFORCE_POST_FUNDING_VALIDATION: 1'; continue }
  }
  Set-Content -Path .\config.yaml -Value $lines -Encoding UTF8

  $statePath = '.\configs\test_harness_state.json'
  if (Test-Path $statePath) {
    Set-Content -Path $statePath -Value '{"force_entry_once_available": false, "force_gate_once_available": false, "wait_intervals": {"BTC": 0}}' -Encoding UTF8
  }

  Restart-Service funding_basis_bot -Force
  Start-Sleep -Seconds 6
  Write-Output '---FINAL_CONFIG_KEYS---'
  Select-String -Path .\config.yaml -Pattern 'ENABLE_LIVE|FUNDING_EDGE_MULTIPLIER|TEST_FORCE_ENTRY_ONCE|ENFORCE_POST_FUNDING_VALIDATION|ALLOW_LONG_CARRY'
  Write-Output '---SERVICE---'
  Get-Service funding_basis_bot | Select-Object Name,Status
}
