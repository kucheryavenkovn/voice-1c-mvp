# mock-api — заглушка API остатков 1С

Имитация HTTP-шлюза 1С. Сейчас используется как **фоллбэк/тестовый** источник:
по умолчанию шлюз берёт остатки из реальной 1С через MCP Toolkit
(`STOCK_BACKEND=1c`), а при ошибке — из этого mock (см. `docs/1C_INTEGRATION.md`).
In-memory справочник, FastAPI.

## Эндпоинты
- `GET /health` → `{"ok": true, "items": <N>}`.
- `GET /api/stock?item=<товар>` — остаток по названию.
- `POST /api/stock` body `{"item": "<товар>"}` — то же через POST.

### Ответ
```json
{
  "item": "молоко",
  "found": true,
  "quantity": 42,
  "message": "Остаток по товару 'молоко': 42 штук."
}
```
Незнакомый товар → `found: false`, `quantity: null`, сообщение «не найден».

## Контракт для замены на реальную 1С
Реальный шлюз (1C MCP Toolkit / 1CKit / MCP-tool `get_stock`) должен отвечать **тем же JSON**:
минимум `found` (bool) и `quantity` (int|null). Поле `message` использует
`voice-gateway` как готовую реплику для TTS — если его не будет, шлюз соберёт
фразу сам из `quantity`.

Расширенный контракт (фактический при `STOCK_BACKEND=1c`):
```json
{ "item":"молоко", "found":true, "quantity":480,
  "warehouses":[{"name":"Торговый зал","quantity":150}],
  "message":"Остаток 'молоко': всего 480 единиц. По складам: …",
  "source":"1c" }
```

## Товары (ключи lowercase)
молоко, хлеб, сахар, соль, кофе, чай, вода, мука, масло, сыр (+ milk/bread/sugar/water).
Расширить — отредактируй словарь `STOCK` в `app.py`.
