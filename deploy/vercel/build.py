"""Build the static frontend for Vercel.

    python deploy/vercel/build.py --backend https://203-0-113-7.sslip.io

Output (deploy/vercel/out/):
- /            the landing page (deploy/landing/index.html)
- /app         the prototype (proof-layer/static/index.html)
- /static/*    the app's own CSS, JS and icons, cached for good: each page
               links them with ?v=<content hash>, so a deploy never mixes
               new HTML with old JS
- vercel.json  rewrites /v1/*, /architecture* and /healthz to the backend,
               and the security headers shared with the gateway

The browser only ever talks to the Vercel origin, so the gateway's workspace
cookie, the media link cookie (path-scoped, SameSite=Strict) and video Range
requests work exactly as they do on one host. No framework, no build step for
the app itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "proof-layer" / "static"
OUT = Path(__file__).with_name("out")
SITE = "https://lullaby-rho.vercel.app"
PAGES = ("index.html", "app.html", "privacy.html", "404.html")

sys.path.insert(0, str(REPO / "deploy" / "gateway"))
from headers import security_headers  # noqa: E402


def asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(STATIC.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(STATIC).as_posix().encode() + path.read_bytes())
    return digest.hexdigest()[:10]


def finish_page(path: Path, version: str, site: str) -> None:
    """Version the page's own CSS and JS links, and make the preview image absolute."""
    html = path.read_text(encoding="utf-8")
    html = re.sub(r'((?:src|href)="/static/[^"?]+\.(?:js|css))"', rf'\1?v={version}"', html)
    html = html.replace('content="/static/', f'content="{site}/static/')
    path.write_text(html, encoding="utf-8")


def build(backend: str, out: Path = OUT, site: str = SITE) -> Path:
    backend = backend.rstrip("/")
    if not backend.startswith("https://"):
        raise SystemExit("the backend must be an https:// origin")
    # Clear the last build but keep `.vercel/`, the link to the Vercel project:
    # without it the CLI creates a new project instead of updating this one.
    if out.exists():
        for item in out.iterdir():
            if item.name != ".vercel":
                shutil.rmtree(item) if item.is_dir() else item.unlink()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, out / "static")
    shutil.copy(REPO / "deploy" / "landing" / "index.html", out / "index.html")
    shutil.copy(STATIC / "index.html", out / "app.html")
    for page in ("privacy.html", "404.html"):
        if (STATIC / page).exists():
            shutil.copy(STATIC / page, out / page)
    version = asset_version()
    for page in PAGES:
        if (out / page).exists():
            finish_page(out / page, version, site.rstrip("/"))
    headers = security_headers([out / page for page in PAGES])
    config = {
        "cleanUrls": True,
        "trailingSlash": False,
        "rewrites": [
            {"source": "/v1/:path*", "destination": f"{backend}/v1/:path*"},
            {"source": "/architecture", "destination": f"{backend}/architecture"},
            {"source": "/architecture/:path*", "destination": f"{backend}/architecture/:path*"},
            {"source": "/healthz", "destination": f"{backend}/healthz"},
        ],
        "headers": [
            {"source": "/(.*)", "headers": [{"key": k, "value": v} for k, v in headers.items()]},
            {"source": "/static/(.*)", "headers": [{"key": "Cache-Control",
                                                    "value": "public, max-age=31536000, immutable"}]},
        ],
    }
    (out / "vercel.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--site", default=SITE, help="the public origin, for the link-preview image")
    a = ap.parse_args()
    print(build(a.backend, Path(a.out), a.site))
