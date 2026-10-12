"""Build the three Excalidraw boards from the code they describe.

    proof-layer/.venv/Scripts/python docs/architecture/boards.py

Writes editable scenes (`*.excalidraw`, Excalidraw's JSON scene format v2) to
docs/architecture/. Rendering and SVG export are done by the real Excalidraw
library (docs/architecture/viewer.html), never by this script.

Before writing anything the script checks its claims and stops on the first
one that fails:
- Board A: every arrow names a file and a symbol, and the symbol must be in
  that file. Proposed components are drawn dashed and labelled PROPOSED.
- Board B: every branch names a Scenario Studio scenario (or a test-only
  case). The scenario's expected status must equal the board's decision, and
  every test named must exist in proof-layer/tests.
- Board C: every frame embeds a real screenshot from artifacts/visual-qa.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "proof-layer"
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

INK, MUTED, RED, GREY = "#1e1e1e", "#495057", "#c92a2a", "#868e96"
FILL = {"client": "#e7f5ff", "route": "#fff9db", "trust": "#ffe3e3", "domain": "#ebfbee", "async": "#f3f0ff",
        "db": "#f1f3f5", "sim": "#fff4e6", "proposed": "#ffffff", "note": "#f8f9fa", "ok": "#d3f9d8",
        "warn": "#fff3bf", "stop": "#ffe3e3"}


class Board:
    def __init__(self, name: str):
        self.name, self.elements, self.files = name, [], {}
        self.rng = random.Random(name)
        self.boxes: dict[str, dict] = {}

    def _base(self, kind: str, eid: str, x: float, y: float, w: float, h: float, **kw) -> dict:
        e = {"id": eid, "type": kind, "x": round(x, 1), "y": round(y, 1), "width": round(w, 1), "height": round(h, 1),
             "angle": 0, "strokeColor": kw.get("stroke", INK), "backgroundColor": kw.get("fill", "transparent"),
             "fillStyle": "solid", "strokeWidth": kw.get("stroke_width", 1),
             "strokeStyle": "dashed" if kw.get("dashed") else "solid", "roughness": 0, "opacity": 100,
             "groupIds": [], "frameId": None, "roundness": kw.get("roundness"), "seed": self.rng.randint(1, 2**31),
             "version": 1, "versionNonce": self.rng.randint(1, 2**31), "isDeleted": False, "boundElements": [],
             "updated": 1791700000000, "link": None, "locked": False}
        self.elements.append(e)
        return e

    @staticmethod
    def _wrap(text: str, width: float, size: int) -> str:
        per_line = max(8, int(width / (size * 0.55)))
        return "\n".join("\n".join(textwrap.wrap(p, per_line) or [""]) for p in text.split("\n"))

    def text(self, eid: str, x: float, y: float, text: str, size: int = 16, color: str = INK, width: float = 600,
             container: dict | None = None, align: str = "left", bold: bool = False) -> dict:
        wrapped = self._wrap(text, width, size)
        lines = wrapped.split("\n")
        w = max(len(line) for line in lines) * size * 0.55
        h = len(lines) * size * 1.25
        e = self._base("text", eid, x, y, w, h, stroke=color)
        e.update({"text": wrapped, "originalText": wrapped, "fontSize": size, "fontFamily": 2 if not bold else 2,
                  "textAlign": align, "verticalAlign": "top" if container is None else "middle",
                  "containerId": container["id"] if container else None, "lineHeight": 1.25,
                  "autoResize": True, "roundness": None, "boundElements": None})
        if container is not None:
            container["boundElements"].append({"type": "text", "id": eid})
            e["x"] = round(container["x"] + 8, 1)
            e["y"] = round(container["y"] + (container["height"] - h) / 2, 1)
            e["width"] = round(container["width"] - 16, 1)
        return e

    def box(self, key: str, x: float, y: float, w: float, h: float, text: str, kind: str = "domain",
            size: int = 15, dashed: bool = False, stroke: str = INK, align: str = "left") -> dict:
        r = self._base("rectangle", f"{key}", x, y, w, h, fill=FILL[kind], dashed=dashed, stroke=stroke,
                       roundness={"type": 3}, stroke_width=1)
        self.text(f"{key}-t", x, y, text, size=size, width=w - 16, container=r, align=align)
        self.boxes[key] = r
        return r

    def frame_label(self, eid: str, x: float, y: float, w: float, h: float, title: str, dashed: bool = False,
                    stroke: str = MUTED) -> None:
        self._base("rectangle", eid, x, y, w, h, fill="transparent", dashed=dashed, stroke=stroke,
                   roundness={"type": 3}, stroke_width=1)
        if title:
            self.text(eid + "-label", x + 10, y + 6, title, size=18, color=stroke, width=w - 20)

    def arrow(self, eid: str, a: str, b: str, label: str = "", *, color: str = INK, dashed: bool = False,
              side: str = "auto", off: float = 0, via: list | None = None) -> dict:
        """A bound arrow from box a to box b. `off` shifts it sideways (two
        arrows between the same boxes); `via` routes it through absolute
        waypoints (around boxes, through a column gap)."""
        ra, rb = self.boxes[a], self.boxes[b]
        acx, acy = ra["x"] + ra["width"] / 2, ra["y"] + ra["height"] / 2
        bcx, bcy = rb["x"] + rb["width"] / 2, rb["y"] + rb["height"] / 2
        horizontal = side == "h" or (side == "auto" and abs(bcx - acx) > abs(bcy - acy))
        if horizontal:
            sx = ra["x"] + ra["width"] if bcx > acx else ra["x"]
            ex = rb["x"] if bcx > acx else rb["x"] + rb["width"]
            sy, ey = acy, bcy
        else:
            sy = ra["y"] + ra["height"] if bcy > acy else ra["y"]
            ey = rb["y"] if bcy > acy else rb["y"] + rb["height"]
            sx, ex = acx, bcx
        if horizontal:
            sy, ey = sy + off, ey + off
        else:
            sx, ex = sx + off, ex + off
        if via:
            first, last = via[0], via[-1]
            if abs(first[0] - acx) > abs(first[1] - acy):          # leave sideways
                sx, sy = (ra["x"] + ra["width"] if first[0] > acx else ra["x"]), first[1]
            else:
                sx, sy = first[0], (ra["y"] + ra["height"] if first[1] > acy else ra["y"])
            if abs(last[0] - bcx) > abs(last[1] - bcy):            # arrive sideways
                ex, ey = (rb["x"] if last[0] < bcx else rb["x"] + rb["width"]), last[1]
            else:
                ex, ey = last[0], (rb["y"] if last[1] < bcy else rb["y"] + rb["height"])
            pts = [(sx, sy)] + [tuple(v) for v in via] + [(ex, ey)]
        else:
            pts = [(sx, sy), (ex, ey)]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        e = self._base("arrow", eid, sx, sy, max(xs) - min(xs), max(ys) - min(ys), stroke=color, dashed=dashed,
                       roundness=None if via else {"type": 2}, stroke_width=2 if not dashed else 1)
        mid_a, mid_b = (pts[len(pts) // 2 - 1], pts[len(pts) // 2]) if via else (pts[0], pts[1])
        e.update({"points": [[round(x - sx, 1), round(y - sy, 1)] for x, y in pts], "lastCommittedPoint": None,
                  "startBinding": {"elementId": a, "focus": 0, "gap": 4},
                  "endBinding": {"elementId": b, "focus": 0, "gap": 4},
                  "startArrowhead": None, "endArrowhead": "arrow", "elbowed": False})
        ra["boundElements"].append({"type": "arrow", "id": eid})
        rb["boundElements"].append({"type": "arrow", "id": eid})
        if label:
            t = self.text(eid + "-l", 0, 0, label, size=12, color=color, width=220)
            t.update({"containerId": eid, "textAlign": "center", "verticalAlign": "middle",
                      "x": round((mid_a[0] + mid_b[0]) / 2 - t["width"] / 2, 1),
                      "y": round((mid_a[1] + mid_b[1]) / 2 - t["height"] / 2, 1)})
            e["boundElements"].append({"type": "text", "id": t["id"]})
        return e

    def image(self, eid: str, x: float, y: float, w: float, png: bytes) -> None:
        file_id = hashlib.sha1(png).hexdigest()
        width, height = _png_size(png)
        h = w * height / width
        e = self._base("image", eid, x, y, w, h, stroke="transparent")
        e.update({"fileId": file_id, "status": "saved", "scale": [1, 1], "roundness": None, "boundElements": None})
        self.files[file_id] = {"mimeType": "image/png", "id": file_id, "created": 1791700000000,
                               "dataURL": "data:image/png;base64," + base64.b64encode(png).decode()}
        return h

    def save(self) -> Path:
        scene = {"type": "excalidraw", "version": 2, "source": "nanit-proof-layer/docs/architecture/boards.py",
                 "elements": self.elements,
                 "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": self.files}
        path = OUT / f"{self.name}.excalidraw"
        path.write_text(json.dumps(scene, indent=1), encoding="utf-8")
        return path


def _png_size(png: bytes) -> tuple[int, int]:
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def check_ref(ref: str) -> None:
    """'path/to/file.py:symbol' must name a symbol present in that file."""
    path, symbol = ref.split(":", 1)
    text = (ROOT / path).read_text(encoding="utf-8")
    if symbol not in text:
        raise SystemExit(f"Board A claims {ref}, but {symbol!r} isn't in {path}")


# ---------------------------------------------------------------- Board A

def board_architecture() -> Path:
    b = Board("A-backend-architecture")
    b.text("title", 40, 20, "Nanit Proof Layer: backend architecture (as built, 2026-10-11)", size=30, width=1800)
    b.text("sub", 40, 64, "Independent engineering prototype. Synthetic data, simulated payments, not affiliated with "
           "Nanit. Solid arrow = a real code path (listed in ARCHITECTURE_EDGES.md; boards.py checks each one). "
           "Red dashed = failure and recovery path. Grey dashed = PROPOSED, not built.", size=15, color=MUTED,
           width=2300)
    R = [170 + 130 * i for i in range(8)]          # row tops; boxes are 110 high
    H = 110
    # Clients
    b.frame_label("f-clients", 30, 120, 330, 1100, "Clients")
    b.box("web", 50, R[0], 290, H, "Parent web app\nstatic/app.js (no framework)\nshows only what the API returns",
          "client")
    b.box("api", 50, R[1], 290, H, "Any API client (mobile-style)\nversioned /v1 JSON, stable error codes, "
          "Idempotency-Key, expected_revision", "client", size=14)
    b.box("legend", 50, R[2], 290, 3 * 130 - 20, "How to read this\n• Simulated: the store events, the payment "
          "provider, the households and payments.\n• Real: PostgreSQL constraints and locks, the worker process, "
          "video decoding and measurement.\n• Not claimed: production readiness (the four-process soak FAILED).",
          "note", size=14)
    b.box("media", 50, R[5], 290, H, "<video> player\nsigned 5-minute media cookie, scoped to one clip (no "
          "token in any URL)", "client", size=14)
    b.box("eng", 50, R[7], 290, H, "Engineering views: X-Ray, Growth Lab, Scenario Studio (Studio and "
          "/v1/dev/* only with PROOF_LAYER_DEV=1)", "client", size=13)
    # Trust boundary
    b.frame_label("f-trust", 400, 120, 330, 560, "Trust boundary (every request)", stroke=RED)
    b.box("authn", 420, R[0], 290, H, "authenticate: signed member token\napp/security.py", "trust")
    b.box("lock", 420, R[1], 290, H, "write_scope: household lock (SELECT … FOR UPDATE), re-check membership, "
          "authorize(POLICY role)", "trust", size=14)
    b.box("tenancy", 420, R[2], 290, H, "load_owned: rows only inside the caller's household; a foreign or "
          "missing id is the same 404", "trust", size=14)
    b.box("authlog", 420, R[3], 290, H, "authorization_log: every allow and deny recorded", "trust", size=14)
    b.frame_label("f-proposed", 400, 700, 330, 520, "PROPOSED (not built)", dashed=True, stroke=GREY)
    b.box("p-store", 420, 745, 290, H, "Real store notifications: verify Apple JWS / Google Pub/Sub, "
          "re-fetch current state, then purchase_evidence.record", "proposed", size=13, dashed=True, stroke=GREY)
    b.box("p-proxy", 420, 945, 290, H + 10, "One listening socket per process behind a proxy (the "
          "four-process soak FAILED on a shared Windows accept)", "proposed", size=13, dashed=True, stroke=GREY)
    # Routes
    b.frame_label("f-routes", 770, 120, 360, 1100, "FastAPI routes (app/main.py)")
    b.box("r-dev", 790, R[0], 320, H, "/v1/dev/*: scenarios, store events, worker (dev only; 404 otherwise)",
          "route", size=14)
    b.box("r-brief", 790, R[1], 320, H, "/v1/windows/{id}/brief, /corrections, /acknowledgments", "route", size=14)
    b.box("r-access", 790, R[2], 320, 240, "/v1/access\n/cameras/{id}\n/check\n/bind (Idempotency-Key, "
          "expected_state_revision)\n/verify", "route", size=14)
    b.box("r-commerce", 790, R[4], 320, H, "/v1/capability-decisions\n/v1/purchase-intents", "route", size=14)
    b.box("r-video", 790, R[5], 320, H, "/v1/video/assets…, /media-link, /annotations, /acknowledge "
          "(PROOF_LAYER_VIDEO)", "route", size=14)
    b.box("r-growth", 790, R[6], 320, H, "/v1/analytics, /v1/render-receipts, /v1/access/metrics", "route", size=14)
    b.box("r-xray", 790, R[7], 320, H, "/v1/xray, /v1/proof (read-only)", "route", size=14)
    # Domain
    L, RR = 1190, 1530
    b.frame_label("f-domain", 1170, 120, 700, 1100, "Domain (decisions over stored facts)")
    b.box("evidence", L, R[0], 320, H, "purchase_evidence.record: versioned store events; the top version wins; "
          "the same version disagreeing = conflicting", "domain", size=13)
    b.box("lifecycle", RR, R[0], 320, H, "provider_lifecycle.translate: Apple/Google event → verified, grace, "
          "billing_retry, paused, expired, refunded", "domain", size=13)
    b.box("brief", L, R[1], 320, H, "engine / briefing: night revisions, corrections, per-revision "
          "acknowledgments", "domain", size=13)
    b.box("claims", RR, R[1], 320, H, "claims.record via apply_periods: one camera per purchase; a renewal is a "
          "new period on the same camera", "domain", size=13)
    b.box("assess", L, R[2], 320, H, "access.assess (read-only): 12 steps; fixed status precedence, "
          "contradictions first", "domain", size=13)
    b.box("plan", RR, R[2], 320, H, "entitlements.effective_plan: the camera's plan derived only from its "
          "claims", "domain", size=13)
    b.box("verify", L, R[3], 320, H, "access.bind / verify: connect = operation; check = capability; feature "
          "opened = client event", "domain", size=13)
    b.box("purchases", RR, R[3], 320, H, "purchases.prepare: purchase intent and outbox job in one "
          "transaction", "domain", size=13)
    b.box("decide", L, R[4], 320, H, "decisions.evaluate_now: offer only when the plan is the sole blocker; "
          "withheld if already bought", "domain", size=13)
    b.box("vidom", L, R[5], 320, H, "video_evidence: gate = plan includes 7-day video history; runs; reports as "
          "new revisions", "domain", size=13)
    b.box("metrics", L, R[6], 320, H, "access_metrics.funnel: computed at read time from stored rows; labelled "
          "synthetic", "domain", size=13)
    # Durable work
    b.frame_label("f-async", 1910, 120, 440, 700, "Durable work (at least once, idempotent)")
    b.box("provider", 1930, R[0], 195, 240, "Provider simulator (SIMULATED)\nown DB provider_sim; dedupes on the "
          "operation key", "sim", size=13)
    b.box("vproc", 2140, R[0], 195, 240, "video_processing (PyAV + numpy): decoded frames, brightness, sharpness, "
          "camera shift, local change. No sleep or health", "async", size=13)
    b.box("worker", 1930, R[2] + 70, 405, H, "app/worker.py run_once (separate OS process): submit_purchase · "
          "notify_revision · analyze_video", "async", size=13)
    b.box("outbox", 1930, R[4], 405, H, "outbox table: dedupe_key unique; claimed FOR UPDATE SKIP LOCKED with a "
          "lease", "async", size=13)
    # Database
    b.frame_label("f-db", 30, 1260, 2320, 290,
                  "PostgreSQL 17 (one application database; the provider simulator in its own)")
    tables = [
        ("t-claims", "entitlement_claims: uq_claim_event NULLS NOT DISTINCT; ex_claim_one_camera (btree_gist)"),
        ("t-evid", "purchase_evidence + purchase_refs: append-only; one owner household per purchase"),
        ("t-access", "access_actions / access_checks: append-only; created_at = clock_timestamp()"),
        ("t-intents", "purchase_intents + intent_transitions: unique (household, Idempotency-Key)"),
        ("t-outbox", "outbox: unique dedupe_key; lease_until"),
        ("t-video", "video_assets / runs / observations / annotations / acknowledgments: one run per (clip, "
                    "processor, config); 3 append-only"),
        ("t-brief", "evidence_events / briefing_revisions / acknowledgments: append-only; corrections supersede"),
        ("t-growth", "analytics_events, render_receipts, experiment_assignments: idempotent; allow-listed "
                     "properties; kill switch"),
    ]
    for i, (key, label) in enumerate(tables):
        b.box(key, 50 + (i % 4) * 572, 1305 + (i // 4) * 120, 550, 100, label, "db", size=14)

    edges = [
        ("e1", "web", "authn", "Bearer token", "proof-layer/app/security.py:def authenticate", {}),
        ("e2", "api", "authn", "same API", "proof-layer/app/security.py:def authenticate", {}),
        ("e3", "authn", "lock", "writes", "proof-layer/app/main.py:def write_scope", {"side": "v"}),
        ("e4", "lock", "tenancy", "", "proof-layer/app/security.py:def load_owned", {"side": "v"}),
        ("e5", "lock", "r-access", "", "proof-layer/app/main.py:def access_bind", {}),
        ("e6", "r-access", "assess", "assess()", "proof-layer/app/access.py:def assess", {"off": -18}),
        ("e7", "r-access", "verify", "bind / verify", "proof-layer/app/access.py:def verify", {"off": -18}),
        ("e8", "r-commerce", "decide", "evaluate_now", "proof-layer/app/decisions.py:def evaluate_now", {}),
        ("e9", "r-video", "vidom", "view / annotate", "proof-layer/app/video_evidence.py:def annotate", {}),
        ("e10", "r-growth", "metrics", "", "proof-layer/app/access_metrics.py:def household_funnel", {}),
        ("e11", "r-dev", "evidence", "store event", "proof-layer/app/main.py:def dev_purchase_evidence", {}),
        ("e11b", "r-brief", "brief", "", "proof-layer/app/main.py:evidence.apply_correction", {}),
        ("e12", "evidence", "lifecycle", "translate",
         "proof-layer/app/purchase_evidence.py:provider_lifecycle.translate", {}),
        ("e13", "evidence", "claims", "apply_periods", "proof-layer/app/purchase_evidence.py:def apply_periods", {}),
        ("e14", "claims", "plan", "", "proof-layer/app/claims.py:def camera_plan", {"side": "v"}),
        ("e15", "plan", "assess", "camera_plan", "proof-layer/app/access.py:claims.camera_plan", {}),
        ("e16", "decide", "purchases", "offer accepted", "proof-layer/app/purchases.py:def prepare", {}),
        ("e17", "purchases", "outbox", "submit job", "proof-layer/app/purchases.py:OutboxJob(", {}),
        ("e18", "vidom", "outbox", "analyze_video job", "proof-layer/app/video_evidence.py:kind=\"analyze_video\"",
         {}),
        ("e19", "outbox", "worker", "claim (SKIP LOCKED)",
         "proof-layer/app/worker.py:with_for_update(skip_locked=True)", {"side": "v", "off": -110}),
        ("e20", "worker", "provider", "same key", "proof-layer/app/worker.py:def process_submit",
         {"side": "v", "off": -40}),
        ("e21", "worker", "vproc", "analyze", "proof-layer/app/worker.py:def process_video", {"side": "v", "off": 100}),
        ("e22", "media", "r-video", "media cookie", "proof-layer/app/main.py:def video_media", {}),
        ("e23", "eng", "r-xray", "", "proof-layer/app/main.py:def xray", {}),
    ]
    gate_via = [(1520, R[5] + H / 2), (1520, R[2] + H + 10), (RR + 160, R[2] + H + 10)]
    failures = [
        ("x1", "worker", "outbox", "x1", "proof-layer/app/worker.py:lease_until < func.now()",
         {"side": "v", "off": 110}),
        ("x2", "provider", "worker", "x2", "proof-layer/app/worker.py:def reconcile_intents", {"side": "v", "off": 60}),
        ("x3", "assess", "r-access", "x3", "proof-layer/app/access.py:STORE_ENDED_CAMERA_ACTIVE", {"off": 22}),
        ("x4", "verify", "r-access", "x4", "proof-layer/app/access.py:STALE_ACCESS_VIEW", {"off": 22}),
    ]
    b.box("failures", RR, 950, 320, 240, "Red dashed arrows (failure and recovery)\nx1 worker dies → its lease "
          "expires → the job is reclaimed\nx2 provider reply lost → reconcile from its ledger, same key\nx3 "
          "contradiction → CONFLICTING_EVIDENCE, above 'checking'\nx4 screen built on old facts → 409 "
          "STALE_ACCESS_VIEW", "stop", size=13)
    for eid, a, c, label, ref, kw in edges:
        check_ref(ref)
        b.arrow(eid, a, c, label, **kw)
    check_ref("proof-layer/app/video_evidence.py:entitlements.evaluate")
    b.arrow("e24", "vidom", "plan", "gate", via=gate_via)
    for eid, a, c, label, ref, kw in failures:
        check_ref(ref)
        b.arrow(eid, a, c, label, color=RED, dashed=True, **kw)
    edges_md = ["# Board A: what each arrow is", "", "Generated by `docs/architecture/boards.py`, which stops if a "
                "named symbol is missing from its file.", "", "| Arrow | From → to | Label | Code |",
                "|---|---|---|---|"]
    for eid, a, c, label, ref, _kw in edges:
        edges_md.append(f"| {eid} | {a} → {c} | {label} | `{ref}` |")
    edges_md.append("| e24 | vidom → plan | gate | `proof-layer/app/video_evidence.py:entitlements.evaluate` |")
    for eid, a, c, label, ref, _kw in failures:
        edges_md.append(f"| {eid} (failure path) | {a} → {c} | {label} | `{ref}` |")
    edges_md.append("| (PROPOSED) | real store notifications → purchase evidence | not built | — |")
    (OUT / "ARCHITECTURE_EDGES.md").write_text("\n".join(edges_md) + "\n", encoding="utf-8")
    return b.save()


# ---------------------------------------------------------------- Board B

BRANCHES = [
    # (scenario or None, status, observed, missing/conflicting, decision, allowed, forbidden, parent words, tests)
    ("paid_not_active", "VERIFIED_UNBOUND", "Store confirmed Memories (web order).", "Not on any camera.",
     "Connect it to this camera.", "connect, then verify", "selling the plan again (offer withheld)",
     "We found your plan… There's nothing more to pay.",
     ["test_paid_not_active_connects_without_a_new_purchase", "test_unconnected_purchase_withholds_the_offer_for_what_it_unlocks_only"]),
    ("plan_ended", "PLAN_EXPIRED", "Last confirmed period ended 5 days ago.", "No renewal from the store.",
     "Say it ended, and when. Wait for the store.", "check again, contact support",
     "a paywall on this screen; reconnecting the ended purchase (the feature page may offer a new plan: the period "
     "really ended)",
     "Memories on Nursery camera ended on Oct 6…", ["test_renewal", "test_an_ended_purchase_cannot_be_connected"]),
    ("renewal_reflected", "ACTIVE_FEATURE_USABLE", "Store renewal (version 2, new period).", "None.",
     "Write the new period to the same camera.", "verify", "a second connect or purchase",
     "Ready to use. Renewed · runs to …", ["test_renewal_of_a_connected_purchase_keeps_the_camera_active", "test_a_renewal_delivered_twice_adds_one_period"]),
    ("incompatible_hardware", "ACTIVE_HARDWARE_BLOCKED", "Plan active; camera on a Flex Stand.", "None: the mount is the limit.",
     "Explain the mount requirement.", "mount guide, verify", "offering a plan for a hardware problem",
     "…A different plan wouldn't change this.", ["test_active_plan_on_wrong_mount_is_not_a_billing_problem"]),
    ("purchase_uncertain", "PURCHASE_PENDING", "Google Play: pending.", "Final store answer.",
     "Wait; don't guess.", "check again", "granting access; selling again",
     "Waiting for the store to confirm… nothing more to buy.", ["test_pending_evidence_waits_and_never_offers_a_purchase"]),
    ("paid_wrong_camera", "ALREADY_ASSIGNED", "Code redeemed on the Playroom camera.", "Which camera was intended.",
     "Keep it where it is; support can move it.", "contact support", "moving a plan between cameras automatically",
     "This plan is on another camera.", ["test_purchase_bound_to_another_camera_is_not_moved"]),
    ("access_lost_after_refund", "REFUNDED_OR_REVOKED", "Refund at a newer store version.", "None: the refund supersedes.",
     "Revoke through the claim history; keep the history.", "contact support", "honouring the older confirmation",
     "This plan was refunded… Your history is kept.", ["test_refund_after_activation_revokes_access_and_keeps_history", "test_refund_before_verification"]),
    ("billing_grace", "BILLING_GRACE", "Store: renewal payment failed, grace to day 11.", "Whether the retry succeeds.",
     "Keep access through the grace end.", "check again", "cutting access early; selling again",
     "Your payment needs attention… stays on through Oct 21.", ["test_apple_grace_keeps_access_until_the_grace_end"]),
    ("billing_retry", "BILLING_RETRY", "Google: account hold.", "Recovery.",
     "No access; keep the purchase on its camera.", "check again, contact support", "granting access; reconnect; resale",
     "Your renewal payment didn't go through… nothing more to buy.", ["test_billing_retry_without_grace_removes_access_but_keeps_the_camera", "test_a_plan_whose_payment_is_retrying_or_paused_is_never_sold_again"]),
    (None, "CONFLICTING_EVIDENCE", "Two answers at the same store version, or a contradiction after connect.", "Which answer is true.",
     "Don't guess; show the conflict above 'checking'.", "contact support", "connecting it; hiding it behind 'checking'",
     "We got two different answers.", ["test_same_version_contradiction_is_conflicting_and_cannot_be_connected", "test_a_store_contradiction_after_connect_is_not_hidden_behind_checking"]),
    (None, "409 PURCHASE_OWNED_ELSEWHERE", "The purchase is owned by another household.", "None.",
     "Refuse; reveal nothing.", "none", "binding it to this family's camera",
     "We couldn't find this purchase on your account.", ["test_cross_household_purchase_ownership", "test_another_familys_purchase_cannot_be_recorded_or_seen"]),
    (None, "409 STALE_ACCESS_VIEW", "The screen was built on an older state revision.", "What changed since.",
     "Refuse the write; return the current revision.", "reload, then act", "applying a decision made on old facts",
     "Your view of this family's plans is out of date.", ["test_stale_view_is_refused_with_the_current_revision"]),
]


GROUPS = [
    ("works", "ok", "Works, or can work without paying again",
     ("VERIFIED_UNBOUND", "ACTIVE_FEATURE_USABLE", "BILLING_GRACE")),
    ("explain", "note", "Explain, wait, or point to support (nothing changes automatically)",
     ("PLAN_EXPIRED", "ACTIVE_HARDWARE_BLOCKED", "PURCHASE_PENDING", "ALREADY_ASSIGNED")),
    ("refuse", "stop", "Refuse or revoke (the backend says no)",
     ("REFUNDED_OR_REVOKED", "BILLING_RETRY", "CONFLICTING_EVIDENCE", "409 PURCHASE_OWNED_ELSEWHERE",
      "409 STALE_ACCESS_VIEW")),
]


def board_decisions() -> Path:
    from app import scenarios   # noqa: E402  (the app's own scenario table)
    tests_text = "\n".join(p.read_text(encoding="utf-8") for p in (APP / "tests").rglob("test_*.py"))
    for scenario, status, *_rest, tests in BRANCHES:
        if scenario is not None:
            expected = scenarios.SCENARIOS[scenario]["expected"][scenarios.NURSERY]
            if expected != status:
                raise SystemExit(f"Board B says {scenario} → {status}; scenarios.py says {expected}")
        for t in tests:
            if f"def {t}(" not in tests_text:
                raise SystemExit(f"Board B names {t}, which isn't in proof-layer/tests")
    grouped = {s for _k, _c, _t, statuses in GROUPS for s in statuses}
    if grouped != {b[1] for b in BRANCHES}:
        raise SystemExit("Board B: every branch must sit in exactly one group")
    by_status = {b[1]: b for b in BRANCHES}

    b = Board("B-same-symptom-different-cause")
    b.text("title", 40, 20, "Same symptom, different cause, different safe outcome", size=30, width=2400)
    b.text("sub", 40, 64, "Each card is a Scenario Studio scenario or a tested case. Its status is what "
           "proof-layer/app/access.py returns; boards.py checks it against scenarios.py and the named tests.",
           size=15, color=MUTED, width=2600)
    b.box("root", 60, 110, 520, 70, "Parent: \"I paid for this. Why can't I use it?\"", "warn", size=20, align="center")
    w, h, gap, head_w = 440, 330, 20, 300
    y = 230
    for key, kind, title, statuses in GROUPS:
        row_w = head_w + 40 + len(statuses) * (w + gap)
        b.frame_label(f"f-{key}", 40, y, row_w, h + 70, "")
        b.box(f"g-{key}", 60, y + 35, head_w, h, title, kind, size=18)
        b.arrow(f"to-{key}", "root", f"g-{key}", "", color=MUTED,
                via=[(30, 180), (30, y + 35 + h / 2)])
        for j, status in enumerate(statuses):
            scenario, _s, observed, missing, decision, allowed, forbidden, words, tests = by_status[status]
            label = (f"{status}" + (f"\n(scenario: {scenario})" if scenario else "\n(tested case)") +
                     f"\nSeen: {observed}\nMissing or conflicting: {missing}\nDecision: {decision}\n"
                     f"Allowed: {allowed}\nForbidden: {forbidden}\nParent reads: \"{words}\"\nTests: "
                     + ", ".join(tests))
            b.box(f"c-{key}-{j}", 60 + head_w + 40 + j * (w + gap), y + 35, w, h, label, kind, size=13)
        y += h + 100
    return b.save()


# ---------------------------------------------------------------- Board C

FRAMES = [
    # (title, screenshot, crop (x0, y0, x1, y1) in the 1440-wide capture, caption)
    ("1. The parent's problem", "renewal-ended-1440x900.png", (230, 300, 1440, 880),
     "Memories ended five days ago. The date, what still works (Sleep), and no paywall."),
    ("2. The backend's diagnosis", "access-unbound-1440x900.png", (230, 300, 1440, 980),
     "Five checks in order; the first unresolved one is named: paid, but not on a camera."),
    ("3. The right intervention", "renewal-renewed-1440x900.png", (230, 300, 1440, 880),
     "The store's renewal reaches the same camera. No new purchase, no new connect."),
    ("4. Eligibility checked, then evidence", "recorded-1440x900.png", (230, 340, 1440, 1060),
     "Video history verified on this camera; the clip's measured interval, with what isn't known."),
    ("5. A safe refusal", "billing-retry-1440x900.png", (230, 300, 1440, 880),
     "Payment retrying: no access granted, nothing sold, reconnecting refused."),
    ("6. The proof", "renewal-xray-1440x900.png", (965, 100, 1440, 560),
     "X-Ray: claim periods and store evidence versions, from stored rows."),
    ("7. What Growth would measure", "growth-1440x900.png", (230, 1030, 1440, 1660),
     "The funnel from stored synthetic events, each event separate. Not a customer rate."),
]


def _crop_png(png_path: Path, box: tuple[int, int, int, int], width: int) -> bytes:
    """Crop a region of a real screenshot and scale it, with the project's own decoder."""
    import av
    import numpy as np
    with av.open(str(png_path)) as container:
        rgb = next(container.decode(video=0)).to_ndarray(format="rgb24")
    x0, y0, x1, y1 = box
    region = np.ascontiguousarray(rgb[y0:min(y1, rgb.shape[0]), x0:min(x1, rgb.shape[1])])
    frame = av.VideoFrame.from_ndarray(region, format="rgb24")
    h = int(region.shape[0] * width / region.shape[1]) // 2 * 2
    small = frame.reformat(width=width, height=h, format="rgb24")
    buf = io.BytesIO()
    out = av.open(buf, "w", format="image2pipe")
    s = out.add_stream("png")
    s.width, s.height, s.pix_fmt = small.width, small.height, "rgb24"
    for p in s.encode(small):
        out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return buf.getvalue()


def board_storyboard(shots: Path) -> Path:
    b = Board("C-demo-storyboard")
    b.text("title", 40, 20, "90-second demonstration storyboard (real screenshots, cropped)", size=30, width=2400)
    b.text("sub", 40, 64, "Captured by scripts/visual_qa.py from the running app at 1440×900. Synthetic family; "
           "simulated store events; the clip (step 4) is the staged sample or the local CC0 sample, never Nanit "
           "footage.", size=15, color=MUTED, width=2400)
    width, cols, gap = 760, 3, 40
    images = []
    for title, name, box, caption in FRAMES:
        path = shots / name
        if not path.is_file():
            raise SystemExit(f"Board C needs the real screenshot {path}; run scripts/visual_qa.py first")
        images.append((title, _crop_png(path, box, width), caption))
    y = 120
    for row in range(0, len(images), cols):
        chunk = images[row:row + cols]
        heights = [width * _png_size(png)[1] / _png_size(png)[0] for _t, png, _c in chunk]
        for j, (title, png, caption) in enumerate(chunk):
            i = row + j
            x = 40 + j * (width + gap)
            b.text(f"h{i}", x, y, title, size=22, width=width)
            h = b.image(f"img{i}", x, y + 40, width, png)
            b.box(f"cap{i}", x, y + 50 + h, width, 70, caption, "note", size=16)
        y += 40 + max(heights) + 70 + 70
    return b.save()


if __name__ == "__main__":
    shots = ROOT / "artifacts" / "visual-qa" / "access"
    print(board_architecture())
    print(board_decisions())
    print(board_storyboard(shots))
