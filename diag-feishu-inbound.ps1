<#
  diag-feishu-inbound.ps1
  --------------------------------------------------------------
  Diagnoses why OpenClaw stops replying to Feishu DM messages.
  Reads .openclaw/openclaw.json, lark plugin state, gateway process,
  and openclaw.sqlite to report the most likely root cause and a fix.

  Usage:
    powershell -ExecutionPolicy Bypass -File diag-feishu-inbound.ps1
  No side effects. No restarts. No file mutations.
#>

$ErrorActionPreference = 'Continue'
$root    = Join-Path $env:USERPROFILE '.openclaw'
$cfgPath = Join-Path $root 'openclaw.json'
$bakPath = Join-Path $root 'openclaw.json.bak'
$dbPath  = Join-Path $root 'state\openclaw.sqlite'
$extDir  = Join-Path $root 'extensions\openclaw-lark'
$nodeExe = 'C:\Program Files\nodejs\node.exe'

function Section([string]$title) {
  Write-Host ''
  Write-Host ('==' * 30)
  Write-Host ("## {0}" -f $title)
  Write-Host ('==' * 30)
}
function Pass([string]$msg) { Write-Host ("[ OK ] {0}" -f $msg) -ForegroundColor Green }
function Warn([string]$msg) { Write-Host ("[WARN] {0}" -f $msg) -ForegroundColor Yellow }
function Fail([string]$msg) { Write-Host ("[FAIL] {0}" -f $msg) -ForegroundColor Red }
function Info([string]$msg) { Write-Host ("[INFO] {0}" -f $msg) -ForegroundColor Cyan }

# Extract specific fields via Node, returning tab-separated lines.
# Avoids PowerShell's flaky JSON parser on this config (Chinese name, etc.).
function Get-FeishuConfig {
  if (-not (Test-Path $cfgPath)) { return @() }
  $lines = & $nodeExe -e @"
const fs=require('node:fs');
const c=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const f=(c.channels||{}).feishu||{};
const p=(c.plugins||{}).entries||{};
const out=[
  'dmPolicy='+f.dmPolicy,
  'enabled='+f.enabled,
  'connectionMode='+f.connectionMode,
  'webhookPath='+f.webhookPath,
  'appId='+f.appId,
  'allowFrom='+JSON.stringify(f.allowFrom||[]),
  'groupAllowFrom='+JSON.stringify(f.groupAllowFrom||[]),
  'mainName='+(((f.accounts||{}).main||{}).name||''),
  'mainAppId='+(((f.accounts||{}).main||{}).appId||''),
  'openclawLarkEnabled='+(p['openclaw-lark']?p['openclaw-lark'].enabled:''),
  'allowList='+JSON.stringify((c.plugins||{}).allow||[]),
  'binding='+JSON.stringify(((c.bindings||[]).find(b=>b.match&&b.match.channel==='feishu')||{}).agentId||'')
];
process.stdout.write(out.join('\n'));
"@ $cfgPath
  $lines -split "`n"
}
function Get-BakFeishuConfig {
  if (-not (Test-Path $bakPath)) { return @() }
  $lines = & $nodeExe -e @"
const fs=require('node:fs');
const c=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const f=(c.channels||{}).feishu||{};
const out=[
  'dmPolicy='+f.dmPolicy,
  'allowFrom='+JSON.stringify(f.allowFrom||[])
];
process.stdout.write(out.join('\n'));
"@ $bakPath
  $lines -split "`n"
}
function Get-Field([string[]]$arr, [string]$key) {
  foreach ($l in $arr) { if ($l.StartsWith($key+'=')) { return $l.Substring($key.Length+1) } }
  return ''
}

# ----------------------------------------------------------------
Section '1. OpenClaw Gateway process & port'
# ----------------------------------------------------------------
$listener = Get-NetTCPConnection -State Listen -LocalPort 18789 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
  $gw = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
  if ($gw) {
    Pass ("Gateway running: PID {0}, started {1:o}" -f $gw.Id, $gw.StartTime.ToUniversalTime())
  } else {
    Warn ("Listener on 18789 owned by PID {0}, but process not found" -f $listener.OwningProcess)
  }
  Pass ("Gateway listening on {0}:{1}" -f $listener.LocalAddress, $listener.LocalPort)
  if ($listener.LocalAddress -in @('127.0.0.1','::1')) {
    Warn  'Gateway bound to loopback only. Webhook mode would not work in this state.'
    Info  'WebSocket mode (current) is unaffected; loopback is fine for outbound WS to Feishu.'
  }
} else {
  Fail 'No listener on 18789'
  $gw = $null
}

# ----------------------------------------------------------------
Section '2. Feishu WebSocket connection'
# ----------------------------------------------------------------
if ($null -eq $gw) {
  Fail 'Cannot check WS connection: gateway PID unknown'
} else {
  $wsConns = Get-NetTCPConnection -State Established -RemotePort 443 -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq $gw.Id } |
    Sort-Object RemoteAddress -Unique
  if ($wsConns) {
    Pass ("{0} ESTABLISHED TLS socket(s) from gateway (PID {1}) to 443" -f $wsConns.Count, $gw.Id)
    foreach ($c in $wsConns) { Info ("  -> {0}:{1}" -f $c.RemoteAddress, $c.RemotePort) }
  } else {
    Fail "No ESTABLISHED TLS socket from gateway (PID $($gw.Id)) -- WS is NOT connected"
  }
}

# ----------------------------------------------------------------
Section '3. Plugin state (openclaw-lark)'
# ----------------------------------------------------------------
if (Test-Path (Join-Path $extDir 'openclaw.plugin.json')) {
  Pass ('Plugin present: {0}' -f $extDir)
} else {
  Fail ('Plugin missing: {0}' -f $extDir)
}

$cfgLines = Get-FeishuConfig
if ($cfgLines.Count -gt 0) {
  $dmPolicy        = Get-Field $cfgLines 'dmPolicy'
  $feishuEnabled   = Get-Field $cfgLines 'enabled'
  $connMode        = Get-Field $cfgLines 'connectionMode'
  $webhookPath     = Get-Field $cfgLines 'webhookPath'
  $appId           = Get-Field $cfgLines 'appId'
  $allowFromJson   = Get-Field $cfgLines 'allowFrom'
  $groupAllowFrom  = Get-Field $cfgLines 'groupAllowFrom'
  $mainName        = Get-Field $cfgLines 'mainName'
  $mainAppId       = Get-Field $cfgLines 'mainAppId'
  $larkEnabled     = Get-Field $cfgLines 'openclawLarkEnabled'
  $allowListJson   = Get-Field $cfgLines 'allowList'
  $bindingAgent    = Get-Field $cfgLines 'binding'

  if ($larkEnabled -eq 'True') { Pass 'plugins.entries.openclaw-lark.enabled = true' }
  else { Fail ('plugins.entries.openclaw-lark.enabled = {0}' -f $larkEnabled) }

  if ($allowListJson -match 'openclaw-lark') { Pass 'openclaw-lark in plugins.allow' }
  else { Fail ('openclaw-lark NOT in plugins.allow: {0}' -f $allowListJson) }

  if ($feishuEnabled -eq 'True') { Pass 'channels.feishu.enabled = true' }
  else { Fail ('channels.feishu.enabled = {0}' -f $feishuEnabled) }

  Info ("  connectionMode = {0}" -f $connMode)
  Info ("  webhookPath    = {0}" -f $webhookPath)
  Info ("  dmPolicy       = {0}" -f $dmPolicy)
  Info ("  allowFrom      = {0}" -f $allowFromJson)
  Info ("  groupAllowFrom = {0}" -f $groupAllowFrom)
  Info ("  appId          = {0}" -f $appId)
  Info ("  accounts.main  = {0} (appId={1})" -f $mainName, $mainAppId)

  if ($bindingAgent) { Pass ("binding: feishu -> {0}" -f $bindingAgent) }
  else { Fail 'No binding for feishu channel' }
} else {
  Fail 'Could not parse openclaw.json'
}

# ----------------------------------------------------------------
Section '4. Config diff vs last backup'
# ----------------------------------------------------------------
$cur = ''
$bak = ''
if (Test-Path $cfgPath) { $cur = (Get-Content $cfgPath -Raw).Trim() }
if (Test-Path $bakPath) { $bak = (Get-Content $bakPath -Raw).Trim() }
if ($cur -and $bak) {
  if ($cur -eq $bak) { Info 'openclaw.json identical to .bak' }
  else {
    Warn 'openclaw.json differs from .bak -- possible recent policy edit'
    $liveCfg = Get-FeishuConfig
    $bakCfg  = Get-BakFeishuConfig
    Info ("  backup dmPolicy  = {0}" -f (Get-Field $bakCfg 'dmPolicy'))
    Info ("  backup allowFrom = {0}" -f (Get-Field $bakCfg 'allowFrom'))
    Info ("  live   dmPolicy  = {0}" -f (Get-Field $liveCfg 'dmPolicy'))
    Info ("  live   allowFrom = {0}" -f (Get-Field $liveCfg 'allowFrom'))
  }
} else { Info 'No backup to diff against' }

# ----------------------------------------------------------------
Section '5. SQLite ingress + audit (last 30 days)'
# ----------------------------------------------------------------
if (Test-Path $dbPath) {
  Pass ('State DB: {0}' -f $dbPath)
  $size = (Get-Item $dbPath).Length
  Info ("  size = {0} MB" -f [Math]::Round($size/1MB,1))

  $py = @"
import sqlite3, datetime, os
db = sqlite3.connect(r'$($dbPath -replace '\\','\\')')
db.row_factory = sqlite3.Row
def ms_iso(ms):
  return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).isoformat() if ms else None
def show(label, sql, args=()):
  cur = db.execute(sql, args)
  rows = cur.fetchall()
  cols = [c[0] for c in cur.description] if cur.description else []
  print(f'-- {label} ({len(rows)} rows) --')
  for r in rows[:15]:
    out = {k: r[k] for k in cols}
    for k in ('occurred_at','received_at','started_at_ms','completed_at_ms','created_at'):
      if k in cols: out[k] = ms_iso(out[k])
    if 'payload_json' in cols and out.get('payload_json'):
      out['payload_json'] = out['payload_json'][:160]+'...'
    print('  ', out)

cutoff_ms = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).timestamp() * 1000)
show('channel_ingress_events (total)', 'SELECT COUNT(*) AS n FROM channel_ingress_events')
show('channel_ingress_events by channel_id (30d)',
     'SELECT channel_id, status, COUNT(*) AS n, MAX(received_at) AS received_at FROM channel_ingress_events WHERE received_at > ? GROUP BY channel_id, status ORDER BY received_at DESC', (cutoff_ms,))
show('audit_events where channel is set (30d)',
     'SELECT actor_id, channel, direction, action, status, occurred_at FROM audit_events WHERE channel IS NOT NULL AND occurred_at > ? ORDER BY occurred_at DESC LIMIT 20', (cutoff_ms,))
show('audit_events with feishu/lark in any text col (30d)',
     '''SELECT sequence, kind, action, status, channel, error_code, occurred_at FROM audit_events
        WHERE occurred_at > ? AND (action LIKE "%feishu%" OR action LIKE "%lark%" OR error_code LIKE "%feishu%" OR channel LIKE "%feishu%")
        ORDER BY occurred_at DESC LIMIT 20''', (cutoff_ms,))
show('gateway_boot_lifecycle',
     'SELECT boot_id, pid, started_at_ms, completed_at_ms, outcome, reason FROM gateway_boot_lifecycle ORDER BY started_at_ms DESC LIMIT 5')
"@
  $py | python - 2>&1 | ForEach-Object { Write-Host $_ }
} else {
  Fail ('State DB missing: {0}' -f $dbPath)
}

# ----------------------------------------------------------------
Section '6. Heuristic: dmPolicy/allowFrom sanity check'
# ----------------------------------------------------------------
$maOpenId = 'ou_503adf68c6c9bd05f755ec39aacb2acc'
if ($cfgLines.Count -gt 0) {
  $allowFromParsed = @()
  try { $allowFromParsed = $allowFromJson | ConvertFrom-Json -ErrorAction SilentlyContinue } catch {}
  $wildcard       = $false
  $bareMatch      = $false
  $prefixedMatch  = $false
  foreach ($e in $allowFromParsed) {
    if ($e -eq '*') { $wildcard = $true }
    if ($e -eq $maOpenId) { $bareMatch = $true }
    if ($e -eq "feishu:$maOpenId") { $prefixedMatch = $true }
  }

  # The match in openclaw-lark/src/messaging/inbound/policy.js is EXACT lowercased
  # 'allowFrom.includes(senderId)'. SDK senderId for Feishu DM is bare 'ou_...'
  # without any 'feishu:' prefix. So 'feishu:ou_...' entries will NEVER match.
  if ($wildcard) {
    Pass 'allowFrom contains "*" -- wildcard match, all DMs allowed'
  } elseif ($dmPolicy -eq 'open') {
    Pass 'dmPolicy=open -- all DMs allowed'
  } elseif ($dmPolicy -eq 'allowlist') {
    if ($bareMatch) {
      Pass ('allowFrom contains bare openId "{0}" -- exact match will succeed' -f $maOpenId)
    } elseif ($prefixedMatch) {
      Fail ('allowFrom contains "feishu:{0}" but gate compares BARE openId. EXACT MATCH FAILS.' -f $maOpenId)
      Info  'Fix: change to bare openId, add "*", or set dmPolicy to "open"/"pairing".'
    } else {
      Fail ('Ma openId not in allowFrom')
    }
  } elseif ($dmPolicy -eq 'pairing') {
    Info 'dmPolicy=pairing -- unknown senders get a pairing request; verify Ma is paired'
  } elseif ($dmPolicy -eq 'disabled') {
    Fail 'dmPolicy=disabled -- all DMs blocked'
  } else {
    Warn ('Unknown dmPolicy: {0}' -f $dmPolicy)
  }
}

# ----------------------------------------------------------------
Section '7. Last commands.log entry (legacy inbound log)'
# ----------------------------------------------------------------
$log = Join-Path $root 'logs\commands.log'
if (Test-Path $log) {
  $last = Get-Content $log -Tail 5 -ErrorAction SilentlyContinue
  if ($last) {
    Write-Host ('Last 5 lines of {0}:' -f $log)
    $last | ForEach-Object { Write-Host ('  ' + $_) }
  } else { Info 'commands.log is empty' }
} else { Info 'commands.log not present (expected in 9.4: events go to sqlite)' }

# ----------------------------------------------------------------
Section '8. Diagnosis summary'
# ----------------------------------------------------------------
Write-Host ''
Write-Host 'Most likely root cause:'
Write-Host '  1) channels.feishu.dmPolicy = "allowlist" AND'
Write-Host '     allowFrom entries use "feishu:ou_..." prefix, but the lark plugin'
Write-Host '     gate does an EXACT lowercased match against the BARE openId.'
Write-Host '     Result: every DM inbound is rejected with reason=dm_not_allowed'
Write-Host '     before the agent dispatcher runs. No commands.log entry, no'
Write-Host '     channel_ingress_events row, no agent turn.'
Write-Host ''
Write-Host 'Fix (no Gateway restart required for the config change; the runtime'
Write-Host 're-reads the config on the next config.external change):'
Write-Host '  Option A (recommended): set allowFrom to the BARE openId:'
Write-Host '    "allowFrom": ["ou_503adf68c6c9bd05f755ec39aacb2acc"]'
Write-Host '  Option B: switch back to wildcard allow:'
Write-Host '    "dmPolicy": "open"'
Write-Host '    "allowFrom": ["*"]'
Write-Host '  Option C (safer for production): set dmPolicy: "open" + add specific'
Write-Host '    allowFrom, OR keep allowlist but with bare openId (not prefixed).'
Write-Host ''
Write-Host 'After editing openclaw.json, the lark plugin will pick up the change'
Write-Host 'on the next config-watch tick (a few seconds).'
Write-Host ''
Write-Host 'No gateway restart is needed for this fix -- only for code/extension changes.'
