#!/usr/bin/env python3
"""DOM-focused provider onboarding - capture selectors, POST bodies, localStorage."""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from specter.core.browser import VIEWPORT, close_browser, create_browser, parse_cookies

# JS to extract all interactive elements with selector candidates
EXTRACT_SELECTORS_JS = """() => {
    const results = [];
    const seen = new Set();

    // Find interactive elements
    document.querySelectorAll(
        'input, textarea, button, [contenteditable="true"], [role="button"], ' +
        '[role="textbox"], [data-testid], [aria-label]'
    ).forEach(el => {
        if (!el.offsetParent && el.tagName !== 'INPUT') return; // Skip hidden

        const selectors = [];
        const tag = el.tagName.toLowerCase();

        // Priority order for selectors
        if (el.dataset.testid) {
            selectors.push({s: `[data-testid="${el.dataset.testid}"]`, c: 'HIGH'});
        }
        if (el.getAttribute('aria-label')) {
            selectors.push({s: `[aria-label="${el.getAttribute('aria-label')}"]`, c: 'HIGH'});
        }
        if (el.id && !el.id.includes(':')) {
            selectors.push({s: `#${el.id}`, c: 'MED'});
        }
        if (el.placeholder) {
            selectors.push({s: `${tag}[placeholder*="${el.placeholder.slice(0,30)}"]`, c: 'MED'});
        }
        if (el.getAttribute('contenteditable')) {
            selectors.push({s: `${tag}[contenteditable="true"]`, c: 'LOW'});
        }
        if (el.name) {
            selectors.push({s: `${tag}[name="${el.name}"]`, c: 'LOW'});
        }

        // Skip if no good selectors
        if (!selectors.length) return;

        // Dedupe
        const key = selectors[0].s;
        if (seen.has(key)) return;
        seen.add(key);

        // Get text hint
        let text = (el.textContent || el.value || el.placeholder || '').trim().slice(0, 40);

        results.push({
            tag,
            type: el.type || null,
            selectors,
            text: text || null,
            role: el.getAttribute('role'),
        });
    });

    return results;
}"""

# JS to track clicks with parent chain
CLICK_TRACKER_JS = """() => {
    window.__specter_clicks = window.__specter_clicks || [];
    document.addEventListener('click', (e) => {
        const path = [];
        let el = e.target;
        for (let i = 0; i < 4 && el && el !== document.body; i++) {
            const info = {tag: el.tagName.toLowerCase()};
            if (el.dataset.testid) info.testid = el.dataset.testid;
            if (el.getAttribute('aria-label')) info.ariaLabel = el.getAttribute('aria-label');
            if (el.id) info.id = el.id;
            if (el.className && typeof el.className === 'string') info.class = el.className.split(' ')[0];
            path.push(info);
            el = el.parentElement;
        }
        window.__specter_clicks.push({time: Date.now(), path});
    }, true);
}"""


class Capture:
    """Holds captured data during onboarding session."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.states = []
        self.posts = []
        self.clicks = []
        self.localStorage_baseline = {}

    def add_state(self, name: str, selectors: list, localStorage: dict, screenshot_path: str):
        # Compute localStorage diff from baseline
        ls_diff = {}
        for k, v in localStorage.items():
            if k not in self.localStorage_baseline or self.localStorage_baseline[k] != v:
                ls_diff[k] = v

        self.states.append({
            "name": name,
            "selectors": selectors,
            "localStorage": localStorage,
            "localStorage_diff": ls_diff,
            "screenshot": screenshot_path,
        })

        # Update baseline after first state
        if not self.localStorage_baseline:
            self.localStorage_baseline = localStorage.copy()

    def add_post(self, url: str, body: dict):
        # Extract URL pattern
        parsed = urlparse(url)
        pattern = parsed.path
        self.posts.append({"url": url, "pattern": pattern, "body": body})

    def generate_report(self, url: str):
        """Generate markdown report optimized for Claude."""
        domain = urlparse(url).netloc
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        md = f"""# Provider Onboard: {domain}
**URL:** {url}
**Captured:** {ts}

---

"""
        # States with selectors
        for state in self.states:
            md += f"## {state['name']}\n"
            md += f"![{state['name']}]({Path(state['screenshot']).name})\n\n"

            md += "### Selectors\n"
            md += "| Type | Selector | Confidence | Hint |\n"
            md += "|------|----------|------------|------|\n"

            for el in state["selectors"]:
                if not el["selectors"]:
                    continue
                best = el["selectors"][0]
                hint = el["text"] or el["role"] or el["type"] or ""
                md += f"| {el['tag']} | `{best['s']}` | {best['c']} | {hint[:30]} |\n"

            if state["localStorage_diff"]:
                md += "\n### localStorage changes\n```json\n"
                md += json.dumps(state["localStorage_diff"], indent=2)[:500]
                md += "\n```\n"

            md += "\n"

        # POST requests
        if self.posts:
            md += "## POST Requests (injection points)\n\n"
            seen_patterns = set()
            for post in self.posts:
                if post["pattern"] in seen_patterns:
                    continue
                seen_patterns.add(post["pattern"])

                md += f"### `{post['pattern']}`\n"
                md += f"Full URL: `{post['url'][:100]}`\n"
                md += "```json\n"
                md += json.dumps(post["body"], indent=2)[:1500]
                md += "\n```\n\n"

        # Clicks
        if self.clicks:
            md += "## Click Events\n"
            for click in self.clicks[-10:]:  # Last 10
                path_str = " > ".join(
                    c.get("testid") or c.get("ariaLabel") or c.get("id") or c["tag"]
                    for c in click["path"]
                )
                md += f"- `{path_str}`\n"
            md += "\n"

        # Final localStorage
        if self.states:
            final_ls = self.states[-1]["localStorage"]
            interesting = {k: v for k, v in final_ls.items()
                          if any(x in k.lower() for x in ["model", "theme", "mode", "setting", "config", "user"])}
            if interesting:
                md += "## Interesting localStorage keys\n```json\n"
                md += json.dumps(interesting, indent=2)[:1000]
                md += "\n```\n"

        # Selector tips
        md += """
---

## Selector Tips

### Framework Detection
- **Angular**: Custom element tags like `<my-component>` - use `my-component` NOT `.my-component`
- **React**: Look for `data-testid`, `data-message-*` attributes
- **Quill/Rich editors**: `rich-textarea .ql-editor[contenteditable='true']`

### Language-Agnostic Selectors (IMPORTANT!)
- AVOID: `[aria-label="Enter a prompt"]` - gets localized!
- PREFER: `[contenteditable='true']`, `[role='textbox']`, `.ql-editor`, `rich-textarea`
- PREFER: `[data-testid='...']`, custom element tags, structural selectors

### Racing Multiple States
Use `.or_()` to handle different UI paths without delays:
```python
prompt_input = page.locator("rich-textarea .ql-editor[contenteditable='true']")
welcome_btn = page.locator(".welcome-button")
dialog_accept = page.locator("dialog-component mat-dialog-actions button").last

# Race all - whichever appears first wins
await prompt_input.or_(welcome_btn).or_(dialog_accept).wait_for(timeout=30000)

# Handle what appeared
if await welcome_btn.is_visible():
    await welcome_btn.click()
    await prompt_input.or_(dialog_accept).wait_for(timeout=30000)

if await dialog_accept.is_visible():
    await dialog_accept.click()  # .last = accept, .first = decline
    await prompt_input.wait_for(timeout=30000)
```

### Dialog Buttons
- `.first` = typically "Cancel" / "No thanks"
- `.last` = typically "Accept" / "Continue"

### File Upload Patterns (CRITICAL!)
Hidden buttons with `xapfileselectortrigger` are NOT file inputs:
```python
# WRONG - fails with "Node is not an HTMLInputElement"
await page.locator('[data-test-id="hidden-local-file-upload-button"]').set_input_files(files)

# RIGHT - find the actual <input type="file"> that appears after menu interaction
await page.locator('input[type="file"][name="Filedata"]').set_input_files(files)
```

File upload sequence pattern:
```python
# 1. Click prompt area first (may activate upload UI)
await prompt_input.click()

# 2. Open upload menu (use structural selector, NOT aria-label which is localized!)
await page.locator('uploader button').first.click()  # NOT get_by_role("button", name="...")

# 3. Click upload option in menu
await page.locator('[data-test-id="local-images-files-uploader-button"]').click()

# 4. Set files on the ACTUAL input element (appears dynamically)
await page.locator('input[type="file"][name="Filedata"]').set_input_files(file_paths)
```

### Gemini-Specific Reference
Tested patterns for gemini.google.com (Jan 2026):

**Uploader component structure:**
- `uploader button` index 0 = Main menu button (visible, triggers dropdown)
- `uploader button` index 1 = Hidden image upload trigger
- `uploader button` index 2 = Hidden file upload trigger

**Working selectors:**
- Prompt input: `rich-textarea .ql-editor[contenteditable='true']`
- Upload menu: `page.locator('uploader button').first` (language-agnostic!)
- Local files option: `[data-test-id="local-images-files-uploader-button"]`
- File input: `input[type="file"][name="Filedata"]`
- Response done: `model-response message-content [aria-busy="false"]`

**Gotchas:**
- `aria-label="Open upload file menu"` becomes `"Åbn menuen til filupload"` in Danish!
- Video uploads need ~2s processing time before sending prompt
- Model switcher: `bard-mode-switcher button.input-area-switch`
- Model options: `[data-test-id="bard-mode-option-{model}"]` where model is fast/thinking/pro

"""

        # Provider template hint
        md += f"""
## Provider Template

```python
# specter/providers/{domain.split('.')[0]}_chat.py

import asyncio
from ..core.browser import ProgressTracker, capture_preview, launch_browser, close_browser, log

async def chat_with_{domain.split('.')[0].replace('-','_')}(prompt: str, model: str = "default", pbar=None, preview: bool = False) -> str:
    progress = ProgressTracker(pbar, preview)
    progress.update(5)

    pw, context, page, _ = await launch_browser("{domain.split('.')[0]}")
    progress.update(10)

    try:
        await page.goto("{url}", wait_until="domcontentloaded")

        # Race possible UI states (welcome screens, dialogs, or ready state)
        prompt_input = page.locator('TEXTAREA_SELECTOR')  # Use language-agnostic!
        welcome_btn = page.locator('.welcome-button')
        dialog_btn = page.locator('dialog-tag mat-dialog-actions button').last
        await prompt_input.or_(welcome_btn).or_(dialog_btn).wait_for(timeout=30000)

        # Handle onboarding flows
        if await welcome_btn.is_visible():
            await welcome_btn.click()
            await prompt_input.or_(dialog_btn).wait_for(timeout=30000)
        if await dialog_btn.is_visible():
            await dialog_btn.click()
            await prompt_input.wait_for(timeout=30000)

        log("Connected", "●")
        progress.update(20)

        # Fill prompt FIRST
        await prompt_input.fill(prompt)

        # Set up request interception AFTER fill, BEFORE Enter
        # async def intercept(route):
        #     body = route.request.post_data
        #     await route.continue_(post_data=modified_body)
        # await page.route("**/api/endpoint*", intercept)

        log(f'Sending: "{{prompt[:60]}}..."', "✎")
        await page.keyboard.press('Enter')
        progress.update(40)

        result = await _wait_for_response(page, progress, preview)
        log(f"Success: {{len(result)}} chars", "★")
        return result

    finally:
        await close_browser(pw, context)


async def _wait_for_response(page, progress: ProgressTracker, preview: bool) -> str:
    await page.wait_for_selector('RESPONSE_SELECTOR', timeout=30000)
    progress.update(50)

    last_text, stable_count = "", 0
    start, last_preview = asyncio.get_event_loop().time(), 0

    for _ in range(60):
        await asyncio.sleep(2)
        elapsed = asyncio.get_event_loop().time() - start
        progress.update(50 + min(int(elapsed / 3), 40))

        if preview and elapsed - last_preview >= 3:
            progress.update(50 + min(int(elapsed / 3), 40), await capture_preview(page))
            last_preview = elapsed

        current_text = await page.locator('RESPONSE_SELECTOR').last.inner_text()
        if current_text == last_text and current_text:
            stable_count += 1
            if stable_count >= 2:
                if preview:
                    progress.update(95, await capture_preview(page))
                return current_text
        else:
            stable_count, last_text = 0, current_text

    return last_text
```
"""

        report_path = self.output_dir / "onboard_report.md"
        report_path.write_text(md)
        return report_path


async def onboard(url: str, output_dir: Path, cookies: list | None = None):
    """Run interactive onboarding session."""
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = Capture(output_dir)

    print(f"\n{'='*60}")
    print(f"SPECTER ONBOARD - {urlparse(url).netloc}")
    print(f"{'='*60}")
    print("\nControls:")
    print("  ENTER  = Capture single state")
    print("  r      = Record interval (5s) until ENTER - uses last clicked element")
    print("  q      = Quit and generate report")
    print(f"\nOutput: {output_dir}/")
    print("="*60)

    pw, browser, ctx, page = await create_browser(headed=True, viewport=VIEWPORT)

    # Add cookies if provided
    if cookies:
        await ctx.add_cookies(cookies)
        print(f"Loaded {len(cookies)} cookies")

    try:
        # Track POST requests
        async def on_request(request):
            if request.method == "POST":
                try:
                    body = request.post_data
                    if body and body.startswith("{"):
                        capture.add_post(request.url, json.loads(body))
                        short = request.url.split("/")[-1][:40]
                        print(f"  [POST] {short}")
                except:
                    pass

        page.on("request", on_request)

        # Navigate
        print(f"\nNavigating to {url}...")
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")

        # Install click tracker
        await page.evaluate(CLICK_TRACKER_JS)
        print("Ready. Interact with the page.\n")

        step = 0
        while True:
            cmd = input(f"[Step {step}] ENTER=capture, r=record, q=quit: ").strip().lower()
            if cmd == "q":
                break

            if cmd == "r":
                # Interval recording mode - use last clicked element as focus
                focus_selector = await page.evaluate("""() => {
                    const clicks = window.__specter_clicks || [];
                    if (!clicks.length) return null;
                    const last = clicks[clicks.length - 1].path[0];
                    if (last.testid) return `[data-testid="${last.testid}"]`;
                    if (last.ariaLabel) return `[aria-label="${last.ariaLabel}"]`;
                    if (last.id) return `#${last.id}`;
                    return null;
                }""")

                if focus_selector:
                    print(f"  Recording focused on: {focus_selector}")
                else:
                    print("  Recording full page (click element first to focus)")

                print("  Press ENTER to stop recording...")
                stop_event = asyncio.Event()
                substep_counter = [0]  # Mutable container for substep

                async def record_loop(cur_step=step, selector=focus_selector, stop=stop_event, counter=substep_counter):
                    while not stop.is_set():
                        ss_path = output_dir / f"step_{cur_step}_{counter[0]}.png"
                        await page.screenshot(path=str(ss_path))

                        # Capture focused element text if available
                        focus_text = None
                        if selector:
                            try:
                                focus_text = await page.locator(selector).first.inner_text(timeout=1000)
                                focus_text = focus_text[:200] if focus_text else None
                            except:
                                pass

                        localStorage = await page.evaluate("() => ({...localStorage})")
                        capture.add_state(
                            f"Step {cur_step}.{counter[0]}" + (f" [{selector}]" if selector else ""),
                            [{"tag": "focus", "selectors": [{"s": selector, "c": "FOCUS"}], "text": focus_text, "type": None, "role": None}] if focus_text else [],
                            localStorage,
                            str(ss_path),
                        )
                        print(f"    {cur_step}.{counter[0]}: {len(focus_text) if focus_text else 0} chars")
                        counter[0] += 1
                        await asyncio.sleep(5)

                task = asyncio.create_task(record_loop())
                await asyncio.get_event_loop().run_in_executor(None, input)  # Wait for ENTER
                stop_event.set()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                print(f"  Recorded {substep_counter[0]} snapshots")
                step += 1
                continue

            # Single capture
            ss_path = output_dir / f"step_{step}.png"
            await page.screenshot(path=str(ss_path))
            selectors = await page.evaluate(EXTRACT_SELECTORS_JS)
            localStorage = await page.evaluate("() => ({...localStorage})")

            clicks = await page.evaluate("() => window.__specter_clicks || []")
            await page.evaluate("() => window.__specter_clicks = []")
            capture.clicks.extend(clicks)

            capture.add_state(f"Step {step}", selectors, localStorage, str(ss_path))
            print(f"  Captured: {len(selectors)} elements, {len(localStorage)} localStorage keys")
            step += 1

    finally:
        await close_browser(pw, ctx, browser)

    # Generate report
    if capture.states:
        report = capture.generate_report(url)
        print(f"\n{'='*60}")
        print(f"Report saved: {report}")
        print(f"Screenshots: {output_dir}/step_*.png")
        print(f"{'='*60}\n")
    else:
        print("\nNo states captured.")


def main():
    parser = argparse.ArgumentParser(description="DOM-focused provider onboarding")
    parser.add_argument("url", help="URL to onboard")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("-c", "--cookies", help="Cookie file (JSON or Netscape TXT)")
    args = parser.parse_args()

    if args.output:
        output_dir = Path(args.output)
    else:
        domain = urlparse(args.url).netloc.replace(".", "_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(f"onboard_{domain}_{ts}")

    cookies = None
    if args.cookies:
        cookie_path = Path(args.cookies)
        if cookie_path.exists():
            cookies = parse_cookies(cookie_path.read_text())
            print(f"Parsed {len(cookies)} cookies from {args.cookies}")

    asyncio.run(onboard(args.url, output_dir, cookies))


if __name__ == "__main__":
    main()
