"""The full-capture recorder: what it saves for each user action, and that the saved DOM can be reloaded and read by the engine."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from recorder_capture import Recorder  # noqa: E402
from job_agent.apply.perceive import perceive  # noqa: E402

BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")

PAGE = """<!doctype html><html><head><title>Apply</title><style>body{font-family:sans-serif}.box{padding:8px}</style></head><body><main>
<wiz-box></wiz-box>
<label>Account password <input id="pw" type="password"></label>
<div id="magic" onclick="this.dataset.on=1">Magic toggle</div>
<button id="open">Terms</button>
<iframe id="fr" srcdoc="<p>Inner frame text</p><label>Frame field <input id='ff'></label>" width="300" height="80"></iframe>
<div id="modal-host"></div>
<script>
customElements.define('wiz-box', class extends HTMLElement { connectedCallback() {
  const r = this.attachShadow({mode: 'open'});
  r.innerHTML = '<label id="l1">Mobile number</label><input id="n" aria-labelledby="l1">'
    + '<div role="radiogroup" aria-labelledby="q"><span id="q">Do you need sponsorship?</span>'
    + '<div role="radio" id="yes" tabindex="0" aria-checked="false"><svg width="20" height="20"><circle id="c" cx="10" cy="10" r="8"></circle></svg> Yes</div></div>'
    + '<label id="l2">State</label><div id="trg" role="button" aria-expanded="false" aria-labelledby="l2 sel" tabindex="0"><span id="sel">Select...</span></div>';
  const yes = r.getElementById('yes'); yes.onclick = () => yes.setAttribute('aria-checked', 'true');
  const trg = r.getElementById('trg'); trg.onclick = () => trg.setAttribute('aria-expanded', trg.getAttribute('aria-expanded') === 'true' ? 'false' : 'true');
} });
document.getElementById('open').onclick = () => { document.getElementById('modal-host').innerHTML =
  '<div role="dialog" aria-modal="true" style="position:fixed;inset:20% 20%;background:#fff;border:1px solid"><p>Terms and conditions</p><button>Agree</button></div>'; };
</script></main></body></html>"""


def run(coro):
    return asyncio.run(coro)


async def _record():
    out = Path(tempfile.mkdtemp()) / "rec"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.set_content(PAGE)
        rec = Recorder(ctx, out, settle=0.4)
        await rec.start()
        await asyncio.sleep(1.5)                                   # the first settled state is the 'before' of the first action
        n = page.locator("#n")
        await n.click(); await page.keyboard.type("9123456780"); await page.keyboard.press("Tab"); await asyncio.sleep(1.2)
        await page.locator("#c").click(force=True); await asyncio.sleep(1.2)
        await page.locator("#trg").click(); await asyncio.sleep(1.2)
        await page.locator("#pw").click(); await page.keyboard.type("S3cret-Pass!"); await page.keyboard.press("Tab"); await asyncio.sleep(1.2)
        await page.locator("#magic").click(); await asyncio.sleep(1.2)
        await page.locator("#open").click(); await asyncio.sleep(1.5)
        live = {(f.kind, f.label) for f in (await perceive(page)).model.fields}
        await rec.stop()
        await browser.close()
    rows = [json.loads(l) for l in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return out, rows, live


@pytest.fixture(scope="module")
def recording():
    return run(_record())


def test_the_element_is_saved_in_full_including_the_inner_piece_that_was_hit(recording):
    _, rows, _ = recording
    radio = next(r for r in rows if r.get("t") == "click" and r["el"]["role"] == "radio")
    el = radio["el"]
    assert el["hit"]["tag"] in {"circle", "svg"} and "<circle" in el["hit"]["html"]         # what the mouse actually landed on
    assert el["group"] == "Do you need sponsorship?"
    assert ">>>" in el["selector"] and "wiz-box" in el["selector"]                          # a path that crosses the shadow boundary
    assert 'role="radio"' in el["html"] and el["attrs"]["aria-checked"] == "false" and el["inShadow"]
    assert el["chain"][0]["role"] == "radio" and any(c["role"] == "radiogroup" for c in el["chain"])
    assert el["rect"][2] > 0 and el["style"]["display"]


def test_the_engines_agent_id_is_recorded_so_seen_and_not_seen_can_be_decided_exactly(recording):
    _, rows, _ = recording
    radio = next(r for r in rows if r.get("t") == "click" and r["el"]["role"] == "radio")
    assert radio["el"]["agent"] and radio["el"]["agent"]["id"].startswith("f0-n")
    state = next(r for r in rows if r.get("t") == "state" and r["id"] == radio["before"])
    ids = {f["id"] for f in state["model"]["fields"]} | {o["id"] for f in state["model"]["fields"] for o in (f.get("options") or [])}
    assert radio["el"]["agent"]["id"] in ids                                                # the control the person used is one the reader listed


def test_typed_values_are_saved_but_secrets_are_blanked_everywhere(recording):
    out, rows, _ = recording
    typed = {r["el"]["name"]: r["value"] for r in rows if r.get("t") == "change"}
    assert typed["Mobile number"] == "9123456780" and typed["Account password"] == "<blanked>"
    everything = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in [out / "events.jsonl", *(out / "states").glob("*.html")])
    assert "S3cret-Pass" not in everything
    assert "9123456780" in (out / "states").glob("S*.html").__next__().read_text(encoding="utf-8") or any("9123456780" in p.read_text(encoding="utf-8") for p in (out / "states").glob("S*.html"))


def test_each_action_links_the_state_before_and_the_state_after(recording):
    _, rows, _ = recording
    states = {r["id"]: r for r in rows if r.get("t") == "state"}
    clicks = [r for r in rows if r.get("t") == "click"]
    assert clicks and all(c["before"] in states for c in clicks[1:])
    assert all(any(c["step"] in s["after_steps"] for s in states.values()) for c in clicks)


def test_the_dom_changes_each_action_caused_are_recorded(recording):
    _, rows, _ = recording
    muts = [m for r in rows if r.get("t") == "state" for m in (r.get("delta") or {}).get("muts", [])]
    assert any(m["op"] == "attr" and m["attr"] == "aria-expanded" and m["now"] == "true" for m in muts)     # the dropdown opened
    assert any(m["op"] == "attr" and m["attr"] == "aria-checked" and m["now"] == "true" for m in muts)      # the radio was ticked
    assert any(m["op"] == "add" and m["modal"] for m in muts)                                               # an overlay appeared


def test_the_raw_event_sequence_is_kept_without_the_typed_characters(recording):
    _, rows, _ = recording
    evts = [e for r in rows if r.get("t") == "state" for e in (r.get("delta") or {}).get("evts", [])]
    assert {"pointerdown", "mousedown", "click", "keydown"} <= {e["type"] for e in evts} and all(e["trusted"] for e in evts if e["type"] == "click")
    assert not any(e.get("key") and len(e["key"]) == 1 and e["key"] != " " for e in evts)                   # never a typed letter or digit


def test_a_saved_state_reloads_standalone_and_the_engine_reads_it_the_same_way(recording):
    out, rows, live = recording
    last = [r for r in rows if r.get("t") == "state"][-1]
    html = (out / "states" / f"{last['id']}.html").read_text(encoding="utf-8")
    assert "shadowrootmode" in html and "Inner frame text" in html                                           # shadow DOM and the iframe are both inside

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content(html)
            got = {(f.kind, f.label) for f in (await perceive(page)).model.fields}
            await browser.close()
            return got
    replayed = run(go())
    assert replayed >= {k for k in live if k[1] in {"Mobile number", "Account password"}} and any(l == "State" for _, l in replayed)


def test_the_engines_own_view_and_classification_are_saved_with_each_state(recording):
    _, rows, _ = recording
    state = [r for r in rows if r.get("t") == "state"][-1]
    assert state["engine"]["state"] and {f["label"] for f in state["model"]["fields"]} >= {"Mobile number", "State"}


def test_study_names_the_control_the_reader_could_not_list_and_shows_what_it_could(recording):
    from recorder_study import study
    out, _, _ = recording
    text = study(out)
    assert (out / "report.md").is_file()
    gaps = text.split("## Reader gaps")[1].split("##")[0]
    assert "Magic toggle" in gaps and "selector" in gaps and "html:" in gaps                               # the control is identified exactly
    assert "Do you need sponsorship?" not in gaps and "Mobile number" not in gaps                          # what the reader lists is not reported
    assert "radio" in text and "-> field" in text or "-> option" in text


def test_promote_writes_sanitised_fixtures_that_the_replay_test_can_read(recording):
    import test_apply_recorded as replay
    from recorder_study import promote
    out, _, _ = recording
    dest = Path(tempfile.mkdtemp())
    facts = {"contact": {"email": "real.person@corp.io", "phone": "+91 9123456780", "name": "Real Person"}, "identity": {"first_name": "Real", "last_name": "Person"}}
    written = promote(out, "smoke", facts=facts, dest=dest)
    html_files = [p for p in written if p.suffix == ".html"]
    assert html_files and all("9123456780" not in p.read_text(encoding="utf-8") for p in html_files)
    assert any("9000000000" in p.read_text(encoding="utf-8") for p in html_files)                          # the typed number became a placeholder
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in written if p.suffix == ".json"]
    exps = [e for m in metas for e in m["interactions"]]
    assert any(e["label"] == "Magic toggle" and not e["seen_when_recorded"] for e in exps)                 # a gap, recorded as one
    assert any(e["label"] == "Do you need sponsorship?" and e["as"] == "field" and e["seen_when_recorded"] for e in exps)
    # replay one fixture through the reader and check the expectations the same way the permanent test does
    meta = next(m for m in metas if any(e["label"] == "Do you need sponsorship?" for m in [m] for e in m["interactions"]))
    html = next(p for p in html_files if p.stem == meta["state"]).read_text(encoding="utf-8")
    model = run(replay.read_fixture(html))
    for e in meta["interactions"]:
        assert replay.seen(model, e) == e["seen_when_recorded"], e


def test_a_tab_opened_by_the_page_is_followed_with_its_own_states_and_its_opener():
    """Apply often opens the application in a NEW tab (target=_blank): its first action must already have a 'before' state, and the opener is noted."""
    pages = {"/job": "<!doctype html><html><head><title>Job</title></head><body><main><a id=ap href='/apply' target=_blank>Apply now</a></main></body></html>",
             "/apply": "<!doctype html><html><head><title>Application</title></head><body><main><label>Your name <input id=nm></label><button id=go>Next</button></main></body></html>"}

    async def go():
        out = Path(tempfile.mkdtemp()) / "rec"
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            ctx = await browser.new_context()
            await ctx.route("http://app.test/**", lambda route: route.fulfill(status=200, content_type="text/html", body=pages[route.request.url.split("app.test")[1].split("?")[0]]))
            page = await ctx.new_page()
            rec = Recorder(ctx, out, settle=0.4)
            await rec.start()
            await page.goto("http://app.test/job")
            await asyncio.sleep(1.5)
            async with ctx.expect_page() as info:
                await page.locator("#ap").click()
            tab = await info.value
            await tab.wait_for_load_state()
            await asyncio.sleep(1.8)
            await tab.locator("#nm").click(); await tab.keyboard.type("Test"); await tab.keyboard.press("Tab"); await asyncio.sleep(1.2)
            await rec.stop()
            await browser.close()
        return [json.loads(l) for l in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    rows = run(go())
    new = next(r for r in rows if r.get("t") == "new_tab")
    opener = next(r for r in rows if r.get("t") == "tab_opener" and r["tab"] == new["tab"])
    assert opener["opener"] is not None                                                          # which tab opened it is recorded
    first = next(r for r in rows if r.get("t") in {"click", "change"} and r["el"]["name"] == "Your name" and r["page"] == new["tab"])
    states = {r["id"]: r for r in rows if r.get("t") == "state"}
    assert first["before"] in states and any(f["label"] == "Your name" for f in states[first["before"]]["model"]["fields"])   # seen before the first action


def test_every_click_names_the_page_read_whose_agent_ids_it_carries(recording):
    """A snapshot can finish after the next click: the click must still map to the read whose stamps were live (before_epoch), not to 'the last saved state'."""
    from recorder_study import load
    out, rows, _ = recording
    epochs = {r["epoch"] for r in rows if r.get("t") == "state"}
    clicks = [r for r in rows if r.get("t") == "click"]
    assert epochs and all(c["before_epoch"] in epochs for c in clicks[1:])
    _, states, inter = load(out)
    for it in inter:
        agent = ((it["el"].get("agent") or {}).get("id"))
        if agent and it.get("before"):
            m = states[it["before"]]["model"]
            ids = {f["id"] for f in m["fields"]} | {o["id"] for f in m["fields"] for o in (f.get("options") or []) if o.get("id")} | {a["id"] for a in m["actions"]}
            assert agent in ids or it["el"].get("role") in {"option", "button", ""}, (it["step"], agent, it["before"])


def test_an_older_recording_without_epochs_is_linked_to_the_state_that_holds_the_stamp():
    from recorder_study import _nearest_state
    mk = lambda ids: {"model": {"fields": [{"id": i, "label": "Mobile number", "options": []} for i in ids], "actions": []}}
    states = {"S001": mk(["f0-n1"]), "S002": mk(["f0-n7"]), "S003": mk(["f0-n9"])}
    it = {"before": "S001", "el": {"name": "Mobile number", "agent": {"id": "f0-n7"}}}
    assert _nearest_state(it, states, list(states)) == "S002"                                  # S001 was superseded before the click landed
    assert _nearest_state({"before": "S001", "el": {"name": "Mobile number", "agent": {"id": "f0-n1"}}}, states, list(states)) == "S001"


def test_each_click_saves_the_page_as_it_was_with_the_exact_control_marked(recording):
    out, rows, _ = recording
    clicks = [r for r in rows if r.get("t") == "click"]
    assert clicks and all(c.get("dom_file") for c in clicks)
    radio = next(c for c in clicks if c["el"]["role"] == "radio")
    html = (out / "states" / radio["dom_file"]).read_text(encoding="utf-8")
    assert 'data-rec-target="control"' in html and 'data-rec-target="hit"' in html and 'shadowrootmode' in html     # the circle that was hit, and the radio around it
    assert "S3cret-Pass" not in html


def test_the_reader_is_replayed_on_the_page_as_it_was_so_seen_and_not_seen_are_exact(recording):
    from recorder_study import analyse
    out, _, _ = recording
    _, _, judged = analyse(out)
    by_name = {(it["el"].get("group") or it["el"].get("name") or it["el"].get("text")): v for it, _, v in judged if it["t"] == "click"}
    assert by_name["Do you need sponsorship?"][0] in {"field", "option"}                      # the radio inside the shadow root: listed by the reader
    assert by_name["Magic toggle"][0] == "not-seen"                                           # a plain div with a click handler: not listed, found by identity
    assert all(it.get("_exact") for it, _, _ in judged if it.get("_replay"))
