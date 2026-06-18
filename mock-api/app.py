from fastapi import FastAPI, Query
from pydantic import BaseModel

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
}

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
    return {"ok": True, "items": len(STOCK)}


@app.get("/api/stock", response_model=StockResponse)
def get_stock(item: str = Query(..., description="item name")):
    return lookup(item)


class StockBody(BaseModel):
    item: str


@app.post("/api/stock", response_model=StockResponse)
def get_stock_post(body: StockBody):
    return lookup(body.item)
