"""
Stage 2 — Sandbox initialization & safety guard.
1) Boot local app (subprocess + httpx health wait) when repo mode is used.
2) Safety guard: route blocking of off-domain/destructive requests,
   locator_handler that auto-dismisses cookie banners/modals.
3) Log in ONCE, persist Playwright storage_state for every later context.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from . import config


class Sandbox:
    def __init__(self, target_url: str, artifacts: Path) -> None:
        self.target_url = target_url.rstrip("/")
        self.base_host = urlparse(self.target_url).netloc
        self.artifacts = artifacts
        self.pw: Playwright | None = None
        self.browser: Browser | None = None
        self.auth_state_path = artifacts / "auth_state.json"
        self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._app_proc: subprocess.Popen | None = None

    # ---- 1) optional local-app boot --------------------------------------
    def boot_local_app(self, folder: str, command: str, port: int) -> None:
        """[Python: subprocess + httpx] start the local server, wait for HTTP 200."""
        self._app_proc = subprocess.Popen(
            command.split(), cwd=folder,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.wait_until_responsive(f"http://127.0.0.1:{port}/", timeout=60)

    @staticmethod
    def wait_until_responsive(url: str, timeout: int = 30) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = httpx.get(url, timeout=2)
                if r.status_code < 600:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"target never became responsive: {url}")

    def seed_target(self) -> bool:
        """QArena determinism hook: reset the world if the target offers /__seed__."""
        try:
            r = httpx.get(self.target_url + "/__seed__", timeout=4)
            return r.status_code == 200 and "seed" in r.text
        except Exception:
            return False

    # ---- 2) browser + safety guard ---------------------------------------
    def start_browser(self) -> Browser:
        self.pw = sync_playwright().start()
        try:
            # the bundled "headless shell" (playwright 1.49+) has a bug where
            # fresh contexts in a long-lived browser get net::ERR_FAILED on
            # form-POST navigations; the full chromium build doesn't
            self.browser = self.pw.chromium.launch(
                headless=config.HEADLESS, channel="chromium")
        except Exception:
            self.browser = self.pw.chromium.launch(headless=config.HEADLESS)
        return self.browser

    def new_context(self, with_auth: bool = True, tracing: bool = False,
                    video_dir: Path | None = None,
                    guard: str = "tester") -> BrowserContext:
        assert self.browser is not None
        kwargs: dict = {"viewport": config.VIEWPORT, "ignore_https_errors": True}
        if with_auth and self.auth_state_path.exists():
            kwargs["storage_state"] = str(self.auth_state_path)
        if video_dir:
            video_dir.mkdir(parents=True, exist_ok=True)
            kwargs["record_video_dir"] = str(video_dir)
        ctx = self.browser.new_context(**kwargs)
        ctx.set_default_timeout(config.STEP_TIMEOUT_MS)
        ctx.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        patterns = (config.NAV_BLOCKED_EXPLORER if guard == "explorer"
                    else config.NAV_BLOCKED_TESTER)
        self._attach_guard(ctx, patterns, read_only=(guard == "explorer"))
        if tracing:
            ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        return ctx

    def _attach_guard(self, ctx: BrowserContext, patterns,
                      read_only: bool = False) -> None:
        """Block off-domain/destructive paths and keep exploration read-only."""
        allowed = self.base_host

        def guard(route):
            url = route.request.url
            host = urlparse(url).netloc
            if host and host != allowed and not url.startswith("data:"):
                return route.abort()
            if read_only and route.request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
                return route.abort()
            low = url.lower()
            if any(p in low for p in patterns):
                return route.abort()
            return route.continue_()
        ctx.route("**/*", guard)

    @staticmethod
    def dismiss_popups(page) -> None:
        """[Playwright locator_handler] sweep common consent dialogs."""
        for label in ("Accept all", "Accept", "I agree", "Got it", "OK",
                      "No thanks", "Close"):
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=400)
            except Exception:
                pass

    # ---- 3) login once -> storage_state ----------------------------------
    def login_once(self, email: str, password: str, login_path_hint: str = "") -> bool:
        """[Playwright storage_state] authenticate once; every context reuses it."""
        if not email or not password:
            return False
        ctx = self.new_context(with_auth=False)
        page = ctx.new_page()
        try:
            candidates = [login_path_hint, "/login", "/signin", "/sign-in",
                          "/auth/login", "/account/login"]
            loaded = False
            for path in candidates:
                if not path:
                    continue
                try:
                    resp = page.goto(self.target_url + path)
                    if resp and resp.status < 400:
                        loaded = True
                        break
                except Exception:
                    continue
            if not loaded:
                return False
            self.dismiss_popups(page)
            self._fill_first(page, ["email", "Email", "E-mail",
                                    "username", "Username"], email)
            self._fill_first(page, ["password", "Password"], password)
            clicked = False
            for name in ("Log in", "Login", "Sign in", "Sign In", "Submit",
                         "Continue"):
                try:
                    btn = page.get_by_role("button", name=name)
                    if btn.count():
                        btn.first.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                page.keyboard.press("Enter")
            page.wait_for_timeout(1200)
            ctx.storage_state(path=str(self.auth_state_path))
            return True
        except Exception:
            return False
        finally:
            ctx.close()

    @staticmethod
    def _fill_first(page, names: list[str], value: str) -> None:
        for nm in names:
            try:
                el = page.get_by_label(nm)
                if el.count():
                    el.first.fill(value)
                    return
            except Exception:
                pass
        try:
            page.locator(f"input[name='{names[0]}']").first.fill(value)
        except Exception:
            pass

    # ---- teardown ----------------------------------------------------------
    def shutdown(self) -> None:
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        finally:
            if self._app_proc:
                self._app_proc.terminate()
                try:
                    self._app_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._app_proc.kill()
