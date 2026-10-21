"""Capture REAL demo screenshots of the running Streamlit app (offline).

Part of the Demo Mode/portfolio release: it drives the ACTUAL
application (``streamlit run app/streamlit_app.py`` by the caller) in a
headless Chromium via the Chrome DevTools Protocol and saves genuine
screenshots of the demo flow into ``docs/assets/``. Nothing is staged
or mocked: the pages you get are the pages the product renders.

Usage:

    streamlit run app/streamlit_app.py --server.port 8741   # terminal 1
    python scripts/capture_demo_screenshots.py              # terminal 2

Requires Edge/Chrome (Windows ships Edge); uses only the standard CDP
APIs via ``httpx`` + ``websockets``.
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

APP_URL = "http://localhost:8741"
DEBUG_PORT = 9333
OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "assets"
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def launch_browser(profile_dir: Path) -> subprocess.Popen:
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return subprocess.Popen([
                path,
                "--headless=new", "--disable-gpu", "--no-first-run",
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={profile_dir}",
                "--window-size=1440,1200", "about:blank",
            ])
    raise SystemExit("no Edge/Chrome installation found")


def wait_for_endpoint(timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            version = httpx.get(
                f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=2
            )
            if version.status_code == 200:
                created = httpx.put(
                    f"http://127.0.0.1:{DEBUG_PORT}/json/new?{APP_URL}",
                    timeout=10,
                )
                return created.json()
        except Exception:
            time.sleep(0.5)
    raise SystemExit("browser CDP endpoint did not come up")


class Cdp:
    def __init__(self, ws_url: str) -> None:
        self._next_id = 0
        self.ws = None
        self.ws_url = ws_url

    async def __aenter__(self) -> "Cdp":
        self.ws = await websockets.connect(self.ws_url, max_size=64 * 1024 * 1024)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.ws.close()

    async def cmd(self, method: str, **params) -> dict:
        self._next_id += 1
        await self.ws.send(__import__("json").dumps(
            {"id": self._next_id, "method": method, "params": params}
        ))
        while True:
            message = __import__("json").loads(await self.ws.recv())
            if message.get("id") == self._next_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def js(self, expression: str) -> object:
        result = await self.cmd(
            "Runtime.evaluate",
            expression=expression, returnByValue=True, awaitPromise=True,
        )
        return result.get("result", {}).get("value")

    async def wait_for(self, text: str, timeout: float = 40.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.js(f"document.body.innerText.includes({text!r})"):
                return
            await asyncio.sleep(0.4)
        raise SystemExit(f"timeout waiting for page text {text!r}")

    async def click_button(self, label: str) -> None:
        found = await self.js(f"""
            (() => {{
                const buttons = [...document.querySelectorAll('button')];
                const hit = buttons.find(
                    b => b.textContent.trim() === {label!r}
                );
                if (!hit) return null;
                hit.scrollIntoView({{block: 'center'}});
                const r = hit.getBoundingClientRect();
                return [r.x + r.width / 2, r.y + r.height / 2];
            }})()
        """)
        if not found:
            raise SystemExit(f"button {label!r} not on page")
        x, y = found
        await self.cmd("Input.dispatchMouseEvent", type="mousePressed",
                       x=x, y=y, button="left", clickCount=1)
        await self.cmd("Input.dispatchMouseEvent", type="mouseReleased",
                       x=x, y=y, button="left", clickCount=1)

    async def click_option(self, option_text: str) -> None:
        """Click a Streamlit selectbox: open it, then pick the option."""
        found = await self.js("""
            (() => {
                const boxes = [...document.querySelectorAll(
                    '[data-testid="stSelectbox"]')];
                const hit = boxes.find(b =>
                    (b.querySelector('label') || {}).textContent?.includes(
                        'Inspect a ranked activity'));
                if (!hit) return null;
                const combo = hit.querySelector('[role="combobox"]') || hit;
                combo.scrollIntoView({block: 'center'});
                const r = combo.getBoundingClientRect();
                return [r.x + r.width / 2, r.y + r.height / 2];
            })()
        """)
        if not found:
            raise SystemExit("detail selectbox not found")
        x, y = found
        portal_open = False
        for _attempt in range(3):
            await self.cmd("Input.dispatchMouseEvent", type="mousePressed",
                           x=x, y=y, button="left", clickCount=1)
            await self.cmd("Input.dispatchMouseEvent", type="mouseReleased",
                           x=x, y=y, button="left", clickCount=1)
            for _poll in range(6):
                if await self.js(
                    "document.querySelectorAll('[role=\"option\"]').length > 0"
                ):
                    portal_open = True
                    break
                await asyncio.sleep(0.5)
            if portal_open:
                break
        if not portal_open:
            raise SystemExit("detail selectbox menu did not open")
        deadline = time.time() + 15.0
        option = None
        while time.time() < deadline and option is None:
            option = await self.js(f"""
                (() => {{
                    const options = [
                        ...document.querySelectorAll('[role="option"]')
                    ];
                    const hit = options.find(
                        o => o.textContent.includes({option_text!r}));
                    if (!hit) return null;
                    const r = hit.getBoundingClientRect();
                    return [r.x + r.width / 2, r.y + r.height / 2];
                }})()
            """)
            await asyncio.sleep(0.5)
        if not option:
            raise SystemExit(f"option {option_text!r} not offered")
        x, y = option
        await self.cmd("Input.dispatchMouseEvent", type="mousePressed",
                       x=x, y=y, button="left", clickCount=1)
        await self.cmd("Input.dispatchMouseEvent", type="mouseReleased",
                       x=x, y=y, button="left", clickCount=1)

    async def save(self, name: str) -> None:
        await asyncio.sleep(1.2)  # let charts/layout settle
        # Size the viewport to the whole document — captureBeyondViewport
        # is unreliable with device metrics overrides.
        height = (await self.js("document.body.scrollHeight")) or 2400
        await self.cmd("Emulation.setDeviceMetricsOverride", width=1440,
                       height=min(max(int(height) + 40, 1200), 20000),
                       deviceScaleFactor=1.5, mobile=False)
        await asyncio.sleep(0.6)
        data = await self.cmd("Page.captureScreenshot", format="png")
        await self.cmd("Emulation.setDeviceMetricsOverride", width=1440,
                       height=1200, deviceScaleFactor=1.5, mobile=False)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUT_DIR / name
        target.write_bytes(base64.b64decode(data["data"]))
        print("saved", target)


async def capture() -> None:
    target = wait_for_endpoint()
    async with Cdp(target["webSocketDebuggerUrl"]) as cdp:
        await cdp.cmd("Page.enable")
        await cdp.cmd("Runtime.enable")
        await cdp.cmd("Emulation.setDeviceMetricsOverride", width=1440,
                      height=1200, deviceScaleFactor=1.5, mobile=False)
        await cdp.wait_for("Load Demo Project")

        # 1. Project setup — source choice, nothing loaded yet.
        await cdp.save("demo_1_project_setup.png")

        # 2. Demo project loaded → planning period + candidates.
        await cdp.click_button("Load Demo Project")
        await cdp.wait_for("Production Line Commissioning")
        await cdp.wait_for("Apply planning period")
        await cdp.click_button("Apply planning period")
        await cdp.wait_for("Add an existing task")
        await cdp.save("demo_2_candidate_selection.png")

        # 3. Confirm and run → decision dashboard with ranking.
        await cdp.click_button("Confirm Planning Session")
        await cdp.wait_for("Run Decision Analysis")
        await cdp.click_button("Run Decision Analysis")
        await cdp.wait_for("Decision Dashboard")
        await asyncio.sleep(3.0)  # plotly chart render
        await cdp.save("demo_3_decision_dashboard.png")

        # 4. Task detail — pick a different ranked activity on purpose.
        await cdp.click_option("Line-speed load test")
        await cdp.wait_for("Criterion contributions")
        await cdp.save("demo_4_task_detail.png")


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pda-cdp-") as profile:
        browser = launch_browser(Path(profile))
        try:
            asyncio.run(capture())
        finally:
            browser.terminate()


if __name__ == "__main__":
    sys.exit(main())
