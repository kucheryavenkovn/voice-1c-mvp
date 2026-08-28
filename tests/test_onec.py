"""Integration: onec.query_stock end-to-end with the 1C HTTP transport faked."""

import onec
import pytest
from conftest import ONEC_DECIMAL, ONEC_EMPTY, ONEC_MULTI, ONEC_SINGLE


def test_query_single_groups_warehouses(gw):
    gw.onec_data = ONEC_SINGLE
    res = onec.query_stock("молоко")
    assert res["found"] is True
    assert res["source"] == "1c"
    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["name"] == "Молоко 3.2%"
    assert it["unit"] == "шт"
    assert it["quantity"] == 50
    assert res["quantity"] == 50
    assert "всего 50 шт" in res["message"] and "По складам" in res["message"]


def test_query_multi_heterogeneous_units(gw):
    gw.onec_data = ONEC_MULTI
    res = onec.query_stock("7777")
    assert res["found"] is True
    assert len(res["items"]) == 3
    assert res["quantity"] is None  # mixed units (кг + шт) → no sum
    assert "10 кг" in res["message"] and "7 шт" in res["message"]
    assert "По складам" in res["message"]


def test_query_empty_not_found(gw):
    gw.onec_data = ONEC_EMPTY
    res = onec.query_stock("xyz")
    assert res["found"] is False
    assert res["items"] == []
    assert "не найден" in res["message"]


def test_query_decimal_quantity(gw):
    gw.onec_data = ONEC_DECIMAL
    res = onec.query_stock("барбарис")
    assert res["items"][0]["quantity"] == 143.25
    assert res["items"][0]["unit"] == "кг"
    assert "143.25" in res["message"]


def test_query_1c_business_error_raises(gw):
    gw.onec_fail = True
    with pytest.raises(RuntimeError):
        onec.query_stock("молоко")


def test_query_empty_input():
    res = onec.query_stock("   ")
    assert res["found"] is False
    assert res["items"] == []


# --- заказ запчастей (execute_code) ---


def test_create_order_ok(gw):
    gw.onec_code = "OK|ТД00-000012|Уплотнитель|0167899"
    res = onec.create_order("уплотнитель", 3)
    assert res["found"] is True
    assert res["source"] == "1c"
    assert res["order_number"] == "ТД00-000012"
    assert res["quantity"] == 3
    assert res["status"] == "Потребность зарегистрирована"
    assert "3 штуки" in res["message"] and "ТД00-000012" in res["message"]


def test_create_order_not_found(gw):
    gw.onec_code = "NOTFOUND"
    res = onec.create_order("xyz", 1)
    assert res["found"] is False
    assert res["order_number"] is None
    assert "не найден" in res["message"]


def test_create_order_write_error_raises(gw):
    gw.onec_code = "ERROR|Операция запрещена: опасные ключевые слова: Записать"
    with pytest.raises(RuntimeError, match="Записать"):
        onec.create_order("уплотнитель", 1)


def test_create_order_toolkit_error_raises(gw):
    gw.onec_code_fail = True
    with pytest.raises(RuntimeError):
        onec.create_order("уплотнитель", 1)


def test_build_order_code_shape():
    code = onec._build_order_code("телевизоры SHARP", 5)
    # запросы внутри execute_code обязаны быть однострочными
    for line in code.split("\n"):
        assert "ВЫБРАТЬ" not in line or "ИЗ" in line
    # лемматизация + wildcard: телевизоры -> телевизор
    assert '"%телевизор%SHARP%"' in code
    assert "КолВо = 5;" in code
    assert "Документы.ЗаказПоставщику.СоздатьДокумент()" in code
    assert "РежимЗаписиДокумента.Запись" in code


def test_build_order_code_fills_both_quantities_and_packunit():
    """ERP: в строке заказа видимое количество — КоличествоУпаковок (+Упаковка);
    заполнить оба, иначе в документе количество остаётся пустым."""
    code = onec._build_order_code("уплотнитель", 7)
    assert "Стр.КоличествоУпаковок = КолВо;" in code
    assert "Стр.Количество = КолВо;" in code
    assert "Стр.Упаковка = Выборка.Ед;" in code
    assert "Номенклатура.ЕдиницаИзмерения КАК Ед" in code


def test_build_order_code_no_article_null_safe():
    code = onec._build_order_code("молоко", 2)
    assert "Выборка.Арт = NULL" in code  # артикул может быть NULL


# --- кейс «запчасть для техники» (request_part) ---


def test_build_part_request_code_shape():
    code = onec._build_part_request_code("колёсные диски", "кировец", 2)
    # запросы внутри execute_code обязаны быть однострочными
    for line in code.split("\n"):
        assert "ВЫБРАТЬ" not in line or "ИЗ" in line
    # лемматизация + независимые токены (порядок слов не важен, кавычки удвоены,
    # ё нормализована в е — как в номенклатуре 1С)
    assert '""%колесный%""' in code and '""%диск%""' in code
    assert '""%кировец%""' in code
    assert "КолВо = 2;" in code
    # склады кейса
    assert 'НайтиПоНаименованию("Склад инженера")' in code
    assert 'НайтиПоНаименованию("Склад текущего ОП")' in code
    assert 'НайтиПоНаименованию("Склад другого ОП")' in code
    # все ветки и документы
    for marker in (
        "ЗаказНаРемонт.СоздатьДокумент",
        "ПеремещениеТоваров.СоздатьДокумент",
        "ЗаказНаПеремещение.СоздатьДокумент",
        "ЗаказПоставщику.СоздатьДокумент",
        '"B1|"',
        '"B2|"',
        '"B3|"',
        '"B4|"',
        "NO_VEHICLE|",
        "NO_PART|",
    ):
        assert marker in code


def test_token_matchconds_order_insensitive():
    """'масляный фильтр' должен находить 'Фильтр масляный' — токены И, не порядок.
    Кавычки удвоены (фрагмент встраивается в BSL-строку Запрос.Текст)."""
    c = onec._token_matchconds("Номенклатура", "масляный фильтр")
    assert 'ПОДОБНО ВРЕГ(""%масляный%"")' in c
    assert 'ПОДОБНО ВРЕГ(""%фильтр%"")' in c
    assert " И " in c
    # цифровой токен тоже ищется и в артикуле
    assert "Артикул" in c


def test_request_part_branch1(gw):
    gw.onec_code = (
        "B1|000000008|Трактор Кировец К-744Р Гос. № А123ВС04|Диск колесный задний|DK-300|1"
    )
    res = onec.request_part("диск задний", "кировец", 1)
    assert res["found"] is True
    assert res["branch"] == "B1"
    assert res["docs"] == ["000000008"]
    assert "000000008" in res["message"] and "складе инженера" in res["message"]


def test_request_part_branch2(gw):
    gw.onec_code = "B2|РЕМ1|ПЕР1|Трактор МТЗ-82|Диск колесный|12345|3|проведено"
    res = onec.request_part("диск", "мтз", 1)
    assert res["branch"] == "B2"
    assert res["docs"] == ["РЕМ1", "ПЕР1"]
    assert "ПЕР1" in res["message"] and "текущего ОП" in res["message"]


def test_request_part_branch3(gw):
    gw.onec_code = (
        "B3|РЕМ2|ЗП1|ПЕР2|Трактор Кировец К-744Р|Диск колесный передний|DK-100|5|проведено"
    )
    res = onec.request_part("диск передний", "кировец", 1)
    assert res["branch"] == "B3"
    assert res["docs"] == ["РЕМ2", "ЗП1", "ПЕР2"]
    assert "заказ на перемещение № ЗП1" in res["message"]


def test_request_part_branch4(gw):
    gw.onec_code = "B4|ТД00-000010|Трактор МТЗ-82|Фильтр масляный|77777"
    res = onec.request_part("фильтр масляный", "мтз", 1)
    assert res["branch"] == "B4"
    assert "заказ поставщику" in res["message"].lower()


def test_request_part_no_vehicle_lists_known(gw):
    gw.onec_code = "NO_VEHICLE|Трактор Кировец К-744Р; Трактор МТЗ-82"
    res = onec.request_part("диск", "камаз", 1)
    assert res["found"] is False
    assert res["branch"] == "NO_VEHICLE"
    assert "Кировец" in res["message"] and "Уточните" in res["message"]


def test_request_part_no_part(gw):
    gw.onec_code = "NO_PART|товар не найден"
    res = onec.request_part("левиафан", "кировец", 1)
    assert res["branch"] == "NO_PART"
    assert "не найдена" in res["message"]


def test_request_part_error_raises(gw):
    gw.onec_code = "ERROR|что-то сломалось"
    with pytest.raises(RuntimeError):
        onec.request_part("диск", "кировец", 1)
