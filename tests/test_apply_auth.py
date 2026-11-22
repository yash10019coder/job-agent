"""Credentials and emailed codes: storage, password strength, code/link extraction, Gmail reading (mocked HTTP)."""
import asyncio
import base64
import json
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from job_agent.apply.auth import Credential, MemoryStore, default_store, generate_password, site_key
from job_agent.apply.mailcode import GmailProvider, MailCode, StaticCodeProvider, extract_code, extract_link, site_hints


def test_site_key_is_the_host_and_credentials_never_print_the_password():
    assert site_key("https://www.browserstack.wd3.myworkdayjobs.com/en-US/x") == "browserstack.wd3.myworkdayjobs.com"
    assert site_key("file:///tmp/x.html") == "local"
    cred = Credential("me@example.com", "Sup3r-Secret!")
    assert "Sup3r" not in repr(cred) and "Sup3r" not in str(cred)


def test_generated_passwords_are_strong_and_different_each_time():
    seen = {generate_password() for _ in range(20)}
    assert len(seen) == 20
    for pw in seen:
        assert len(pw) >= 18 and re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw) and re.search(r"\d", pw) and re.search(r"[!@#$%^&*\-_+=]", pw)


def test_memory_store_round_trip_and_forget():
    store = MemoryStore()
    store.save("a.com", Credential("u", "p"))
    assert store.get("a.com").password == "p" and store.sites() == ["a.com"]
    store.forget("a.com")
    assert store.get("a.com") is None


def test_keyring_store_round_trip_when_the_os_keyring_works():
    store = default_store()
    if store is None:
        return  # no usable keyring on this machine: the controller then refuses to create accounts
    site = f"job-agent-test-{int(time.time())}.example"
    try:
        store.save(site, Credential("me@example.com", "pw-123"))
        assert store.get(site).username == "me@example.com" and site in store.sites()
    finally:
        store.forget(site)
    assert store.get(site) is None and site not in store.sites()


def test_code_extraction_from_typical_emails():
    assert extract_code("Your verification code is 482913. It expires in 10 minutes.") == "482913"
    assert extract_code("Use security code: 7731 to continue") == "7731"
    assert extract_code("482913 is your one-time code") == "482913"
    assert extract_code("Enter OTP 559102 on the page") == "559102"
    assert extract_code("Order #12 shipped on 2026-11-14") is None


def test_verification_link_extraction_skips_unsubscribe_and_tracking():
    text = 'Click <a href="https://x.com/unsubscribe?u=1">unsubscribe</a> or <a href="https://acme.wd3.myworkdayjobs.com/verify?token=abc&amp;e=1">verify</a>'
    assert extract_link(text) == "https://acme.wd3.myworkdayjobs.com/verify?token=abc&e=1"
    assert extract_link("see https://acme.com/privacy and https://acme.com/jobs") is None


def test_site_hints_name_the_company_and_the_vendor():
    h = site_hints("https://browserstack.wd3.myworkdayjobs.com/en-US/External/job/x")
    assert "browserstack" in h and "workday" in h
    assert "navitus" in site_hints("https://careers-navitus.icims.com/jobs/1") and "icims" in site_hints("https://careers-navitus.icims.com/jobs/1")


def gmail_message(subject, body, sender, when):
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {"id": "m1", "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
                        "mimeType": "text/plain", "body": {"data": data}}}


def make_provider(messages):
    token = Path(tempfile.mkdtemp()) / "t.json"
    token.write_text(json.dumps({"access_token": "tok", "expires_at": time.time() + 3600, "refresh_token": "r", "client_id": "c", "client_secret": "s"}))

    def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer tok"
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        return httpx.Response(200, json=messages[0])
    return GmailProvider(token, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_gmail_provider_returns_the_code_from_a_matching_recent_message():
    now = datetime.now()
    provider = make_provider([gmail_message("Your Acme verification code", "Your code is 482913", "noreply@acme.com", now)])
    got = asyncio.run(provider.fetch(["acme"], now - timedelta(minutes=2), timeout_s=1))
    assert got and got.code == "482913"


def test_gmail_provider_ignores_other_senders_and_old_messages():
    now = datetime.now()
    other = make_provider([gmail_message("Your bank code", "Your code is 111111", "noreply@bank.com", now)])
    assert asyncio.run(other.fetch(["acme"], now - timedelta(minutes=2), timeout_s=0.1)) is None
    old = make_provider([gmail_message("Acme code", "Your code is 222222", "noreply@acme.com", now - timedelta(hours=3))])
    assert asyncio.run(old.fetch(["acme"], now - timedelta(minutes=2), timeout_s=0.1)) is None


def test_static_provider_is_a_queue():
    provider = StaticCodeProvider(MailCode(code="1"))
    assert asyncio.run(provider.fetch([], datetime.now())).code == "1" and asyncio.run(provider.fetch([], datetime.now())) is None
