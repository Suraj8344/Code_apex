"""
Stage 3b — Explorer Agent: app discovery like a user, deterministic.
[Playwright: Chromium + aria_snapshot] perception
[Python: URL pattern + SHA256] canonical state hash => zero infinite loops
[networkx + pyvis] interactive App Map; [SQLite + JSON] persistence

Exploration policy (demo-safety): the crawler follows observed same-origin
links and records visible controls. It does not invent application routes or
submit forms while mapping the target.
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
from .safety import is_safe_exploration_button

SIG_ROLES = ("link", "button", "textbox", "searchbox", "spinbutton", "combobox",
             "checkbox", "radio", "img", "heading")
PRICE_RE = re.compile(r"\$(\d{1,5})\.(\d{2})")
NON_PAGE_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif",
    ".ico", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
    ".rar", ".7z", ".mp3", ".mp4", ".webm", ".wav", ".css", ".js",
    ".mjs", ".json", ".xml", ".csv", ".txt", ".woff", ".woff2", ".ttf",
    ".eot", ".wasm", ".map",
}
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
    def __init__(self, sandbox: Sandbox, run_dir: Path, emit=lambda e: None,
                 llm=None) -> None:
        self.sbx = sandbox
        self.run_dir = run_dir
        self.emit = emit
        self.llm = llm
        self._ai_rank_calls = 0
        self.graph = None       # networkx DiGraph
        self.appmap = AppMap(app_url=sandbox.target_url, description="")
        self._seen_hash: dict[str, str] = {}   # state_hash -> state_id
        self._visited_urls: set[str] = set()
        self._link_status: dict[str, int] = {}
        self._probed_buttons: set[tuple[str, str]] = set()
        self._safe_button_budget = 16

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
            state, links, _has_search = self._visit(page, url)
            actions += 1
            if state is None:
                continue
            if state.access_blocked_reason:
                self.emit({"type": "log", "msg": state.access_blocked_reason})
                # Challenge pages often link to documentation or challenge
                # internals. Treating those as the target's territory creates
                # misleading coverage and false-positive route findings.
                break
            # enqueue discovered links (GET-only navigation)
            if depth < 4:
                ordered_links = self._prioritize_links(state, links)
                # Follow the most promising observed navigation path first,
                # like a user exploring one section before returning to menus.
                for href, lname in reversed(ordered_links):
                    nxt = urljoin(url, href)
                    if self._in_scope(nxt) and nxt not in self._visited_urls:
                        frontier.appendleft((nxt.split("#")[0], depth + 1))
                        self.appmap.edges.append(
                            {"from": state.state_id, "to": canonical_route(nxt),
                             "via": f'link "{lname}"'})
            # Expand only clearly navigational/reveal controls, in their own
            # browser context. Form submitters and consequential actions are
            # never clicked by the explorer.
            for element in state.elements:
                if (element.role == "button" and element.enabled
                        and is_safe_exploration_button(element.name)
                        and self._safe_button_budget > 0
                        and (state.state_id, element.name) not in self._probed_buttons):
                    self._probe_safe_button(state, element, frontier, depth)
                    self._safe_button_budget -= 1
                    self._probed_buttons.add((state.state_id, element.name))
        ctx.close()
        self._persist()
        self._render_map()
        return self.appmap

    def _prioritize_links(self, state: AppState,
                          links: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Use the LLM only to rank actual visible links; never to invent URLs."""
        if not self.llm or not self.llm.available or self._ai_rank_calls >= 4 or len(links) < 2:
            return links
        self._ai_rank_calls += 1
        candidates = [{"index": i, "label": label[:100], "path": urlparse(urljoin(state.url, href)).path[:180]}
                      for i, (href, label) in enumerate(links[:60])]
        response = self.llm.call_json(
            f"explorer-rank#{self._ai_rank_calls}",
            'You assist a safe website QA explorer. Rank only the supplied visible same-site links by likely value to a real user following the supplied task. Prefer primary navigation, key service/workflow pages, and useful search/help pages. Deprioritize privacy, legal, policy, login, and low-value pages. Return JSON {"order":[integer indices]} with indices copied from candidates; never invent links.',
            json.dumps({"task": self.appmap.description, "current_path": state.route,
                        "visible_page_context": state.summary[:1100],
                        "visible_links": candidates}, ensure_ascii=False)[:8000])
        if not response or not isinstance(response.get("order"), list):
            return links
        order: list[int] = []
        for index in response["order"]:
            if isinstance(index, int) and 0 <= index < min(len(links), 60) and index not in order:
                order.append(index)
        order.extend(i for i in range(len(links)) if i not in order)
        return [links[i] for i in order]

    # --------------------------------------------------- guest view probe
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

    def _probe_safe_button(self, source: AppState, element: StateElement,
                           frontier: deque[tuple[str, int]], depth: int) -> None:
        """Click an observed low-risk disclosure/navigation button once."""
        ctx = None
        try:
            ctx = self.sbx.new_context(with_auth=True, guard="explorer")
            page = ctx.new_page()
            page.goto(source.url, wait_until="domcontentloaded")
            button = page.get_by_role("button", name=element.name, exact=True)
            if not button.count() or not button.first.is_visible():
                return
            # A button inside a form may submit even if its visible label looks
            # harmless. Do not click form controls during discovery.
            if button.first.evaluate(
                    "el => !!el.closest('form') || el.getAttribute('type') === 'submit'"):
                return
            button.first.click(timeout=1800)
            page.wait_for_timeout(250)
            discovered, links, _has_search = self._visit(page, page.url)
            destination = discovered.route if discovered else canonical_route(page.url)
            edge = {"from": source.state_id, "to": destination,
                    "via": f'button "{element.name}"'}
            if edge not in self.appmap.edges:
                self.appmap.edges.append(edge)
            if discovered and not discovered.access_blocked_reason and depth < 3:
                for href, label in links:
                    nxt = urljoin(page.url, href).split("#")[0]
                    if self._in_scope(nxt) and nxt not in self._visited_urls:
                        self.appmap.edges.append(
                            {"from": discovered.state_id,
                             "to": canonical_route(nxt),
                             "via": f'link "{label}"'})
                        frontier.append((nxt, depth + 1))
        except Exception:
            return
        finally:
            if ctx:
                ctx.close()

    # ------------------------------------------------------------- visit one
    def _visit(self, page, url: str):
        try:
            resp = page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(350)
        except Exception:
            return None, [], False
        # A same-origin link can point to a PDF, image, or other download.
        # Those are evidence/assets, not application pages or user journeys.
        content_type = (resp.headers.get("content-type", "").split(";", 1)[0].lower()
                        if resp else "")
        if content_type and content_type not in ("text/html", "application/xhtml+xml"):
            return None, [], False
        if not content_type and self._non_page_url(page.url):
            return None, [], False
        self.sbx.dismiss_popups(page)
        status = resp.status if resp else 0
        try:
            title = page.title()
            body_text = page.locator("body").inner_text(timeout=1500)[:12000]
        except Exception:
            title, body_text = "", ""
        from .access import access_block_reason
        blocked_reason = access_block_reason(status, title, body_text)
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
            url=page.url, title=title[:200],
            summary=" ".join([title] + [e["name"] for e in elements
                                       if e["role"] == "heading"] +
                              [re.sub(r"\s+", " ", body_text)[:700]])[:1100],
            state_hash=shash, http_status=status,
            access_blocked_reason=blocked_reason,
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
            absolute = urljoin(page.url, href)
            if not self._in_scope(absolute) or self._non_page_url(absolute):
                continue
            if any(p in low for p in config.NAV_BLOCKED_PATTERNS):
                continue
            out.append((href, name or urlparse(href).path or href))
        return out

    @staticmethod
    def _non_page_url(url: str) -> bool:
        path = urlparse(url).path.lower()
        return Path(path).suffix in NON_PAGE_SUFFIXES

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
        (self.run_dir / "AppMap.json").write_text(
            self.appmap.model_dump_json(indent=2), encoding="utf-8")
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
