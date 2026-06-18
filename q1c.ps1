#requires -Version 7
param([Parameter(Mandatory)][string]$Query)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$body = @{ query = $Query; limit = 50 } | ConvertTo-Json -Compress
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:6003/api/execute_query' -Method Post `
     -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec 60 -UseBasicParsing
"status=$($r.StatusCode)"
$j = $r.Content | ConvertFrom-Json
"success=$($j.success)"
if (-not $j.success) { "error: $($j.error)" } else { "data:`n$($j.data)" }
