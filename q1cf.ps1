#requires -Version 7
param([Parameter(Mandatory)][string]$File)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$q = Get-Content -Raw $File
$body = @{ query = $q; limit = 50 } | ConvertTo-Json -Compress
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:6003/api/execute_query' -Method Post `
     -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec 60 -UseBasicParsing
$j = $r.Content | ConvertFrom-Json
"success=$($j.success)"
if (-not $j.success) { "error: $($j.error)" } else { "data:`n$($j.data)" }
