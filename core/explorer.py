"""
Stage 3b — Explorer Agent: app discovery like a user, deterministic.
[Playwright: Chromium + aria_snapshot] perception
[Python: URL pattern + SHA256] canonical state hash => zero infinite loops
[networkx + pyvis] interactive App Map; [SQLite + JSON] persistence

Exploration policy (demo-safety): the crawler only follows GET navigations —
same-origin links and one safe search probe. Buttons/forms are RECORDED as
elements (they become Planner test fodder) but never clicked during crawl.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import config
from .contracts import AppMap, AppState, StateElement
from .sandbox import Sandbox

SIG_ROLES = ("link", "button", "textbox", "searchbox", "spinbutton", "combobox",
             "checkbox", "radio", "img", "heading")
PRICE_RE = re.compile(r"\$(\d{1,5})\.(\d{2})")
VOLATILE_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f-]{27,}|\b\d{10,}\b|CC-[0-9A-F]{8}"
    r"|\b\d{4}-\d{2}-\d{2}T[0-9:.Z+-]+\b", re.I)


def canonical_route(url: str) -> str:
    """/product/3 -> /product/* ; /search?q=neon -> /search ; strip noise."""
    p = urlparse(url)
    path = re.sub(r"/\d+(?=/|$)", "/*", p.path) or "/"
    return path.rstrip("/") or "/"


def state_signature(route: str, elements: list[dict], status: int) -> str:
    """SHA256 over URL pattern + stable element roles/names (headings/text with
    money/dynamic content excluded => collisions collapse, dynamic junk stripped)."""
    parts = [route, str(status)]
    for el in elements:
        nm = VOLATILE_RE.sub("#", el.get("name", "")).strip().lower()
        parts.append(f"{el.get('role','')}|{nm}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def parse_aria_snapshot(snap: str) -> list[dict]:
    """`- button "Add to Cart"` / `- textbox "Email"` / `- link "Cart"` …"""
    out: list[dict] = []
    for m in re.finditer(r'^\s*-\s*(\w+)(?:\s+"([^"]*)")?', snap, re.M):
        role, name = m.group(1), (m.group(2) or "").strip()
        if role in SIG_ROLES:
            out.append({"role": role, "name": name})
    return out


class Explorer:
    def __init__(self, sandbox: Sandbox, run_dir: Path, emit=lambda e: None) -> None:
        self.sbx = sandbox
        self.run_dir = run_dir
        self.emit = emit
        self.graph = None       # networkx DiGraph
        self.appmap = AppMap(app_url=sandbox.target_url, description="")
        self._seen_hash: dict[str, str] = {}   # state_hash -> state_id
        self._visited_urls: set[str] = set()
        self._link_status: dict[str, int] = {}

    # ---------------------------------------------------------------- main
    def explore(self, description: str) -> AppMap:
        import networkx as nx
        self.graph = nx.DiGraph()
        self.appmap.description = description
        ctx = self.sbx.new_context(with_auth=True, guard="explorer")
        page = ctx.new_page()

        start = self.sbx.target_url + "/"
        guest_links = self._guest_nav_links()   # /login, /signup when logged in
        frontier: deque[tuple[str, int]] = deque([(start, 0)])
        for gl in guest_links:
            frontier.append((gl, 0))
        actions = 0

        while frontier and actions < config.MAX_CRAWL_ACTIONS \
                and len(self.appmap.states) < config.MAX_STATES:
            url, depth = frontier.popleft()
            if url in self._visited_urls or not self._in_scope(url):
                continue
            self._visited_urls.add(url)
            state, links, has_search = self._visit(page, url)
            actions += 1
            if state is None:
                continue
            # enqueue discovered links (GET-only navigation)
            if depth < 3:
                for href, lname in links:
                    nxt = urljoin(url, href)
                    if self._in_scope(nxt) and nxt not in self._visited_urls:
                        frontier.append((nxt.split("#")[0], depth + 1))
                        self.appmap.edges.append(
                            {"from": state.state_id, "to": canonical_route(nxt),
                             "via": f'link "{lname}"'})
            # safe search probe discovers /search state
            if has_search:
                probe = self.sbx.target_url + "/search?q=" + config.SEARCH_PROBE
                if self._in_scope(probe) and probe not in self._visited_urls:
                    frontier.append((probe, depth + 1))
                    self.appmap.edges.append(
                        {"from": state.state_id, "to": "/search",
                         "via": 'searchbox "Search"'})
        ctx.close()
        self._populate_cart_probe()     # elements that only exist with cart items
        self.sbx.seed_target()          # probe mutated the world — reset it
        self._persist()
        self._render_map()
        return self.appmap

    # --------------------------------------------------- enrichment probes
    def _guest_nav_links(self) -> list[str]:
        """Probe: open home WITHOUT the saved login — surfaces links that only
        guests see (/login, /signup). Deterministic, GET-only."""
        out: list[str] = []
        try:
            ctx = self.sbx.new_context(with_auth=False, guard="explorer")
            page = ctx.new_page()
            page.goto(self.sbx.target_url + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(300)
            for href, _name in self._collect_links(page):
                if any(k in href.lower() for k in ("login", "signin", "sign-in",
                                                   "signup", "register")):
                    out.append(urljoin(self.sbx.target_url, href).split("#")[0])
            ctx.close()
        except Exception:
            pass
        return list(dict.fromkeys(out))

    def _populate_cart_probe(self) -> None:
        """Probe: add one item, then re-visit /cart and /checkout so their
        elements (Quantity spinbutton, Promo code, Pay Now…) enter the AppMap.
        Classic crawler enrichment — some states only exist after a mutation."""
        try:
            ctx = self.sbx.new_context(with_auth=True)
            page = ctx.new_page()
            page.goto(self.sbx.target_url + "/", wait_until="domcontentloaded")
            btn = page.get_by_role("button", name="Add to Cart")
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(600)
            self._visit(page, self.sbx.target_url + "/cart")
            self._visit(page, self.sbx.target_url + "/checkout")
            ctx.close()
        except Exception:
            pass

    # ------------------------------------------------------------- visit one
    def _visit(self, page, url: str):
        try:
            resp = page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(350)
        except Exception:
            return None, [], False
        self.sbx.dismiss_popups(page)
        status = resp.status if resp else 0
        try:
            snap = page.locator("body").aria_snapshot(timeout=2500)
        except Exception:
            snap = ""
        elements = parse_aria_snapshot(snap)
        route = canonical_route(page.url)
        shash = state_signature(route, elements, status)
        if shash in self._seen_hash and status == 200:
            return None, [], False  # already-visited identical state

        state_id = f"S{len(self.appmap.states):02d}"
        self._seen_hash[shash] = state_id

        state = AppState(
            state_id=state_id, route=route,
            url=page.url, state_hash=shash, http_status=status,
            elements=[StateElement(id=f"{state_id}e{i}", role=e["role"],
                                   name=e["name"])
                      for i, e in enumerate(elements)])
        self.appmap.states.append(state)
        if self.graph is not None:
            self.graph.add_node(state_id, route=route, status=status)

        if status == 200:
            self._harvest_prices(page)
        links = self._collect_links(page)
        has_search = any(e["role"] == "searchbox" for e in elements)
        self.emit({"type": "state", "state_id": state_id, "route": route,
                   "status": status, "elements": len(elements)})
        return state, links, has_search

    def _collect_links(self, page) -> list[tuple[str, str]]:
        try:
            raw = page.eval_on_selector_all(
                "a[href]", "els => els.map(a => [a.getAttribute('href'),"
                           " (a.innerText||a.getAttribute('aria-label')||'').trim()])")
        except Exception:
            raw = []
        out = []
        for href, name in raw:
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            low = href.lower()
            if any(p in low for p in config.NAV_BLOCKED_PATTERNS):
                continue
            out.append((href, name or urlparse(href).path or href))
        return out

    def _harvest_prices(self, page) -> None:
        """Catalog price ledger (first observation wins) — the deterministic
        Signal 6 oracle for price-mismatch bugs (catalog vs cart totals)."""
        from .prices import extract_pairs
        for name, cents in extract_pairs(page):
            self.appmap.catalog_prices.setdefault(name, cents)

    # ---------------------------------------------------------------- scope
    def _in_scope(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc and p.netloc != self.sbx.base_host:
            return False
        return not any(b in url.lower() for b in config.NAV_BLOCKED_PATTERNS)

    # ------------------------------------------------------------- persist
    def _persist(self) -> None:
        (self.run_dir / "AppMap.json").write_text(self.appmap.model_dump_json(indent=2))
        db = sqlite3.connect(self.run_dir / "codemap.sqlite")
        db.executescript(
            "CREATE TABLE IF NOT EXISTS states(state_id TEXT PRIMARY KEY, data TEXT);"
            "CREATE TABLE IF NOT EXISTS edges(id INTEGER PRIMARY KEY, data TEXT);")
        for s in self.appmap.states:
            db.execute("INSERT OR REPLACE INTO states VALUES (?,?)",
                       (s.state_id, s.model_dump_json()))
        for e in self.appmap.edges:
            db.execute("INSERT INTO edges(data) VALUES (?)", (json.dumps(e),))
        db.commit()
        db.close()

    def _render_map(self) -> None:
        """[networkx + pyvis] interactive territory map artifact."""
        try:
            from pyvis.network import Network
            net = Network(height="520px", width="100%", directed=True,
                          bgcolor="#f6f2e9", font_color="#22251f")
            for s in self.appmap.states:
                color = ("#b3261e" if s.http_status >= 400 else
                         "#2e5e3f" if s.route == "/" else "#c98a12")
                net.add_node(s.state_id,
                             label=f"{s.state_id}\n{s.route} ({s.http_status})",
                             color=color, shape="box")
            id_of = {s.route: s.state_id for s in self.appmap.states}
            for e in self.appmap.edges:
                dst = id_of.get(e["to"])
                if dst:
                    net.add_edge(e["from"], dst, title=e["via"])
            net.force_atlas_2based()
            net.write_html(str(self.run_dir / "map.html"), notebook=False)
        except Exception as exc:
            (self.run_dir / "map.html").write_text(
                f"<html><body>map render failed: {exc}</body></html>")
