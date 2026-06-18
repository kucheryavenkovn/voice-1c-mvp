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
    """Оставить буквы/цифры/пробелы/дефис — безопасно для ПОДОБНО.

    `%` и `_` — wildcards 1C ПОДОБНО, `"`/`\\` рвут строковый литерал → вырезаем.
    """
    s = re.sub(r"[_%\"\\]", " ", item or "", flags=re.UNICODE)
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


_CYR = re.compile(r"[А-Яа-яЁё]+")
_morph = None


def _lemmatize(safe: str) -> str:
    """Нормализовать к именительному падежу ед.ч.: телевизоры→телевизор, стулья→стул,
    молока→молоко. Лемматизируются только кириллические слова; коды/латиница/цифры
    и артикулы не трогаются. Если pymorphy3 недоступен — возвращается как есть.
    """
    global _morph
    if _morph is None:
        try:
            import pymorphy3

            _morph = pymorphy3.MorphAnalyzer()
        except Exception:
            _morph = False
    if not _morph:
        return safe
    out = []
    for t in re.split(r"\s+", safe):
        if not t:
            continue
        if _CYR.fullmatch(t):
            try:
                p = _morph.parse(t)
                out.append(p[0].normal_form if p else t)
            except Exception:
                out.append(t)
        else:
            out.append(t)
    return " ".join(out)


def _like_pattern(safe: str) -> str:
    """'Телевизор SHARP' -> '%Телевизор%SHARP%'.

    Каждый пробел между словами становится wildcard ПОДОБНО, поэтому лишние слова
    в названии из базы ('Телевизор 21 дюйм Sharp') не ломают совпадение.
    Одно слово -> обычное '%слово%'.
    """
    tokens = [t for t in re.split(r"\s+", safe) if t]
    return "%" + "%".join(tokens) + "%"


def _build_query(item: str) -> str:
    like = _like_pattern(_lemmatize(_sanitize(item)))
    return (
        "ВЫБРАТЬ\n"
        "  ТоварыНаСкладахОстатки.Склад.Наименование КАК Склад,\n"
        "  ТоварыНаСкладахОстатки.Номенклатура.Наименование КАК Товар,\n"
        "  ТоварыНаСкладахОстатки.Номенклатура.Артикул КАК Артикул,\n"
        "  ТоварыНаСкладахОстатки.Номенклатура.ЕдиницаИзмерения.Наименование КАК Ед,\n"
        "  СУММА(ТоварыНаСкладахОстатки.ВНаличииОстаток) КАК Остаток\n"
        "ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки КАК ТоварыНаСкладахОстатки\n"
        "ГДЕ ТоварыНаСкладахОстатки.ВНаличииОстаток <> 0\n"
        '  И (ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Наименование) ПОДОБНО ВРЕГ("' + like + '")\n'
        '   ИЛИ ВРЕГ(ТоварыНаСкладахОстатки.Номенклатура.Артикул) ПОДОБНО ВРЕГ("' + like + '"))\n'
        "СГРУППИРОВАТЬ ПО ТоварыНаСкладахОстатки.Склад.Наименование,\n"
        "  ТоварыНаСкладахОстатки.Номенклатура.Наименование,\n"
        "  ТоварыНаСкладахОстатки.Номенклатура.Артикул,\n"
        "  ТоварыНаСкладахОстатки.Номенклатура.ЕдиницаИзмерения.Наименование\n"
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
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return str(round(f))
        return f"{f:.2f}".rstrip("0").rstrip(".")
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


def _unit_of(it: dict) -> str:
    return it.get("unit") or ""


def _build_message(items: list, user_item: str) -> str:
    """Voice-friendly summary for get_stock ('сколько / общий остаток / по складам').

    Always: total + per-warehouse breakdown. Single item keeps its name; several
    items are aggregated — total per unit (same unit → grand total; different units
    → per-unit subtotals) + per-warehouse totals (per unit within a warehouse).
    """

    def art_full(it):
        return f" (арт. {it['article']})" if it.get("article") else ""

    if len(items) == 1:
        it = items[0]
        u = _unit_of(it)
        ustr = f" {u}" if u else f" {_plural(it['quantity'], 'единица', 'единицы', 'единиц')}"
        head = f"{it['name']}{art_full(it)}: всего {_format_qty(it['quantity'])}{ustr}."
        if not it["warehouses"]:
            return head
        top = sorted(it["warehouses"], key=lambda w: w["quantity"], reverse=True)[:4]
        wh = ", ".join(f"{w['name']} {_format_qty(w['quantity'])}" for w in top)
        extra = ""
        if len(it["warehouses"]) > 4:
            r = len(it["warehouses"]) - 4
            extra = f", и ещё на {r} {_plural(r, 'складе', 'складах', 'складах')}"
        return f"{head} По складам: {wh}{extra}."

    # several items → aggregate by unit + by warehouse
    unit_totals: dict[str, float] = {}
    wh_map: dict[str, dict[str, float]] = {}
    for it in items:
        u = _unit_of(it)
        unit_totals[u] = unit_totals.get(u, 0) + it["quantity"]
        for w in it["warehouses"]:
            d = wh_map.setdefault(w["name"], {})
            d[u] = d.get(u, 0) + w["quantity"]

    units = [u for u in unit_totals if u]
    if len(units) <= 1:
        u = units[0] if units else ""
        total_str = (
            f"всего {_format_qty(sum(it['quantity'] for it in items))}{(' ' + u) if u else ''}"
        )
    else:
        total_str = " + ".join(f"{_format_qty(unit_totals[u])} {u}" for u in units)

    def fmt_wh(wh, d):
        if len(d) <= 1:
            (u, q) = next(iter(d.items()))
            return f"{wh} {_format_qty(q)}{(' ' + u) if u else ''}"
        return f"{wh} (" + ", ".join(f"{_format_qty(q)} {u}" for u, q in d.items()) + ")"

    wh_sorted = sorted(wh_map.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    if not wh_sorted:
        return f"Остаток по '{user_item}': {total_str}."
    top = wh_sorted[:4]
    wh_str = ", ".join(fmt_wh(wh, d) for wh, d in top)
    extra = ""
    if len(wh_sorted) > 4:
        r = len(wh_sorted) - 4
        extra = f", и ещё на {r} {_plural(r, 'складе', 'складах', 'складах')}"
    return f"Остаток по '{user_item}': {total_str}. По складам: {wh_str}{extra}."


def _build_list_message(items: list, user_item: str) -> str:
    """Enumerate which items matching the query have stock (name + article + qty + unit).
    For 'по каким товарам с наименованием сахар есть остатки'."""
    if not items:
        return f"По '{user_item}' товаров с остатком нет."
    cap = 6
    parts = []
    for it in items[:cap]:
        a = f" (арт. {it['article']})" if it.get("article") else ""
        u = f" {_unit_of(it)}" if _unit_of(it) else ""
        parts.append(f"{it['name']}{a} — {_format_qty(it['quantity'])}{u}")
    s = "; ".join(parts)
    extra = ""
    if len(items) > cap:
        r = len(items) - cap
        extra = f"; и ещё {r} {_plural(r, 'позиция', 'позиции', 'позиций')}"
    n = len(items)
    pos = _plural(n, "позиция", "позиции", "позиций")
    return f"Товары с остатком по '{user_item}' ({n} {pos}): {s}{extra}."


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
            "items": [],
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
    # rows: [Склад, Товар, Артикул, Ед, Остаток]  (Артикул/Ед may be None/empty)
    items_map = {}
    order = []
    for row in rows:
        if len(row) < 5 or not row[4]:
            continue
        skl, tov, art, unit, qty = row[0], row[1], row[2], row[3], row[4]
        art_s = "" if art is None else str(art)
        unit_s = "" if unit is None else str(unit)
        key = (str(tov), art_s, unit_s)
        it = items_map.get(key)
        if it is None:
            it = {"name": str(tov), "article": art_s, "unit": unit_s, "quantity": 0, "wh": {}}
            items_map[key] = it
            order.append(key)
        it["quantity"] += qty
        wname = str(skl)
        it["wh"][wname] = it["wh"].get(wname, 0) + qty

    if not items_map:
        return {
            "item": item,
            "found": False,
            "quantity": None,
            "items": [],
            "warehouses": [],
            "message": f"Товар '{item}' не найден в остатках 1С.",
            "source": "1c",
        }

    items = []
    for key in order:
        it = items_map[key]
        wh = sorted(
            [{"name": k, "quantity": v} for k, v in it["wh"].items()],
            key=lambda w: w["quantity"],
            reverse=True,
        )
        items.append(
            {
                "name": it["name"],
                "article": it["article"],
                "unit": it["unit"],
                "quantity": it["quantity"],
                "warehouses": wh,
            }
        )
    items.sort(key=lambda x: x["quantity"], reverse=True)

    single = len(items) == 1
    qty_top = items[0]["quantity"] if single else None

    # aggregated per-warehouse across all matched rows (compat / debug only;
    # may mix units when several items match)
    agg = {}
    for it in items:
        for w in it["warehouses"]:
            agg[w["name"]] = agg.get(w["name"], 0) + w["quantity"]
    warehouses_all = sorted(
        [{"name": k, "quantity": v} for k, v in agg.items()],
        key=lambda w: w["quantity"],
        reverse=True,
    )

    return {
        "item": item,
        "found": True,
        "quantity": qty_top,
        "items": items,
        "warehouses": warehouses_all,
        "message": _build_message(items, item),
        "source": "1c",
    }


def ping() -> bool:
    try:
        url = ONEC_BASE_URL.rstrip("/") + "/execute_query"
        if ONEC_CHANNEL:
            url += f"?channel={ONEC_CHANNEL}"
        r = requests.post(url, json={"query": "ВЫБРАТЬ 1", "limit": 1}, timeout=8)
        return r.status_code == 200 and r.json().get("success") is True
    except Exception:
        return False
