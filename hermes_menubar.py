#!/usr/bin/env python3
"""HermesMenuBar — menu bar app for macOS.

Shows gateway + dashboard state in the menu bar and drives the launchd
service behind it. Status probes are deliberately cheap (launchctl list,
ps, a TCP connect) so the 3s refresh timer never stalls the Cocoa run
loop; anything slower runs on a worker thread.
"""
from __future__ import annotations

import os
import plistlib
import re
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path

import rumps

APP_NAME = "Hermes MenuBar"


def _claim_bundle_name(name: str) -> None:
    """Make macOS call this process *name* instead of "Python".

    A plain python process inherits the interpreter's CFBundleName, which
    surfaces in the menu bar, the app switcher and Force Quit. Patching the
    main bundle's info dictionary is the standard fix, and it only takes
    effect if it happens before NSApplication is instantiated — hence at
    import time, not inside App.__init__.
    """
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
    except Exception:
        pass  # cosmetic only — never block startup over a label


_claim_bundle_name(APP_NAME)

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_BIN = HERMES_HOME / "hermes-agent" / "venv" / "bin" / "hermes"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
DASHBOARD_PORT = int(os.environ.get("HERMES_DASHBOARD_PORT", "9119"))
REFRESH_SECONDS = 3
DASHBOARD_LOG = Path.home() / "Library" / "Logs" / "hermes-dashboard.log"

ICON_RUNNING = "🟢"
ICON_STOPPED = "🔴"
ICON_BUSY = "🟡"


# ── status probes ────────────────────────────────────────────────────────────

def launchd_label() -> str:
    """Find the launchd label whose plist targets this HERMES_HOME.

    The label carries a per-profile suffix (ai.hermes.gateway-<suffix>), so
    it is discovered from the installed plists rather than reconstructed.
    Falls back to the default label when nothing matches.
    """
    try:
        candidates = sorted(LAUNCH_AGENTS.glob("ai.hermes.gateway*.plist"))
    except OSError:
        return "ai.hermes.gateway"
    for plist in candidates:
        try:
            data = plistlib.loads(plist.read_bytes())
        except Exception:
            continue
        home = (data.get("EnvironmentVariables") or {}).get("HERMES_HOME")
        if home and Path(home) == HERMES_HOME:
            return data.get("Label") or plist.stem
    return candidates[0].stem if candidates else "ai.hermes.gateway"


def gateway_pid(label: str) -> int | None:
    """PID launchd currently supervises for *label*, or None when down."""
    try:
        out = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    m = re.search(r'"PID"\s*=\s*(\d+)', out.stdout)
    return int(m.group(1)) if m else None


def process_uptime(pid: int) -> str:
    """Human-readable uptime for *pid* (ps etime), or '' when unavailable."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    raw = out.stdout.strip()
    if not raw:
        return ""
    days, _, clock = raw.rpartition("-")
    parts = clock.split(":")
    if len(parts) == 3:
        h, m = int(parts[0]), int(parts[1])
    elif len(parts) == 2:
        h, m = 0, int(parts[0])
    else:
        return raw
    if days:
        return f"{int(days)}g {h}h"
    return f"{h}h {m}m" if h else f"{m}m"


def port_is_open(port: int) -> bool:
    """True when something accepts TCP on 127.0.0.1:*port*."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ── app ──────────────────────────────────────────────────────────────────────

class HermesMenuBar(rumps.App):
    def __init__(self) -> None:
        super().__init__(APP_NAME, title=ICON_BUSY, quit_button=None)
        self.label = launchd_label()
        self._busy = False
        self._dash_pending = False

        self.m_status = rumps.MenuItem("Gateway: …")
        self.m_pid = rumps.MenuItem("")
        self.m_toggle = rumps.MenuItem("Stop gateway", callback=self.on_toggle)
        self.m_restart = rumps.MenuItem("Restart gateway", callback=self.on_restart)
        self.m_dash_state = rumps.MenuItem("Dashboard: …")
        self.m_dash_toggle = rumps.MenuItem(
            "Avvia dashboard", callback=self.on_toggle_dashboard
        )
        self.m_dash_open = rumps.MenuItem(
            f"Apri nel browser :{DASHBOARD_PORT}", callback=self.on_open_dashboard
        )
        self.m_log = rumps.MenuItem("Log gateway", callback=self.on_log)
        self.m_errlog = rumps.MenuItem("Log errori", callback=self.on_errlog)

        self.menu = [
            self.m_status,
            self.m_pid,
            None,
            self.m_restart,
            self.m_toggle,
            None,
            self.m_dash_state,
            self.m_dash_toggle,
            self.m_dash_open,
            None,
            self.m_log,
            self.m_errlog,
            None,
            rumps.MenuItem("Esci", callback=rumps.quit_application),
        ]

        self.timer = rumps.Timer(self.refresh, REFRESH_SECONDS)
        self.timer.start()
        self.refresh(None)

        # The NSStatusItem does not exist until rumps has started the app,
        # so the tooltip is applied on the first tick rather than here.
        self._tooltip_timer = rumps.Timer(self._apply_tooltip, 1)
        self._tooltip_timer.start()

    def _apply_tooltip(self, timer) -> None:
        """Set the hover tooltip on the status item, then stop trying."""
        timer.stop()
        try:
            item = self._nsapp.nsstatusitem
            button = item.button() if hasattr(item, "button") else None
            (button or item).setToolTip_(APP_NAME)
        except Exception:
            pass  # cosmetic only

    # ── refresh ──────────────────────────────────────────────────────────

    def refresh(self, _) -> None:
        """Repaint the menu from live probes. Runs on the Cocoa main thread."""
        if self._busy:
            return
        pid = gateway_pid(self.label)
        if pid:
            up = process_uptime(pid)
            self.title = ICON_RUNNING
            self.m_status.title = f"Gateway: running ({up})" if up else "Gateway: running"
            self.m_pid.title = f"PID {pid}  ·  {self.label}"
            self.m_toggle.title = "Stop gateway"
        else:
            self.title = ICON_STOPPED
            self.m_status.title = "Gateway: stopped"
            self.m_pid.title = self.label
            self.m_toggle.title = "Start gateway"

        dash = port_is_open(DASHBOARD_PORT)
        if dash:
            self._dash_pending = False
        if self._dash_pending:
            self.m_dash_state.title = "Dashboard: starting…"
            self.m_dash_toggle.title = "Ferma dashboard"
        else:
            self.m_dash_state.title = f"Dashboard: {'running' if dash else 'stopped'}"
            self.m_dash_toggle.title = "Ferma dashboard" if dash else "Avvia dashboard"
        # Greyed out until there is actually something to open.
        self.m_dash_open.set_callback(self.on_open_dashboard if dash else None)

    # ── actions ──────────────────────────────────────────────────────────

    def _run_async(self, args: list[str], busy_title: str) -> None:
        """Run a slow hermes command off the main thread, keeping the UI live."""
        self._busy = True
        self.title = ICON_BUSY
        self.m_status.title = busy_title

        def worker() -> None:
            try:
                subprocess.run(args, capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                self._busy = False
        self._dash_pending = False

        threading.Thread(target=worker, daemon=True).start()

    def on_toggle(self, _) -> None:
        if gateway_pid(self.label):
            self._run_async([str(HERMES_BIN), "gateway", "stop"], "Gateway: stopping…")
        else:
            self._run_async([str(HERMES_BIN), "gateway", "start"], "Gateway: starting…")

    def on_restart(self, _) -> None:
        self._run_async(restart_command(self.label), "Gateway: restarting…")

    def on_toggle_dashboard(self, _) -> None:
        if port_is_open(DASHBOARD_PORT) or self._dash_pending:
            self._dash_pending = False
            self._run_async(
                [str(HERMES_BIN), "dashboard", "--stop"], "Dashboard: stopping…"
            )
        else:
            self.start_dashboard()

    def start_dashboard(self) -> None:
        """Launch the dashboard detached so it outlives this menu bar app.

        The web server runs in the foreground and never returns, so it must
        NOT go through _run_async (whose subprocess.run timeout would kill
        it). start_new_session detaches it into its own session; the first
        launch can take a while when the web UI still needs building, which
        is why the menu shows a pending state instead of nothing.
        """
        try:
            DASHBOARD_LOG.parent.mkdir(parents=True, exist_ok=True)
            log = open(DASHBOARD_LOG, "ab")
        except OSError:
            log = subprocess.DEVNULL
        try:
            subprocess.Popen(
                [str(HERMES_BIN), "dashboard", "--no-open", "--port", str(DASHBOARD_PORT)],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, cwd=str(HERMES_HOME),
            )
            self._dash_pending = True
        except OSError as exc:
            rumps.notification(APP_NAME, "Avvio dashboard fallito", str(exc))

    def on_open_dashboard(self, _) -> None:
        webbrowser.open(f"http://127.0.0.1:{DASHBOARD_PORT}")

    def on_log(self, _) -> None:
        open_in_console(HERMES_HOME / "logs" / "gateway.log")

    def on_errlog(self, _) -> None:
        open_in_console(HERMES_HOME / "logs" / "gateway.error.log")


def open_in_console(path: Path) -> None:
    """Open a log file in Console.app, where macOS tails it live."""
    if path.exists():
        subprocess.Popen(["open", "-a", "Console", str(path)])
    else:
        rumps.notification(APP_NAME, "Log non trovato", str(path))


# ─────────────────────────────────────────────────────────────────────────────
# DECISION POINT — how a restart should treat work in flight.
#
# Two valid strategies, and the right one depends on how you use Hermes:
#
#   graceful : [HERMES_BIN, "gateway", "restart"]
#       Hermes' own path. Drains the current turn, cleans up stale
#       processes across profiles, then comes back. Takes several seconds
#       because it boots a second Python interpreter to do it.
#
#   hard     : ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"]
#       Instant. SIGKILLs the process and lets KeepAlive respawn it. A
#       Telegram turn mid-flight is lost, and the 30s ThrottleInterval in
#       the plist can delay the respawn.
#
# TODO: implement the strategy you want. Default below is graceful.
# ─────────────────────────────────────────────────────────────────────────────

def restart_command(label: str) -> list[str]:
    """Return the argv used by the 'Restart gateway' menu item."""
    return [str(HERMES_BIN), "gateway", "restart"]


if __name__ == "__main__":
    HermesMenuBar().run()
