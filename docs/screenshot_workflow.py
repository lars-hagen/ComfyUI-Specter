#!/usr/bin/env python3
"""Take screenshots of ComfyUI workflows for documentation."""

import asyncio
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.async_api import Page, async_playwright
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

COMFY_URL = "http://localhost:8188/"
CONCURRENCY = 4

console = Console()


async def screenshot_workflow(
    page: Page, workflow_path: Path, progress: Progress, task_id: TaskID, output_path: Path | None = None
):
    """Load workflow in ComfyUI and take a screenshot."""
    if not workflow_path.exists():
        console.print(f"[red]✗[/] Workflow not found: {workflow_path.name}")
        return

    output = output_path or workflow_path.with_suffix(".jpg")
    workflow_json = workflow_path.read_text()

    await page.goto(COMFY_URL, wait_until="domcontentloaded")
    await page.wait_for_function("typeof app !== 'undefined' && app.graph")

    # Load workflow, dismiss dialogs, fit view, zoom out
    await page.evaluate(f"app.loadGraphData({workflow_json})")
    await page.keyboard.press("Escape")
    canvas = page.locator("canvas").first
    await canvas.click(force=True)
    await page.keyboard.press(".")
    await page.wait_for_timeout(150)
    for _ in range(5):
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(50)
    await page.wait_for_timeout(50)

    # Hide UI overlays and screenshot
    canvas_panel = page.locator(".graph-canvas-panel")
    await canvas_panel.locator(".pointer-events-auto").evaluate("el => el.style.display = 'none'")
    screenshot_bytes = await canvas_panel.screenshot(type="png")

    # Crop edges (asymmetric: less on left)
    img = Image.open(BytesIO(screenshot_bytes))
    w, h = img.size
    crop_left, crop_right, crop_y = 0.14, 0.18, 0.12
    cropped = img.crop((int(w * crop_left), int(h * crop_y), int(w * (1 - crop_right)), int(h * (1 - crop_y))))
    cropped.save(output, "JPEG", quality=100)

    progress.advance(task_id)
    console.print(f"[green]✓[/] {workflow_path.stem}")


async def worker(queue: asyncio.Queue, browser, progress: Progress, task_id: TaskID):
    """Worker that processes workflows from queue."""
    page = await browser.new_page(viewport={"width": 1920, "height": 1080})
    while True:
        try:
            workflow = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await screenshot_workflow(page, workflow, progress, task_id)
    await page.close()


async def main():
    if len(sys.argv) < 2:
        workflows_dir = Path(__file__).parent.parent / "example_workflows"
        workflows = sorted(workflows_dir.glob("*.json"))
    else:
        workflows = sorted(Path(p) for p in sys.argv[1:])

    if not workflows:
        console.print("[yellow]No workflows found[/]")
        return

    queue: asyncio.Queue = asyncio.Queue()
    for wf in workflows:
        queue.put_nowait(wf)

    console.print(f"[bold]Screenshotting {len(workflows)} workflows...[/]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Processing", total=len(workflows))

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            workers = [worker(queue, browser, progress, task_id) for _ in range(min(CONCURRENCY, len(workflows)))]
            await asyncio.gather(*workers)
            await browser.close()

    console.print(f"\n[bold green]Done![/] Saved {len(workflows)} screenshots")


if __name__ == "__main__":
    asyncio.run(main())
