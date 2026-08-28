"""Контрольный сценарий кейса «запчасть для техники»: прогон голосовых
фикстур fixtures/voice/*.wav через полный тракт (POST /ask: STT → LLM → 1С →
TTS) с одним chat_id. Печатает транскрипт П/А, сверяет ожидания (мягко,
✓/✗) и сохраняет его в fixtures/voice/transcript-last.txt.

Запуск: python tools/control_scenario.py [--base-url http://127.0.0.1:8103]
"""

import argparse
import pathlib
import sys
import urllib.parse

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
VOICE = ROOT / "fixtures" / "voice"

# (fixture, ожидаемая подстрока ответа или None, метка шага, чат)
STEPS = [
    ("01_greeting.wav", "что вам сейчас нужно", "приветствие с вопросом о действии", "A"),
    ("02_order.wav", "Это она?", "техника из 1С (item+vehicle за один ход)", "A"),
    ("03_confirm_vehicle.wav", "Это та деталь", "подтверждение техники -> запчасть найдена", "A"),
    ("04_confirm_part.wav", "DK-300", "позиция добавлена в корзину", "A"),
    ("05_stock_query.wav", None, "остатки внутри маршрута (этап сохраняется)", "A"),
    ("06_finish.wav", "DK-300", "сводка корзины", "A"),
    ("07_confirm_docs.wav", "заказ на ремонт", "создание документов", "A"),
    ("08_order_fuzzy.wav", "Нашёл технику", "новый диалог: техника", "B"),
    ("09_confirm_vehicle.wav", "запчасть", "варианты запчастей", "B"),
    ("10_pick_variant.wav", "DK-100", "выбор «диск колесный передний»", "B"),
    ("11_confirm_part.wav", "DK-100", "позиция в корзине (поставщик)", "B"),
    ("12_finish.wav", "DK-100", "сводка корзины 2", "B"),
    ("13_confirm_docs.wav", "заказ на ремонт", "создание документов 2", "B"),
    ("14_unknown_vehicle.wav", "не найдена", "негатив: техника вне справочника", "C"),
]


def decode(s):
    return urllib.parse.unquote(s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8103")
    ap.add_argument("--chat", default=None)
    args = ap.parse_args()
    import time as _t

    run_id = args.chat or f"control-{_t.strftime('%H%M%S')}"
    args = ap.parse_args()

    lines = []
    ok = 0
    total = 0
    for fixture, expect, label, chat in STEPS:
        path = VOICE / fixture
        if not path.exists():
            print(f"✗ {fixture}: файла нет — сгенерируйте scripts/voice_fixtures.py")
            total += 1
            continue
        with path.open("rb") as f:
            r = requests.post(
                f"{args.base_url}/ask?chat_id={run_id}-{chat}",
                files={"file": (fixture, f, "audio/wav")},
                timeout=300,
            )
        if not r.ok:
            ans = f"HTTP {r.status_code}: {r.text[:120]}"
            verdict = "✗"
        else:
            ans = decode(r.headers.get("X-Answer"))
            verdict = "✓" if (expect is None or expect.lower() in ans.lower()) else "✗"
        if verdict == "✓":
            ok += 1
        total += 1
        q_rec = decode(r.headers.get("X-Question"))
        intent_hdr = decode(r.headers.get("X-Intent"))
        lines.append(f"П (STT): {q_rec}")
        lines.append(f"А: {ans}")
        lines.append(f"   intent: {intent_hdr}")
        print(f"{verdict} [{fixture}] {label}")
        print(f"   STT: {q_rec}")
        print(f"   А: {ans}")
        print(f"   intent: {intent_hdr}")
        print()

    header = f"Итог: {ok}/{total} шагов соответствуют ожиданиям"
    lines.insert(0, header)
    print(header)
    out = VOICE / "transcript-last.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("транскрипт:", out)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
