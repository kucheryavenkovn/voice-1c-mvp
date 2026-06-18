#requires -Version 7
<#
  Re-records golden 1C responses into tests/fixtures/1c/*.txt from a live
  1C MCP Toolkit (http://127.0.0.1:6003). Each fixture stores the raw `data`
  string returned by /api/execute_query for a representative query, so the
  parser contract tests run without 1C.

  Usage:  ./scripts/record_1c_fixtures.ps1
#>
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$out = Join-Path $PSScriptRoot '..' 'tests' 'fixtures' '1c'
New-Item -ItemType Directory -Force -Path $out | Out-Null

$queries = @(
  @{ name = 'select_1'; q = @'
ВЫБРАТЬ 1 КАК Результат
'@ },
  @{ name = 'milk_aggregated'; q = @'
ВЫБРАТЬ
  ТоварыНаСкладахОстатки.Склад.Наименование КАК Склад,
  СУММА(ТоварыНаСкладахОстатки.ВНаличииОстаток) КАК Остаток
ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК ТоварыНаСкладахОстатки
ГДЕ ТоварыНаСкладахОстатки.ВНаличииОстаток <> 0
  И ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Наименование) ПОДОБНО ВРЕГ("%молоко%")
СГРУППИРОВАТЬ ПО ТоварыНаСкладахОстатки.Склад.Наименование
УПОРЯДОЧИТЬ ПО Остаток УБЫВ
'@ },
  @{ name = 'article_7777'; q = @'
ВЫБРАТЬ
  ТоварыНаСкладахОстатки.Склад.Наименование КАК Склад,
  ТоварыНаСкладахОстатки.Номенклатура.Наименование КАК Товар,
  ТоварыНаСкладахОстатки.Номенклатура.Артикул КАК Артикул,
  СУММА(ТоварыНаСкладахОстатки.ВНаличииОстаток) КАК Остаток
ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК ТоварыНаСкладахОстатки
ГДЕ ТоварыНаСкладахОстатки.ВНаличииОстаток <> 0
  И (ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Наименование) ПОДОБНО ВРЕГ("%7777%")
   ИЛИ ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Артикул) ПОДОБНО ВРЕГ("%7777%"))
СГРУППИРОВАТЬ ПО ТоварыНаСкладахОстатки.Склад.Наименование,
  ТоварыНаСкладахОстатки.Номенклатура.Наименование,
  ТоварыНаСкладахОстатки.Номенклатура.Артикул
УПОРЯДОЧИТЬ ПО Остаток УБЫВ
'@ },
  @{ name = 'empty_result'; q = @'
ВЫБРАТЬ
  ТоварыНаСкладахОстатки.Склад.Наименование КАК Склад,
  СУММА(ТоварыНаСкладахОстатки.ВНаличииОстаток) КАК Остаток
ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК ТоварыНаСкладахОстатки
ГДЕ ТоварыНаСкладахОстатки.ВНаличииОстаток <> 0
  И ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Наименование) ПОДОБНО ВРЕГ("%xyzqwerty%")
СГРУППИРОВАТЬ ПО ТоварыНаСкладахОстатки.Склад.Наименование
'@ },
  @{ name = 'barbaris_decimal'; q = @'
ВЫБРАТЬ
  ТоварыНаСкладахОстатки.Склад.Наименование КАК Склад,
  ТоварыНаСкладахОстатки.Номенклатура.Наименование КАК Товар,
  ТоварыНаСкладахОстатки.Номенклатура.Артикул КАК Артикул,
  СУММА(ТоварыНаСкладахОстатки.ВНаличииОстаток) КАК Остаток
ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК ТоварыНаСкладахОстатки
ГДЕ ТоварыНаСкладахОстатки.ВНаличииОстаток <> 0
  И ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Наименование) ПОДОБНО ВРЕГ("%барбарис%")
СГРУППИРОВАТЬ ПО ТоварыНаСкладахОстатки.Склад.Наименование,
  ТоварыНаСкладахОстатки.Номенклатура.Наименование,
  ТоварыНаСкладахОстатки.Номенклатура.Артикул
УПОРЯДОЧИТЬ ПО Остаток УБЫВ
'@ },
  @{ name = 'catalog_name_article'; q = @'
ВЫБРАТЬ ПЕРВЫЕ 6 Наименование, Артикул
ИЗ Справочник.Номенклатура
УПОРЯДОЧИТЬ ПО Наименование
'@ }
)

foreach ($item in $queries) {
  $body = @{ query = $item.q; limit = 50 } | ConvertTo-Json -Compress
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:6003/api/execute_query' -Method Post `
       -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 60 -UseBasicParsing
  $j = $r.Content | ConvertFrom-Json
  if (-not $j.success) { Write-Host "[$($item.name)] FAILED: $($j.error)" -ForegroundColor Red; continue }
  $dataPath = Join-Path $out "$($item.name).txt"
  $queryPath = Join-Path $out "$($item.name).query.txt"
  [System.IO.File]::WriteAllText($dataPath, $j.data, [System.Text.UTF8Encoding]::new($false))
  [System.IO.File]::WriteAllText($queryPath, $item.q, [System.Text.UTF8Encoding]::new($false))
  $first = ($j.data -split "`n")[0]
  Write-Host "[$($item.name)] saved -> $first" -ForegroundColor Green
}
Write-Host "`nDone. Fixtures in $out" -ForegroundColor Green
