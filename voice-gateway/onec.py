"""Клиент к 1C MCP Toolkit (REST /api/execute_query) для получения остатков.

Источник: https://github.com/ROCTUP/1c-mcp-toolkit
REST-эндпоинт выполняет запрос на языке запросов 1С и возвращает значение-таблицу
в собственном текстовом формате:

    [N]{"Колонка1","Колонка2"}:
      val1,val2
      ...

Строки-значения, содержащие спецсимволы, оборачиваются в JSON-строки (с \\"),
числа идут как есть. Пустой результат: "[0]:".

Внимание: запрос выбран так, чтобы колонки были только строками и числами
(без objectRef) — это позволяет простому парсеру корректно разбирать строки.
"""

import json
import os
import re

import requests

ONEC_BASE_URL = os.getenv("ONEC_BASE_URL", "http://host.docker.internal:6003/api")
ONEC_CHANNEL = os.getenv("ONEC_CHANNEL", "").strip()
ONEC_TIMEOUT = int(os.getenv("ONEC_TIMEOUT", "30"))


def _sanitize(item: str) -> str:
    """Оставить буквы/цифры/пробелы/дефис — безопасно для ПОДОБНО."""
    s = re.sub(r"[^\w\s\-]", " ", item or "", flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


def _build_query(item: str) -> str:
    safe = _sanitize(item).replace('"', "")
    return (
        "ВЫБРАТЬ\n"
        "  ТоварыНаСкладахОстатки.Склад.Наименование КАК Склад,\n"
        "  СУММА(ТоварыНаСкладахОстатки.ВНаличииОстаток) КАК Остаток\n"
        "ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК ТоварыНаСкладахОстатки\n"
        'ГДЕ ТоварыНаСкладахОстатки.ВНаличииОстаток <> 0\n'
        '  И ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Наименование) ПОДОБНО ВРЕГ("%'
        + safe
        + '%")\n'
        "СГРУППИРОВАТЬ ПО ТоварыНаСкладахОстатки.Склад.Наименование\n"
        "УПОРЯДОЧИТЬ ПО Остаток УБЫВ"
    )


def _coerce(s: str):
    if s in ("", "null", "Null", "NULL"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _parse_row(line: str) -> list:
    fields = []
    i, n = 0, len(line)
    while i < n:
        if line[i] == '"':
            j = i + 1
            buf = ['"']
            while j < n:
                if line[j] == "\\" and j + 1 < n:
                    buf.append(line[j])
                    buf.append(line[j + 1])
                    j += 2
                    continue
                if line[j] == '"':
                    buf.append('"')
                    j += 1
                    break
                buf.append(line[j])
                j += 1
            try:
                fields.append(json.loads("".join(buf)))
            except Exception:
                fields.append("".join(buf))
            i = j
            if i < n and line[i] == ",":
                i += 1
        else:
            j = line.find(",", i)
            if j == -1:
                j = n
            fields.append(_coerce(line[i:j].strip()))
            i = j + 1 if j < n else n
    return fields


_HEADER_RE = re.compile(r"^\[(\d+)\]\s*(\{.*\})?\s*:?\s*$")


def _parse_table(data: str) -> tuple[list, list]:
    lines = [ln.strip() for ln in (data or "").splitlines() if ln.strip()]
    if not lines:
        return [], []
    m = _HEADER_RE.match(lines[0])
    if not m:
        return [], []
    n = int(m.group(1))
    cols = re.findall(r'"([^"]*)"', m.group(2)) if m.group(2) else []
    if n == 0:
        return cols, []
    rows = [_parse_row(ln) for ln in lines[1:]]
    return cols, rows


def _format_qty(v) -> str:
    try:
        return str(int(v))
    except Exception:
        return str(v)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        return one
    if 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        return few
    return many


def _build_message(item: str, total, warehouses: list) -> str:
    top = warehouses[:4]
    parts = [f"{w['name']} {_format_qty(w['quantity'])}" for w in top]
    wh = ", ".join(parts)
    extra = ""
    if len(warehouses) > 4:
        rest = len(warehouses) - 4
        extra = f", и ещё на {rest} {_plural(rest, 'складе', 'складах', 'складах')}"
    unit = _plural(total, "единица", "единицы", "единиц")
    return (
        f"Остаток '{item}': всего {_format_qty(total)} {unit}. "
        f"По складам: {wh}{extra}."
    )


def query_stock(item: str) -> dict:
    """Вернуть остатки товара по складам из 1С.

    Возвращает словарь, совместимый с контрактом mock-api:
      {item, found, quantity, warehouses:[{name,quantity}], message, source}
    """
    if not _sanitize(item):
        return {
            "item": item,
            "found": False,
            "quantity": None,
            "warehouses": [],
            "message": f"Не удалось определить название товара '{item}'.",
            "source": "1c",
        }

    url = ONEC_BASE_URL.rstrip("/") + "/execute_query"
    if ONEC_CHANNEL:
        url += f"?channel={ONEC_CHANNEL}"
    payload = {"query": _build_query(item), "limit": 50}

    r = requests.post(url, json=payload, timeout=ONEC_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if not body.get("success"):
        raise RuntimeError(f"1C execute_query error: {body.get('error')}")

    _cols, rows = _parse_table(body.get("data", ""))
    warehouses = []
    for row in rows:
        if len(row) >= 2 and row[1]:
            warehouses.append({"name": str(row[0]), "quantity": row[1]})

    if not warehouses:
        return {
            "item": item,
            "found": False,
            "quantity": None,
            "warehouses": [],
            "message": f"Товар '{item}' не найден в остатках 1С.",
            "source": "1c",
        }

    total = sum(w["quantity"] for w in warehouses)
    return {
        "item": item,
        "found": True,
        "quantity": total,
        "warehouses": warehouses,
        "message": _build_message(item, total, warehouses),
        "source": "1c",
    }


def ping() -> bool:
    try:
        url = ONEC_BASE_URL.rstrip("/") + "/execute_query"
        if ONEC_CHANNEL:
            url += f"?channel={ONEC_CHANNEL}"
        r = requests.post(
            url, json={"query": "ВЫБРАТЬ 1", "limit": 1}, timeout=8
        )
        return r.status_code == 200 and r.json().get("success") is True
    except Exception:
        return False
