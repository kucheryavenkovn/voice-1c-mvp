"""Генерация голосовых фикстур кейса через TTS-сервис (Piper).

Озвучивает фразы контрольного сценария в fixtures/voice/*.wav —
далее tools/control_scenario.py прогоняет их через полный тракт
(STT → LLM → 1С → TTS) как регресс.

Запуск: python scripts/voice_fixtures.py [--base-url http://127.0.0.1:8101]
"""

import argparse
import pathlib

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "voice"

# (имя файла, фраза) — порядок = три диалога сценария
PHRASES = [
    # диалог A: полный цикл, позиция со склада инженера
    ("01_greeting", "привет"),
    ("02_order", "нужен диск задний для кировца"),
    ("03_confirm_vehicle", "да, подтверждаю"),
    ("04_confirm_part", "да, подтверждаю"),
    ("05_stock_query", "сколько дисков на моем складе?"),
    ("06_finish", "оформи заказ"),
    ("07_confirm_docs", "да, подтверждаю"),
    # диалог B: фаззи-артикул «дк сто», позиция под заказ поставщику
    ("08_order_fuzzy", "нужен диск для кировца"),
    ("09_confirm_vehicle", "да, подтверждаю"),
    ("10_pick_variant", "диск колесный передний"),
    ("11_confirm_part", "да, подтверждаю"),
    ("12_finish", "оформи заказ"),
    ("13_confirm_docs", "да, подтверждаю"),
    # диалог C: негатив — техника вне справочника
    ("14_unknown_vehicle", "нужен диск для камаза"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8101")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    for name, phrase in PHRASES:
        r = requests.post(f"{args.base_url}/tts", json={"text": phrase}, timeout=120)
        r.raise_for_status()
        path = OUT / f"{name}.wav"
        path.write_bytes(r.content)
        print(f"{path.name}: «{phrase}» ({len(r.content)} байт)")
    print(f"\nготово: {len(PHRASES)} фикстур в {OUT}")


if __name__ == "__main__":
    main()
