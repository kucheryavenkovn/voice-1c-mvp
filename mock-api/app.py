from fastapi import FastAPI, Query
from pydantic import BaseModel

# START_MODULE_CONTRACT
#   PURPOSE: Deterministic test double for 1C stock/order contract.
#   SCOPE: GET/POST /api/stock, POST /api/orders, GET /api/orders, health.
#   DEPENDS: none
#   LINKS: M-MOCK-1C, V-M-MOCK-1C
#   ROLE: RUNTIME
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   app
#   STOCK
#   ORDER_STATUS
#   Warehouse
#   StockItem
#   StockResponse
#   StockBody
#   OrderBody
#   OrderResponse
#   lookup
#   health
#   get_stock
#   get_stock_post
#   create_order
#   list_orders
# END_MODULE_MAP

# tiny in-memory "1C" stock database (lowercased keys for lookup)
STOCK = {
    "молоко": 42,
    "хлеб": 13,
    "сахар": 7,
    "соль": 21,
    "кофе": 0,
    "чай": 5,
    "вода": 99,
    "мука": 30,
    "масло": 18,
    "сыр": 9,
    "milk": 42,
    "bread": 13,
    "sugar": 7,
    "water": 99,
    # запчасти (кейс «заказ частей», данные из duplexV2T: 12345 в наличии, 77777 нет)
    "диск колесный": 3,
    "12345": 3,
    "фильтр масляный": 0,
    "77777": 0,
}

ORDER_STATUS = "Потребность зарегистрирована"

# созданные заказы (в памяти) + счётчик номеров ЗР-NNNNNNN (как в duplexV2T)
_orders: list[dict] = []
_next_order_seq = 1234


def _new_order_number() -> str:
    global _next_order_seq
    num = f"ЗР-{_next_order_seq:07d}"
    _next_order_seq += 1
    return num


app = FastAPI(title="1C mock stock API")


class Warehouse(BaseModel):
    name: str
    quantity: int | float


class StockItem(BaseModel):
    name: str
    article: str = ""
    unit: str = ""
    quantity: int | float | None = None
    warehouses: list[Warehouse] = []


class StockResponse(BaseModel):
    item: str
    found: bool
    quantity: int | float | None = None
    items: list[StockItem] = []
    warehouses: list[Warehouse] = []
    message: str
    source: str = "mock"


def lookup(item: str) -> StockResponse:
    key = (item or "").strip().lower()
    if key in STOCK:
        qty = STOCK[key]
        return StockResponse(
            item=item,
            found=True,
            quantity=qty,
            items=[
                StockItem(
                    name=item,
                    article="",
                    unit="шт",
                    quantity=qty,
                    warehouses=[Warehouse(name="(mock)", quantity=qty)],
                )
            ],
            warehouses=[Warehouse(name="(mock)", quantity=qty)],
            message=f"Остаток по товару '{item}': {qty} штук.",
            source="mock",
        )
    return StockResponse(
        item=item,
        found=False,
        quantity=None,
        items=[],
        warehouses=[],
        message=f"Товар '{item}' не найден в базе 1С.",
        source="mock",
    )


@app.get("/health")
def health():
    return {"ok": True, "items": len(STOCK), "orders": len(_orders)}


@app.get("/api/stock", response_model=StockResponse)
def get_stock(item: str = Query(..., description="item name")):
    return lookup(item)


class StockBody(BaseModel):
    item: str


@app.post("/api/stock", response_model=StockResponse)
def get_stock_post(body: StockBody):
    return lookup(body.item)


class OrderBody(BaseModel):
    item: str
    quantity: int = 1
    warehouse: str | None = None


class OrderResponse(BaseModel):
    item: str
    found: bool
    quantity: int | None = None
    order_number: str | None = None
    status: str | None = None
    message: str
    source: str = "mock"


@app.post("/api/orders", response_model=OrderResponse)
def create_order(body: OrderBody):
    """Оформить заказ на товар (кейс «заказ частей»): назначает номер ЗР-NNNNNNN.

    Товар должен быть в справочнике (STOCK); если остаток 0 — заказ поставщику."""
    key = (body.item or "").strip().lower()
    qty = body.quantity if isinstance(body.quantity, int) and body.quantity > 0 else 1
    if key not in STOCK:
        return OrderResponse(
            item=body.item,
            found=False,
            message=f"Товар '{body.item}' не найден в базе 1С.",
        )
    number = _new_order_number()
    _orders.append(
        {
            "number": number,
            "item": body.item,
            "quantity": qty,
            "warehouse": body.warehouse,
            "status": ORDER_STATUS,
        }
    )
    supply = "" if STOCK[key] > 0 else " Товара нет в наличии — заказ оформлен поставщику."
    message = f"Создан заказ № {number}: {body.item} — {qty} шт. {ORDER_STATUS}.{supply}"
    return OrderResponse(
        item=body.item,
        found=True,
        quantity=qty,
        order_number=number,
        status=ORDER_STATUS,
        message=message,
    )


@app.get("/api/orders")
def list_orders():
    return {"orders": list(_orders), "count": len(_orders)}
