#!/usr/bin/env python3
"""Provider onboarding CLI - Capture browser automation data for new AI providers."""

import argparse
import asyncio
import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Add parent to path for imports when running directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from specter.core.browser import close_browser, create_browser

# =============================================================================
# CONSTANTS
# =============================================================================

# Text generation flow
TEXT_STEPS_WITH_LOGIN = [
    ("login", "Navigate and LOG IN if required"),
    ("prompt", "TYPE A TEXT PROMPT in the chat input"),
    ("response", "SEND and wait for TEXT RESPONSE to complete"),
]

TEXT_STEPS_NO_LOGIN = [
    ("prompt", "TYPE A TEXT PROMPT in the chat input"),
    ("response", "SEND and wait for TEXT RESPONSE to complete"),
]

# Image generation flow
IMAGE_STEPS_WITH_LOGIN = [
    ("login", "Navigate and LOG IN if required"),
    ("prompt", "TYPE AN IMAGE GENERATION PROMPT (e.g. 'create an image of a cat')"),
    ("generating", "SEND and wait for IMAGE PLACEHOLDERS to appear"),
    ("complete", "Wait for IMAGES TO FULLY LOAD (no more spinners)"),
]

IMAGE_STEPS_NO_LOGIN = [
    ("prompt", "TYPE AN IMAGE GENERATION PROMPT (e.g. 'create an image of a cat')"),
    ("generating", "SEND and wait for IMAGE PLACEHOLDERS to appear"),
    ("complete", "Wait for IMAGES TO FULLY LOAD (no more spinners)"),
]

# Video generation flow
VIDEO_STEPS_WITH_LOGIN = [
    ("login", "Navigate and LOG IN if required"),
    ("mode", "SELECT VIDEO MODE (click 'Image' dropdown → 'Video')"),
    ("prompt", "TYPE A VIDEO GENERATION PROMPT (e.g. 'a cat playing')"),
    ("generating", "SEND and wait for VIDEO to start generating"),
    ("complete", "Wait for VIDEO TO FULLY LOAD (progress bar complete)"),
]

VIDEO_STEPS_NO_LOGIN = [
    ("mode", "SELECT VIDEO MODE (click 'Image' dropdown → 'Video')"),
    ("prompt", "TYPE A VIDEO GENERATION PROMPT (e.g. 'a cat playing')"),
    ("generating", "SEND and wait for VIDEO to start generating"),
    ("complete", "Wait for VIDEO TO FULLY LOAD (progress bar complete)"),
]


def load_cookies_file(path: str) -> list[dict]:
    """Load and convert browser extension cookie export to Playwright format."""
    with open(path) as f:
        cookies = json.load(f)

    playwright_cookies = []
    for c in cookies:
        # Map sameSite values
        same_site_map = {
            "strict": "Strict",
            "lax": "Lax",
            "no_restriction": "None",
            "unspecified": "Lax",  # Default to Lax
        }
        same_site = same_site_map.get(c.get("sameSite", "").lower(), "Lax")

        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": same_site,
        }

        # Handle expiration
        if "expirationDate" in c:
            cookie["expires"] = c["expirationDate"]

        playwright_cookies.append(cookie)

    return playwright_cookies


def load_localstorage_file(path: str) -> dict:
    """Load localStorage export from Chrome DevTools.

    Format: JSON object with key-value pairs, or nested under "localStorage" key.
    """
    with open(path) as f:
        data = json.load(f)

    # Handle nested format from our comparison dump
    if "localStorage" in data and isinstance(data["localStorage"], dict):
        return data["localStorage"]

    # Direct key-value format
    return data


# Selectors to look for (ranked by stability)
SELECTOR_PATTERNS = {
    "data-testid": {"pattern": r'data-testid="([^"]+)"', "confidence": "HIGH", "rank": 1},
    "aria-label": {"pattern": r'aria-label="([^"]+)"', "confidence": "MED", "rank": 2},
    "id": {"pattern": r'\bid="([^"]+)"', "confidence": "MED", "rank": 3},
    "placeholder": {"pattern": r'placeholder="([^"]+)"', "confidence": "MED", "rank": 4},
}

# Keywords that indicate important elements
ELEMENT_KEYWORDS = {
    "input": ["input", "compose", "prompt", "message", "chat", "text"],
    "button": ["send", "submit", "post", "enter"],
    "response": ["response", "answer", "reply", "message", "assistant", "bot"],
    "login": ["login", "sign", "auth"],
}

# Network patterns to ignore
IGNORE_URL_PATTERNS = [
    r"\.js(\?|$)",
    r"\.css(\?|$)",
    r"\.woff",
    r"\.ttf",
    r"\.svg",
    r"\.ico",
    r"google-analytics",
    r"googletagmanager",
    r"facebook\.com",
    r"analytics",
    r"tracking",
    r"segment\.io",
]


# =============================================================================
# RECORDING ENGINE
# =============================================================================


class RecordingEngine:
    """Captures browser events during recording session."""

    def __init__(self, output_dir: Path, verbose: bool = True):
        self.output_dir = output_dir
        self.verbose = verbose
        self.network_entries = []
        self.events = []
        self.console_logs = []
        self.dom_snapshots = {}
        self.screenshots = {}
        self.image_count = 0

    def _log(self, msg: str, symbol: str = "  "):
        """Print log message if verbose."""
        if self.verbose:
            print(f"      {symbol} {msg}")

    def is_interesting_url(self, url: str) -> bool:
        """Check if URL is worth logging."""
        # Skip static assets
        for pattern in IGNORE_URL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return False
        return True

    async def on_request(self, request):
        """Handle request event."""
        url = request.url
        method = request.method

        if method in ("POST", "PUT", "PATCH"):
            entry = {
                "timestamp": datetime.now().isoformat(),
                "method": method,
                "url": url,
                "headers": dict(request.headers),
                "post_data": None,
            }
            try:
                entry["post_data"] = request.post_data
            except:
                pass
            self.events.append({"type": "request", **entry})

            # Log API calls
            if self.is_interesting_url(url):
                short_url = url.split("?")[0][-60:]
                self._log(f"{method} {short_url}", "→")

    async def on_response(self, response):
        """Handle response event - capture for HAR."""
        url = response.url
        method = response.request.method
        content_type = response.headers.get("content-type", "")

        # Always capture for HAR (for playback)
        entry = {
            "startedDateTime": datetime.now().isoformat(),
            "request": {
                "method": method,
                "url": url,
                "headers": [{"name": k, "value": v} for k, v in response.request.headers.items()],
            },
            "response": {
                "status": response.status,
                "headers": [{"name": k, "value": v} for k, v in response.headers.items()],
                "content": {"text": "", "encoding": "base64", "mimeType": content_type},
            },
        }

        # Capture response body for important requests
        body_size = 0
        try:
            if "json" in content_type or "image" in content_type or method in ("POST", "PUT"):
                body = await response.body()
                body_size = len(body)
                entry["response"]["content"]["text"] = base64.b64encode(body).decode()
                entry["response"]["content"]["size"] = body_size

                # Log and track image responses
                if "image" in content_type and body_size > 10000:
                    self.image_count += 1
                    size_kb = body_size // 1024
                    # Extract domain for logging
                    domain = urlparse(url).netloc
                    self._log(f"IMAGE #{self.image_count}: {size_kb}KB from {domain}", "◆")

                    self.events.append(
                        {
                            "type": "image_response",
                            "timestamp": datetime.now().isoformat(),
                            "url": url,
                            "size": body_size,
                            "content_type": content_type,
                        }
                    )
        except:
            pass

        # Log errors
        if response.status >= 400 and self.is_interesting_url(url):
            self._log(f"ERROR {response.status}: {url[:60]}", "✗")

        self.network_entries.append(entry)

    def on_console(self, msg):
        """Handle console messages."""
        if msg.type in ("error", "warning"):
            self.console_logs.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "type": msg.type,
                    "text": msg.text,
                }
            )

    async def capture_snapshot(self, page, step_name: str):
        """Capture DOM snapshot and screenshot for a step."""
        # Screenshot
        screenshot_path = self.output_dir / "raw_capture" / "screenshots" / f"{step_name}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=False)
        self.screenshots[step_name] = str(screenshot_path)

        # DOM snapshot (filtered)
        dom_path = self.output_dir / "raw_capture" / "dom_snapshots" / f"{step_name}.html"
        dom_path.parent.mkdir(parents=True, exist_ok=True)
        html = await page.content()
        dom_path.write_text(html)
        self.dom_snapshots[step_name] = str(dom_path)

    def save_har(self):
        """Save network capture as HAR file."""
        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "specter-onboard", "version": "1.0"},
                "entries": self.network_entries,
            }
        }
        har_path = self.output_dir / "raw_capture" / "network.har"
        har_path.parent.mkdir(parents=True, exist_ok=True)
        har_path.write_text(json.dumps(har, indent=2))

    def save_events(self):
        """Save events as JSONL."""
        events_path = self.output_dir / "raw_capture" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(events_path, "w") as f:
            for event in self.events:
                f.write(json.dumps(event) + "\n")

    def save_console(self):
        """Save console logs."""
        if self.console_logs:
            log_path = self.output_dir / "console.log"
            with open(log_path, "w") as f:
                for entry in self.console_logs:
                    f.write(f"[{entry['timestamp']}] [{entry['type']}] {entry['text']}\n")


# =============================================================================
# ANALYSIS ENGINE
# =============================================================================


class AnalysisEngine:
    """Analyzes captured data to extract selector candidates and patterns."""

    def __init__(self, recording: RecordingEngine):
        self.recording = recording
        self.selectors = {"input": [], "button": [], "response": [], "login": []}
        self.api_patterns = []
        self.image_patterns = []

    def analyze_dom(self, html: str, step_name: str):
        """Extract selector candidates from DOM."""
        # Find data-testid attributes
        for match in re.finditer(r'<([a-z]+)[^>]*data-testid="([^"]+)"[^>]*>', html, re.IGNORECASE):
            tag, testid = match.groups()
            selector = f'[data-testid="{testid}"]'
            self._classify_selector(selector, testid, "HIGH", tag, step_name)

        # Find aria-label attributes
        for match in re.finditer(r'<([a-z]+)[^>]*aria-label="([^"]+)"[^>]*>', html, re.IGNORECASE):
            tag, label = match.groups()
            selector = f'[aria-label="{label}"]'
            self._classify_selector(selector, label, "MED", tag, step_name)

        # Find textarea/input elements with placeholder
        for match in re.finditer(r'<(textarea|input)[^>]*placeholder="([^"]+)"[^>]*>', html, re.IGNORECASE):
            tag, placeholder = match.groups()
            selector = f'{tag}[placeholder*="{placeholder[:20]}"]'
            self._classify_selector(selector, placeholder, "MED", tag, step_name)

        # Find contenteditable elements
        for match in re.finditer(r'<([a-z]+)[^>]*contenteditable="true"[^>]*>', html, re.IGNORECASE):
            tag = match.group(1)
            self._add_selector("input", f'{tag}[contenteditable="true"]', "LOW", tag, step_name, "contenteditable")

    def _classify_selector(self, selector: str, text: str, confidence: str, tag: str, step: str):
        """Classify a selector into input/button/response/login."""
        text_lower = text.lower()

        for category, keywords in ELEMENT_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                self._add_selector(category, selector, confidence, tag, step, text)
                return

        # Fallback: classify by tag
        if tag in ("input", "textarea"):
            self._add_selector("input", selector, confidence, tag, step, text)
        elif tag == "button":
            self._add_selector("button", selector, confidence, tag, step, text)

    def _add_selector(self, category: str, selector: str, confidence: str, tag: str, step: str, notes: str):
        """Add selector to results if not duplicate."""
        for existing in self.selectors[category]:
            if existing["selector"] == selector:
                return
        self.selectors[category].append(
            {
                "selector": selector,
                "confidence": confidence,
                "tag": tag,
                "step": step,
                "notes": notes[:50],
            }
        )

    def analyze_network(self):
        """Extract API patterns from network capture."""
        for entry in self.recording.network_entries:
            url = entry["request"]["url"]
            method = entry["request"]["method"]

            if method in ("POST", "PUT", "PATCH"):
                # Try to parse request body
                post_data = None
                for event in self.recording.events:
                    if event.get("type") == "request" and event.get("url") == url:
                        post_data = event.get("post_data")
                        break

                pattern = {
                    "method": method,
                    "url_pattern": self._extract_url_pattern(url),
                    "sample_url": url,
                    "request_body_schema": self._infer_schema(post_data) if post_data else None,
                }
                self.api_patterns.append(pattern)

            # Track image responses
            content_type = ""
            for h in entry["response"]["headers"]:
                if h["name"].lower() == "content-type":
                    content_type = h["value"]
                    break

            if "image" in content_type:
                size = entry["response"]["content"].get("size", 0)
                if size > 50000:
                    self.image_patterns.append(
                        {
                            "url_pattern": self._extract_url_pattern(url),
                            "sample_url": url,
                            "size": size,
                        }
                    )

    def _extract_url_pattern(self, url: str) -> str:
        """Extract URL pattern (replace IDs with placeholders)."""
        parsed = urlparse(url)
        path = re.sub(r"/[a-f0-9-]{20,}", "/{id}", parsed.path)
        path = re.sub(r"/\d+", "/{id}", path)
        return f"{parsed.netloc}{path}"

    def _infer_schema(self, data: str):
        """Infer JSON schema from request body."""
        try:
            obj = json.loads(data)
            return self._schema_from_value(obj)
        except:
            return None

    def _schema_from_value(self, value, depth=0):
        """Recursively infer schema."""
        if depth > 3:
            return {"type": "..."}
        if isinstance(value, dict):
            return {k: self._schema_from_value(v, depth + 1) for k, v in list(value.items())[:10]}
        elif isinstance(value, list):
            return [self._schema_from_value(value[0], depth + 1)] if value else []
        elif isinstance(value, str):
            return "string"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, int):
            return "integer"
        elif isinstance(value, float):
            return "number"
        return "null"

    def generate_markdown(self, url: str, output_path: Path):
        """Generate Claude-optimized analysis markdown."""
        parsed = urlparse(url)
        domain = parsed.netloc

        md = f"""# Provider Analysis: {domain}
**URL:** {url}
**Recorded:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Detected Selectors

"""
        # Input selectors
        md += "### Prompt Input (textarea)\n"
        md += "| Confidence | Selector | Notes |\n"
        md += "|------------|----------|-------|\n"
        for s in sorted(self.selectors["input"], key=lambda x: {"HIGH": 0, "MED": 1, "LOW": 2}[x["confidence"]]):
            stars = {"HIGH": "⭐⭐⭐", "MED": "⭐⭐", "LOW": "⭐"}[s["confidence"]]
            md += f"| {stars} {s['confidence']} | `{s['selector']}` | {s['notes']} |\n"
        if not self.selectors["input"]:
            md += "| - | (none detected) | - |\n"

        # Button selectors
        md += "\n### Send Button\n"
        md += "| Confidence | Selector | Notes |\n"
        md += "|------------|----------|-------|\n"
        for s in sorted(self.selectors["button"], key=lambda x: {"HIGH": 0, "MED": 1, "LOW": 2}[x["confidence"]]):
            stars = {"HIGH": "⭐⭐⭐", "MED": "⭐⭐", "LOW": "⭐"}[s["confidence"]]
            md += f"| {stars} {s['confidence']} | `{s['selector']}` | {s['notes']} |\n"
        if not self.selectors["button"]:
            md += "| - | (none detected) | - |\n"

        # Response selectors
        md += "\n### Response Container\n"
        md += "| Confidence | Selector | Notes |\n"
        md += "|------------|----------|-------|\n"
        for s in sorted(self.selectors["response"], key=lambda x: {"HIGH": 0, "MED": 1, "LOW": 2}[x["confidence"]]):
            stars = {"HIGH": "⭐⭐⭐", "MED": "⭐⭐", "LOW": "⭐"}[s["confidence"]]
            md += f"| {stars} {s['confidence']} | `{s['selector']}` | {s['notes']} |\n"
        if not self.selectors["response"]:
            md += "| - | (none detected) | - |\n"

        # Login indicators
        md += "\n### Login Indicators (presence = NOT logged in)\n"
        for s in self.selectors["login"]:
            md += f"- `{s['selector']}`\n"
        if not self.selectors["login"]:
            md += "- (none detected)\n"

        # API patterns
        md += "\n## API Patterns\n"
        if self.api_patterns:
            for i, p in enumerate(self.api_patterns[:5]):
                md += f"\n### Endpoint {i + 1}\n"
                md += f"- **Method:** {p['method']}\n"
                md += f"- **URL Pattern:** `{p['url_pattern']}`\n"
                if p["request_body_schema"]:
                    md += (
                        f"- **Request Body Schema:**\n```json\n{json.dumps(p['request_body_schema'], indent=2)}\n```\n"
                    )
        else:
            md += "(No API patterns detected)\n"

        # Image URLs
        md += "\n## Image URL Patterns\n"
        if self.image_patterns:
            for p in self.image_patterns[:3]:
                md += f"- **Pattern:** `{p['url_pattern']}`\n"
                md += f"- **Sample Size:** {p['size'] // 1024}KB\n"
        else:
            md += "(No large images captured)\n"

        # Completion detection
        md += "\n## Completion Detection\n"
        md += "- **Strategy:** Text stability (response text unchanged for 3+ seconds)\n"
        md += "- **Alternative indicators:** Check screenshots for buttons/spinners\n"

        # Screenshots
        md += "\n## Screenshots\n"
        md += "See `raw_capture/screenshots/` for visual reference at each step.\n"

        output_path.write_text(md)

    def generate_selectors_json(self, output_path: Path):
        """Generate structured selectors JSON."""
        output_path.write_text(json.dumps(self.selectors, indent=2))

    def generate_provider_template(self, url: str, output_path: Path):
        """Generate a ChatService provider template from captured data."""
        parsed = urlparse(url)
        domain = parsed.netloc
        # Extract clean service name (e.g., "claude" from "claude.ai")
        service_name = domain.split(".")[0].replace("-", "_")
        class_name = "".join(word.title() for word in service_name.split("_")) + "Service"

        # Get best selectors (highest confidence)
        def get_best(category: str) -> str:
            candidates = sorted(
                self.selectors[category], key=lambda x: {"HIGH": 0, "MED": 1, "LOW": 2}[x["confidence"]]
            )
            return candidates[0]["selector"] if candidates else "TODO"

        textarea_sel = get_best("input")
        button_sel = get_best("button")
        response_sel = get_best("response")
        login_sels = [s["selector"] for s in self.selectors["login"][:3]]

        # Extract image URL patterns
        image_patterns = list({p["url_pattern"].split("/")[0] for p in self.image_patterns[:3]})

        # Extract API endpoint pattern
        api_pattern = "**/api/**"  # Default
        for p in self.api_patterns[:3]:
            if "conversation" in p["url_pattern"] or "chat" in p["url_pattern"]:
                pattern = p["url_pattern"]
                api_pattern = f"**/{pattern.split('/')[-1]}/**"
                break

        template = f'''"""
{class_name} - Browser automation for {domain}.

Generated by Specter onboard tool.
Review and customize before use.

Location: specter/providers/{service_name}/chat.py
"""

from ..base import ChatService, ServiceConfig
from ...core.browser import log


class {class_name}(ChatService):
    """{service_name.title()} implementation of ChatService."""

    config = ServiceConfig(
        service_name="{service_name}",
        base_url="{url}",
        selectors={{
            "textarea": '{textarea_sel}',
            "send_button": '{button_sel}',
            "response": '{response_sel}',
        }},
        login_selectors={login_sels!r},
        login_event="specter-{service_name}-login-required",
        image_url_patterns={image_patterns!r},
        image_min_size=50000,  # Adjust based on provider
        completion_selectors=[
            # TODO: Add completion indicators
            # '[aria-label="Download"]',
            # 'button[data-testid="response-complete"]',
        ],
        response_timeout=90,
    )

    def _get_intercept_pattern(self) -> str:
        return "{api_pattern}"

    def _is_api_response(self, url: str) -> bool:
        # TODO: Customize based on API patterns
        return "api/" in url or "conversation" in url

    def modify_request_body(self, body: dict, model: str, system_message: str, **kwargs) -> bool:
        """Modify request for model and system message."""
        modified = False

        # TODO: Implement model switching
        # if model and "model" in body and body["model"] != model:
        #     body["model"] = model
        #     modified = True

        # TODO: Implement system message injection
        # if system_message:
        #     body["system"] = system_message
        #     modified = True

        return modified

    async def extract_response_text(self) -> str:
        """Extract response text from the page."""
        try:
            response_el = self.page.locator(self.config.selectors["response"]).last
            if await response_el.count() > 0:
                return await response_el.inner_text(timeout=2000)
        except:
            pass
        return ""


# Singleton instance
_service = {class_name}()


async def chat_with_{service_name}(
    prompt: str,
    model: str = "default",
    image_path: str = None,
    system_message: str = None,
    pbar=None,
    preview: bool = False,
) -> tuple[str, bytes | None]:
    """Send message to {service_name.title()} and return response text + captured image.

    This is the public API that nodes use.
    """
    return await _service.chat(
        prompt=prompt,
        model=model,
        image_path=image_path,
        system_message=system_message,
        pbar=pbar,
        preview=preview,
    )
'''

        output_path.write_text(template)

        # Also generate __init__.py template
        init_template = f'''"""{service_name.title()} provider."""

from .chat import {class_name}, chat_with_{service_name}

__all__ = [
    "{class_name}",
    "chat_with_{service_name}",
]
'''
        init_path = output_path.parent / "provider_init.py"
        init_path.write_text(init_template)

        return service_name, class_name


# =============================================================================
# HAR PLAYBACK
# =============================================================================


async def browse_mode(
    url: str,
    cookies_file: str | None = None,
    localstorage_file: str | None = None,
    profile_dir: str | None = None,
    width: int = 767,
):
    """Open browser for manual investigation - no recording."""
    print(f"\n{'=' * 60}")
    print("SPECTER BROWSE MODE")
    print(f"URL: {url}")
    print(f"Viewport: {width}x900")
    if cookies_file:
        print(f"Cookies: {cookies_file}")
    if localstorage_file:
        print(f"localStorage: {localstorage_file}")
    if profile_dir:
        print(f"Profile: {profile_dir} (persistent)")
    print(f"{'=' * 60}")
    print("\nBrowser will open. Investigate freely.")
    print("Press ENTER in this terminal when done to close.\n")

    playwright, browser, ctx, page = await create_browser(
        headless=False,
        viewport={"width": width, "height": 900},
        profile_dir=profile_dir,
    )

    try:
        if cookies_file:
            cookies = load_cookies_file(cookies_file)
            await ctx.add_cookies(cookies)  # type: ignore[arg-type]
            print(f"Loaded {len(cookies)} cookies")

        # Load localStorage data (will inject after initial page load)
        localstorage_data = None
        if localstorage_file:
            localstorage_data = load_localstorage_file(localstorage_file)
            print(f"Will inject {len(localstorage_data)} localStorage keys")

        # Log requests/responses with HEADERS for debugging
        def on_request(request):
            url = request.url
            # Skip static assets
            if any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".woff", ".svg"]):
                return
            if request.method in ("POST", "PUT", "PATCH"):
                print(f"\n  → {request.method} {url[:100]}")
                # Show key headers
                headers = request.headers
                for key in ["cookie", "x-statsig-id", "authorization", "x-csrf-token"]:
                    if key in headers:
                        val = headers[key]
                        if key == "cookie":
                            # Just show cookie names
                            cookie_names = [c.split("=")[0] for c in val.split("; ")]
                            print(f"     {key}: {cookie_names}")
                        else:
                            print(f"     {key}: {val[:60]}...")
            elif request.method == "GET" and "rest/" in url:
                print(f"  → GET {url[:100]}")

        def on_response(response):
            if response.status >= 400:
                print(f"  ✗ {response.status} {response.url[:80]}")
                # Show error body for API errors
                if "rest/" in response.url or "api/" in response.url:

                    async def show_error():
                        try:
                            body = await response.text()
                            if body and len(body) < 500:
                                print(f"     Response: {body[:200]}")
                        except:
                            pass

                    asyncio.create_task(show_error())

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"Navigating to {url}...")
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            print("Page loaded (domcontentloaded)")

            # Inject localStorage if provided (must be done after page load, then refresh)
            if localstorage_data:
                print(f"Injecting {len(localstorage_data)} localStorage keys...")
                for key, value in localstorage_data.items():
                    # Value should be stored as-is (already JSON stringified from export)
                    await page.evaluate(f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
                print("Refreshing page to apply localStorage...")
                await page.reload(wait_until="domcontentloaded")
                print("Page reloaded")

            # Wait for JS to initialize (Statsig, etc.)
            print("Waiting for JS initialization (5s)...")
            await asyncio.sleep(5)

            # Check localStorage AND sessionStorage
            try:
                local_keys = await page.evaluate("Object.keys(localStorage)")
                session_keys = await page.evaluate("Object.keys(sessionStorage)")
                print(
                    f"\n  📦 localStorage ({len(local_keys)} keys): {local_keys[:5]}{'...' if len(local_keys) > 5 else ''}"
                )
                print(
                    f"  📦 sessionStorage ({len(session_keys)} keys): {session_keys[:5]}{'...' if len(session_keys) > 5 else ''}"
                )

                # Look for Statsig
                all_keys = local_keys + session_keys
                statsig_keys = [k for k in all_keys if "statsig" in k.lower()]
                if statsig_keys:
                    print(f"  ✓ Statsig keys: {statsig_keys}")
                    for key in statsig_keys[:3]:
                        val = await page.evaluate(f"localStorage.getItem('{key}') || sessionStorage.getItem('{key}')")
                        if val:
                            print(f"    {key}: {val[:80]}...")
                else:
                    print("  ⚠ NO Statsig keys found - this is likely the problem!")

                # Check for challenge state
                cf_keys = [k for k in all_keys if "cf" in k.lower() or "challenge" in k.lower()]
                if cf_keys:
                    print(f"  Cloudflare keys: {cf_keys}")

            except Exception as e:
                print(f"  Could not check localStorage: {e}")

            # Check cookies that were set
            try:
                cookies = await ctx.cookies()
                cf_cookies = [c["name"] for c in cookies if "cf" in c["name"].lower()]
                print(f"  CF cookies: {cf_cookies}")
            except Exception as e:
                print(f"  Could not check cookies: {e}")

        except Exception as e:
            print(f"Navigation issue: {e}")
            print("Browser still open - you can investigate.")

        # Dump state for comparison
        print("\n" + "=" * 60)
        print("BROWSER STATE DUMP (compare with Chrome DevTools)")
        print("=" * 60)

        try:
            # Get all cookies
            all_cookies = await ctx.cookies()
            grok_cookies = [c for c in all_cookies if "grok" in c.get("domain", "")]
            print(f"\n🍪 Cookies being sent ({len(grok_cookies)} for grok.com):")
            for c in grok_cookies:
                print(f"   {c['name']}: {c['value'][:40]}...")

            # Get localStorage
            local_data = await page.evaluate("""() => {
                const data = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    data[key] = localStorage.getItem(key);
                }
                return data;
            }""")
            print(f"\n💾 localStorage ({len(local_data)} items):")
            for k, v in list(local_data.items())[:10]:
                print(f"   {k}: {str(v)[:50]}...")

            # Get sessionStorage
            session_data = await page.evaluate("""() => {
                const data = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    data[key] = sessionStorage.getItem(key);
                }
                return data;
            }""")
            print(f"\n📋 sessionStorage ({len(session_data)} items):")
            for k, v in list(session_data.items())[:10]:
                print(f"   {k}: {str(v)[:50]}...")

            # Save to file for comparison
            dump_file = Path("camoufox_state_dump.json")
            dump_data = {
                "url": url,
                "cookies": {c["name"]: c["value"] for c in grok_cookies},
                "localStorage": local_data,
                "sessionStorage": session_data,
            }
            dump_file.write_text(json.dumps(dump_data, indent=2))
            print(f"\n📁 Saved to: {dump_file}")
            print("\nTo compare with Chrome, run in DevTools console:")
            print("   JSON.stringify({localStorage: {...localStorage}, sessionStorage: {...sessionStorage}}, null, 2)")

        except Exception as e:
            print(f"Could not dump state: {e}")

        # Wait for user to finish
        input("\nPress ENTER to close browser...")
    finally:
        await close_browser(playwright, ctx, browser)


async def replay_har(har_path: str, target_url: str | None = None):
    """Replay captured HAR file for testing."""
    print(f"\n{'=' * 60}")
    print("SPECTER HAR PLAYBACK")
    print(f"{'=' * 60}\n")

    with open(har_path) as f:
        har = json.load(f)

    # Build response map
    responses = {}
    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        responses[url] = entry["response"]

    print(f"Loaded {len(responses)} cached responses")

    # Determine target URL from HAR if not specified
    if not target_url and har["log"]["entries"]:
        first_url = har["log"]["entries"][0]["request"]["url"]
        parsed = urlparse(first_url)
        target_url = f"{parsed.scheme}://{parsed.netloc}"
        print(f"Target URL: {target_url}")

    playwright, browser, ctx, page = await create_browser(
        headless=False,
        viewport={"width": 1200, "height": 900},
    )

    try:
        served_count = [0]

        async def intercept(route):
            url = route.request.url
            if url in responses:
                resp = responses[url]
                headers = {h["name"]: h["value"] for h in resp["headers"]}
                body = b""
                if resp["content"].get("text"):
                    body = base64.b64decode(resp["content"]["text"])
                await route.fulfill(
                    status=resp["status"],
                    body=body,
                    headers=headers,
                )
                served_count[0] += 1
            else:
                await route.continue_()

        await page.route("**/*", intercept)
        if not target_url:
            raise ValueError("No target URL specified")
        await page.goto(target_url, timeout=60000)

        print(f"\nServed {served_count[0]} cached responses")
        print("Browser open - press ENTER to close...")
        input()
    finally:
        await close_browser(playwright, ctx, browser)


# =============================================================================
# INTERACTIVE RECORDING SESSION
# =============================================================================


async def record_provider(
    url: str,
    output_dir: Path,
    cookies_file: str | None = None,
    no_login: bool = False,
    mode: str = "text",
    profile_dir: str | None = None,
    width: int = 767,
):
    """Run interactive recording session."""
    print(f"\n{'=' * 60}")
    print("SPECTER PROVIDER ONBOARDING")
    print(f"Recording: {url}")
    print(f"Mode: {mode.upper()}")
    print(f"Viewport: {width}x900")
    if cookies_file:
        print(f"Cookies: {cookies_file}")
    if profile_dir:
        print(f"Profile: {profile_dir} (persistent)")
    if no_login:
        print("Login step: SKIPPED (using provided cookies/profile)")
    print(f"{'=' * 60}\n")

    # Select steps based on mode and login
    if mode == "image":
        steps = IMAGE_STEPS_NO_LOGIN if no_login else IMAGE_STEPS_WITH_LOGIN
    elif mode == "video":
        steps = VIDEO_STEPS_NO_LOGIN if no_login else VIDEO_STEPS_WITH_LOGIN
    else:
        steps = TEXT_STEPS_NO_LOGIN if no_login else TEXT_STEPS_WITH_LOGIN
    total_steps = len(steps) + 2  # +1 for launch, +1 for analysis

    output_dir.mkdir(parents=True, exist_ok=True)
    recording = RecordingEngine(output_dir)

    playwright, browser, ctx, page = await create_browser(
        headless=False,
        viewport={"width": width, "height": 900},
        profile_dir=profile_dir,
    )

    try:
        # Load cookies if provided
        if cookies_file:
            cookies = load_cookies_file(cookies_file)
            await ctx.add_cookies(cookies)  # type: ignore[arg-type]
            print(f"Loaded {len(cookies)} cookies from file")

        # Set up event handlers
        page.on("request", lambda r: asyncio.create_task(recording.on_request(r)))
        page.on("response", lambda r: asyncio.create_task(recording.on_response(r)))
        page.on("console", recording.on_console)

        # Navigate to URL
        print(f"[1/{total_steps}] Browser launching...")
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"Navigation warning: {e}")
        print("Page loaded")

        # Step through guided flow
        for i, (step_id, step_desc) in enumerate(steps, start=2):
            print(f"\n[{i}/{total_steps}] {step_desc}")
            print("      → Press ENTER when ready...")
            input()
            await recording.capture_snapshot(page, f"{i - 1:02d}_{step_id}")
            print(f"      📸 Screenshot: {i - 1:02d}_{step_id}.png")

        print(f"\n[{total_steps}/{total_steps}] Recording complete! Analyzing...\n")
    finally:
        await close_browser(playwright, ctx, browser)

    # Print capture summary
    print(f"{'=' * 60}")
    print("CAPTURE SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Network requests: {len(recording.network_entries)}")
    print(f"  Images captured:  {recording.image_count}")

    # Show image sources
    image_events = [e for e in recording.events if e.get("type") == "image_response"]
    if image_events:
        print("\n  Image sources:")
        seen_domains = set()
        for img in image_events:
            domain = urlparse(img["url"]).netloc
            if domain not in seen_domains:
                seen_domains.add(domain)
                size_kb = img["size"] // 1024
                print(f"    ◆ {domain} ({size_kb}KB)")
    else:
        print("\n  ⚠ No images captured - check if image generation completed")

    # Show API endpoints
    api_events = [e for e in recording.events if e.get("type") == "request"]
    if api_events:
        print(f"\n  API endpoints ({len(api_events)}):")
        for evt in api_events[:5]:
            short = evt["url"].split("?")[0][-50:]
            print(f"    → {evt['method']} ...{short}")
        if len(api_events) > 5:
            print(f"    ... and {len(api_events) - 5} more")

    print(f"{'=' * 60}\n")

    # Save raw data
    recording.save_har()
    recording.save_events()
    recording.save_console()

    # Run analysis
    analysis = AnalysisEngine(recording)
    for step_name, dom_path in recording.dom_snapshots.items():
        html = Path(dom_path).read_text()
        analysis.analyze_dom(html, step_name)
    analysis.analyze_network()

    # Generate outputs
    analysis.generate_markdown(url, output_dir / "claude_analysis.md")
    analysis.generate_selectors_json(output_dir / "selectors.json")

    # Generate provider template
    template_path = output_dir / "provider_template.py"
    service_name, _ = analysis.generate_provider_template(url, template_path)

    print("Output saved to:")
    print(f"  {output_dir}/claude_analysis.md    ← Share this with Claude")
    print(f"  {output_dir}/provider_template.py  ← chat.py skeleton")
    print(f"  {output_dir}/provider_init.py      ← __init__.py skeleton")
    print(f"  {output_dir}/raw_capture/          ← Full data archive")
    print()
    print("Next steps:")
    print(f"  1. mkdir specter/providers/{service_name}/")
    print(f"  2. cp provider_template.py → specter/providers/{service_name}/chat.py")
    print(f"  3. cp provider_init.py → specter/providers/{service_name}/__init__.py")
    print("  4. Customize selectors and API patterns in chat.py")
    print("  5. Update specter/providers/__init__.py to import new provider")
    print("  6. Add service config to specter/routes.py SERVICE_CONFIGS")
    print("  7. Add models to data/models.json")
    print("  8. Create nodes in specter/nodes.py")
    print()


# =============================================================================
# CLI ENTRY POINT
# =============================================================================


def main():
    parser = argparse.ArgumentParser(description="Specter Provider Onboarding - Capture browser automation data")
    parser.add_argument("url", nargs="?", help="URL of the AI provider to onboard")
    parser.add_argument("-o", "--output", default=None, help="Output directory")
    parser.add_argument("-c", "--cookies", metavar="FILE", help="Cookie file (browser extension export format)")
    parser.add_argument("-l", "--localstorage", metavar="FILE", help="localStorage JSON file (from Chrome DevTools)")
    parser.add_argument("-p", "--profile", metavar="DIR", help="Browser profile directory (persistent state)")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["text", "image", "video"],
        default="text",
        help="Recording mode: text (default), image, or video generation",
    )
    parser.add_argument(
        "-w", "--width", type=int, default=767, help="Viewport width (default: 767 for tablet breakpoint)"
    )
    parser.add_argument("--no-login", action="store_true", help="Skip login step (use when passing cookies/profile)")
    parser.add_argument("--browse", action="store_true", help="Browse mode - open browser for manual investigation")
    parser.add_argument("--replay", metavar="HAR_FILE", help="Replay a captured HAR file")

    args = parser.parse_args()

    if args.replay:
        asyncio.run(replay_har(args.replay, args.url))
    elif args.browse and args.url:
        asyncio.run(browse_mode(args.url, args.cookies, args.localstorage, args.profile, args.width))
    elif args.url:
        # Generate default output dir from URL
        if args.output:
            output_dir = Path(args.output)
        else:
            parsed = urlparse(args.url)
            domain = parsed.netloc.replace(".", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = Path(f"onboard_{domain}_{timestamp}")

        asyncio.run(
            record_provider(args.url, output_dir, args.cookies, args.no_login, args.mode, args.profile, args.width)
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
