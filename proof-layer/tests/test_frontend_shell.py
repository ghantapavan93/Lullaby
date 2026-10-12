"""The page shell a forwarded link opens: what every served HTML page carries
in its <head>, and the headers both hosts (Vercel's static bundle and the
review gateway) send with it.

Invariants:
- a link preview explains itself (title, description, image), and the image exists;
- the browser chrome matches the theme, and phones get a home-screen icon;
- no inline event handlers, so a strict Content-Security-Policy holds: every
  inline <script> is allowed by its hash, never by 'unsafe-inline';
- the page can't be framed, and powerful browser features are off;
- after a deploy, a page never runs the previous deploy's JS or CSS.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from base64 import b64encode

import pytest
from starlette.testclient import TestClient

from .conftest import ROOT

REPO = ROOT.parent
STATIC = ROOT / "static"
LANDING = REPO / "deploy" / "landing" / "index.html"
PAGES = [STATIC / "index.html", LANDING, STATIC / "privacy.html", STATIC / "404.html"]
SECURITY = ("Content-Security-Policy", "Permissions-Policy", "X-Content-Type-Options", "Referrer-Policy",
            "X-Robots-Tag")


def inline_scripts(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)


def sha256(source: str) -> str:
    return "'sha256-" + b64encode(hashlib.sha256(source.encode()).digest()).decode() + "'"


def load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    build = load("vercel_build_under_test", REPO / "deploy" / "vercel" / "build.py")
    out = build.build("https://backend.example.com", tmp_path_factory.mktemp("vercel") / "out")
    return out, json.loads((out / "vercel.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    import os
    os.environ["GATEWAY_STATE"] = str(tmp_path_factory.mktemp("gw") / "gateway.sqlite3")
    sys.path.insert(0, str(REPO / "deploy" / "gateway"))
    module = load("gateway_shell_under_test", REPO / "deploy" / "gateway" / "gateway.py")
    yield TestClient(module.app)
    module.store.db.close()


@pytest.mark.parametrize("page", ["index.html", "app.html"])
def test_a_forwarded_link_previews_with_a_title_a_summary_and_an_image(bundle, page):
    out, _ = bundle
    html = (out / page).read_text(encoding="utf-8")
    for tag in ('property="og:title"', 'property="og:description"', 'property="og:image"',
                'name="twitter:card"', 'name="theme-color"', 'name="color-scheme"', 'rel="apple-touch-icon"'):
        assert tag in html, f"{page} lacks {tag}"
    image = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
    assert image.startswith("https://"), "scrapers need an absolute image URL"
    assert (out / "static" / image.rsplit("/static/", 1)[1].split("?")[0]).is_file()


def test_no_served_markup_carries_an_inline_event_handler():
    sources = [STATIC / "app.js", *PAGES]
    handlers = [(p.name, m) for p in sources for m in re.findall(r"\son[a-z]+=\"", p.read_text(encoding="utf-8"))]
    assert handlers == []


def test_the_bundle_sends_a_strict_policy_that_still_allows_its_own_inline_scripts(bundle):
    out, config = bundle
    every = next(h for h in config["headers"] if h["source"] == "/(.*)")
    headers = {h["key"]: h["value"] for h in every["headers"]}
    assert set(SECURITY) <= set(headers)
    csp = headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    script_src = re.search(r"script-src ([^;]+)", csp).group(1)
    assert "'unsafe-inline'" not in script_src
    for page in ("index.html", "app.html", "privacy.html", "404.html"):
        for source in inline_scripts((out / page).read_text(encoding="utf-8")):
            assert sha256(source) in script_src, f"an inline script in {page} isn't allowed by hash"


def test_after_a_deploy_pages_load_that_deploys_assets(bundle):
    out, config = bundle
    html = (out / "app.html").read_text(encoding="utf-8")
    versioned = re.findall(r'(?:src|href)="(/static/[^"]+\.(?:js|css))\?v=([0-9a-f]{10})"', html)
    assert {p for p, _ in versioned} >= {"/static/app.js", "/static/app.css", "/static/tokens.css"}
    assert len({v for _, v in versioned}) == 1
    static = next(h for h in config["headers"] if h["source"] == "/static/(.*)")
    assert "immutable" in static["headers"][0]["value"]


@pytest.mark.parametrize("path", ["/", "/app", "/privacy", "/static/app.css"])
def test_the_gateway_sends_the_same_policy(gateway, path):
    r = gateway.get(path)
    assert r.status_code == 200
    for key in SECURITY:
        assert key in r.headers, f"{path} lacks {key}"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
