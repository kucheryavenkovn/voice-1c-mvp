# mock-api — заглушка API остатков 1С

Имитация HTTP-шлюза 1С для отладки голосового цикла без реальной интеграции.
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
Реальный шлюз (1CKit / MCP-tool `get_stock`) должен отвечать **тем же JSON**:
минимум `found` (bool) и `quantity` (int|null). Поле `message` использует
`voice-gateway` как готовую реплику для TTS — если его не будет, шлюз соберёт
фразу сам из `quantity`.

## Товары (ключи lowercase)
молоко, хлеб, сахар, соль, кофе, чай, вода, мука, масло, сыр (+ milk/bread/sugar/water).
Расширить — отредактируй словарь `STOCK` в `app.py`.
