"""Browser/UI tests for the voice chat (Playwright). Local-only — skipped in CI.

These cover the client-side VAD state machine and DOM flow that pytest can't reach:
auto-dialog status transitions, end-of-phrase detection, recognized/answer bubbles,
"ready for next question". Headless Chromium has no mic, so we inject a scripted RMS
timeline in place of getUserMedia/AnalyserNode/MediaRecorder.

Run locally:
    pip install -r requirements-ui.txt
    playwright install chromium
    pytest -m ui
"""

import socket
import threading
import time

import pytest

pytestmark = [pytest.mark.ui]
playwright = pytest.importorskip("playwright")  # skip whole module if not installed

import uvicorn
from conftest import FakeRequests, State, wav_bytes

# Stub the mic stack in the browser before the page script runs.
INIT_JS = """
window.__rms = []; window.__rmsi = 0;
// a MediaStream that actually carries a (silent) audio track — an empty
// new MediaStream() is rejected by createMediaStreamSource.
const _a = new (window.AudioContext || window.webkitAudioContext)();
const _s = _a.createMediaStreamDestination().stream;
Object.defineProperty(navigator, 'mediaDevices', {
  value: { getUserMedia: () => Promise.resolve(_s) }, configurable: true
});
window.MediaRecorder = class {
  constructor(s){ this.stream=s; this.state='inactive'; this.mimeType='audio/webm'; }
  start(){ this.state='recording'; }
  stop(){ this.state='inactive';
    if(this.ondataavailable) this.ondataavailable({data:new Blob([new Uint8Array([0,0,0,0])])});
    if(this.onstop) this.onstop();
  }
  static isTypeSupported(){ return true; }
};
AnalyserNode.prototype.getFloatTimeDomainData = function(arr){
  const v = window.__rms[Math.min(window.__rmsi, (window.__rms.length||1)-1)] || 0;
  window.__rmsi++;
  for (let i=0;i<arr.length;i++) arr[i] = (Math.random()*2-1)*v;
};
"""

GATEWAY_PORT = 18103


@pytest.fixture(scope="module")
def gateway():
    import app
    import onec

    st = State()
    st.stt_text = "сколько молока?"
    st.lm_raw = '{"action":"get_stock","item":"молоко"}'
    st.onec_data = '[1]{"Склад","Товар","Артикул","Остаток"}:\n  Центральный склад,Молоко,М-1,20'
    st.tts_bytes = wav_bytes()
    st.mock_stock = {"item": "молоко", "found": True, "quantity": 20, "message": "Остаток 20."}
    fake = FakeRequests(st)

    orig = (onec.requests, app.requests, app.LM_MODEL)
    onec.requests = fake
    app.requests = fake
    app.LM_MODEL = "test-model"

    config = uvicorn.Config(app.app, host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", GATEWAY_PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{GATEWAY_PORT}", st
    server.should_exit = True
    thread.join(timeout=5)
    onec.requests, app.requests, app.LM_MODEL = orig


def test_chat_loads(page, gateway):
    url, _ = gateway
    page.goto(url + "/")
    page.wait_for_selector("#mic")
    assert page.title() == "voice-1c-mvp — голосовой чат"


def test_auto_dialog_detects_phrase_and_answers(page, gateway):
    url, _ = gateway
    page.add_init_script(INIT_JS)
    page.goto(url + "/")
    page.wait_for_selector("#mic")
    page.check("#autoMode")

    # ~1s ambient silence, ~1.5s speech, then silence → VAD must endpoint.
    page.evaluate("""() => {
        const noise = Array(20).fill(0.004);
        const speech = Array(30).fill(0.12);
        const tail = Array(40).fill(0.004);
        window.__rms = noise.concat(speech).concat(tail);
        window.__rmsi = 0;
    }""")

    page.click("#mic")

    # status must reach 'говорите…' (speech detected)
    page.wait_for_function(
        "() => /говорите/.test(document.getElementById('status').textContent)", timeout=15000
    )
    # then the bot answer must appear and the system become ready for the next phrase
    page.wait_for_selector("#chat .bot", timeout=15000)
    page.wait_for_function(
        "() => /готов слушать/.test(document.getElementById('status').textContent)", timeout=15000
    )
    msgs = page.eval_on_selector_all("#chat .msg", "els => els.map(e => e.textContent)")
    assert any("сколько молока" in m for m in msgs)  # recognized query bubble


def test_threshold_change_updates_readout(page, gateway):
    import re

    url, _ = gateway
    page.add_init_script(INIT_JS)
    page.goto(url + "/")
    page.check("#autoMode")
    page.evaluate("""() => { window.__rms = Array(60).fill(0.004); window.__rmsi = 0; }""")
    page.click("#mic")
    page.wait_for_function(
        "() => /старт/.test(document.getElementById('readout').textContent)", timeout=10000
    )

    def start_value():
        txt = page.eval_on_selector("#readout", "e => e.textContent")
        m = re.search(r"старт ([\d.]+)", txt)
        return float(m.group(1)) if m else None

    page.wait_for_timeout(1000)  # let ambient calibration settle
    low = start_value()
    page.evaluate("""() => {
        const el = document.getElementById('thresh');
        el.value = '0.090';
        el.dispatchEvent(new Event('input', { bubbles: true }));
    }""")
    page.wait_for_timeout(400)
    high = start_value()
    assert high is not None and low is not None and high > low
