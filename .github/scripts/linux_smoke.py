#!/usr/bin/env python3
"""Require a live AppFlowy process and a rendered window on an isolated X server."""

import argparse
from collections import Counter
import os
from pathlib import Path
import re
import signal
import subprocess
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11  # noqa: E402


FATAL = re.compile(
    r"cannot open shared object file|symbol lookup error|undefined symbol|"
    r"No provider of eglGetPlatformDisplay|"
    r"version .*(?:GLIBCXX|GLIBC|CXXABI)_[\d.]+.*not found",
    re.IGNORECASE,
)


def rendered_window(process_group: int, screenshot: Path) -> bool:
    search = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--name", "[Aa]pp[Ff]lowy"],
        capture_output=True, text=True, check=False,
    )
    display = Gdk.Display.get_default()
    for identifier in search.stdout.split():
        owner = subprocess.run(
            ["xdotool", "getwindowpid", identifier],
            capture_output=True, text=True, check=False,
        )
        try:
            if os.getpgid(int(owner.stdout)) != process_group:
                continue
        except (ValueError, ProcessLookupError):
            continue
        window = GdkX11.X11Window.foreign_new_for_display(display, int(identifier))
        if window is None or window.get_width() < 100 or window.get_height() < 100:
            continue
        pixels = Gdk.pixbuf_get_from_window(
            window, 0, 0, window.get_width(), window.get_height(),
        )
        if pixels is None:
            continue
        pixels.savev(str(screenshot), "png", [], [])
        # Ignore borders/title bars. A mapped but blank GTK surface is not
        # evidence that Flutter rendered; save the image for human inspection.
        width, height = pixels.get_width(), pixels.get_height()
        data, stride, channels = (
            pixels.get_pixels(), pixels.get_rowstride(), pixels.get_n_channels()
        )
        colors = Counter(
            data[y * stride + x * channels:y * stride + x * channels + 3]
            for y in range(height // 5, height * 4 // 5, 2)
            for x in range(width // 10, width * 9 // 10, 2)
        )
        if len(colors) > 2 and max(colors.values()) / sum(colors.values()) < 0.995:
            return True
    return False


def smoke(command: list[str], log: Path, startup_timeout: float, observe: float):
    if Gdk.Display.get_default() is None:
        raise RuntimeError("Run this check under xvfb-run (an X11 display is required)")
    log.parent.mkdir(parents=True, exist_ok=True)
    screenshot = log.with_suffix(".png")
    with log.open("w") as output:
        process = subprocess.Popen(
            command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            deadline = time.monotonic() + startup_timeout + observe
            ready_since = None
            while time.monotonic() < deadline:
                status = process.poll()
                if status is not None:
                    raise RuntimeError(f"Application exited before smoke check completed: {status}")
                match = FATAL.search(log.read_text(errors="replace"))
                if match:
                    raise RuntimeError(f"Application reported a loader/EGL failure: {match[0]}")
                rendered = rendered_window(process.pid, screenshot)
                now = time.monotonic()
                ready_since = (ready_since or now) if rendered else None
                if ready_since is not None and now - ready_since >= observe:
                    if process.poll() is not None:
                        raise RuntimeError("Application exited after drawing its window")
                    print(f"AppFlowy rendered a window and stayed alive for {observe:g}s")
                    return
                time.sleep(0.5)
            raise RuntimeError("No stable, rendered AppFlowy window before the startup deadline")
        finally:
            # Only this deliberate termination is success. Neither timeout's
            # exit status nor the absence of a particular log line can pass.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--observe", type=float, default=5)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.startup_timeout <= 0 or args.observe <= 0:
        parser.error("A command and positive timeouts are required")
    try:
        smoke(args.command, args.log, args.startup_timeout, args.observe)
    except (OSError, RuntimeError) as error:
        print(f"::error::{error}")
        if args.log.exists():
            print(args.log.read_text(errors="replace"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
