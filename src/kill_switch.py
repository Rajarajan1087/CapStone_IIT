"""
The kill switch: stop answering automatically, immediately, without a deploy.

Required by the Governance Framework. The framework's test for whether
governance is real is a good one -- pick a plausible way the system could
harm a customer and ask what in the design stops it. If the answer
describes a property of the model rather than a control that was built,
it is a hope rather than a control.

This is a control.

DESIGN
------
Three deliberate choices:

1. FILE-BASED, not configuration. A support manager at two in the morning
   cannot redeploy, cannot edit an environment variable on a running
   process, and should not need an engineer. Creating a file is something
   anyone can do, from any machine with access to the directory, in
   seconds.

2. CHECKED PER TICKET, not at startup. A switch consulted once when the
   process launches is useless -- the whole point is to stop a run that is
   already in progress. The check is a stat() call on a path, which costs
   microseconds against a pipeline already doing retrieval and generation.

3. FAILS SAFE. If the check itself errors -- permissions, a disappearing
   mount -- the switch is treated as ENGAGED. A control whose failure mode
   is "carry on answering customers" is worse than no control, because it
   creates confidence that is not warranted.

The switch does not stop the run. It forces every ticket to escalate to a
human, which keeps the unattended evaluation completing (A9) while
guaranteeing no automated reply reaches a customer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT

# The file whose mere existence disengages automatic answering. Named so
# that someone finding it in a directory listing understands immediately
# what it does.
DEFAULT_SWITCH_PATH = PROJECT_ROOT / "storage" / "KILL_SWITCH_ENGAGED"

# An environment variable is also honoured, for container deployments
# where writing a file into a running image is awkward.
ENV_VARIABLE = "SUPPORT_SYSTEM_KILL_SWITCH"


@dataclass(frozen=True)
class KillSwitchState:
    """Whether automatic answering is permitted, and why not if it is not."""

    engaged: bool
    reason: str = ""
    source: str = ""          # "file", "environment", or "check_failed"
    engaged_at: str | None = None

    @property
    def automatic_answering_allowed(self) -> bool:
        return not self.engaged


def check(switch_path: Path | None = None) -> KillSwitchState:
    """
    Read the current state. Never raises; fails safe if it cannot tell.

    Called once per ticket rather than once per run, because a switch that
    cannot stop work already in progress does not do the job it exists for.
    """
    path = switch_path or DEFAULT_SWITCH_PATH

    # Environment variable first: cheapest, and the container path.
    env_value = os.environ.get(ENV_VARIABLE, "").strip().lower()
    if env_value in ("1", "true", "yes", "on", "engaged"):
        return KillSwitchState(
            engaged=True,
            reason=(f"Automatic answering is disabled because the "
                    f"{ENV_VARIABLE} environment variable is set. Every "
                    f"ticket is being escalated to a person."),
            source="environment",
        )

    try:
        if path.exists():
            # The file's contents, if any, explain who engaged it and why.
            try:
                note = path.read_text(encoding="utf-8").strip()
            except OSError:
                note = ""
            engaged_at = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc).isoformat()
            return KillSwitchState(
                engaged=True,
                reason=(f"Automatic answering is disabled because the kill "
                        f"switch file is present at {path}. Every ticket is "
                        f"being escalated to a person."
                        + (f" Note recorded: {note}" if note else "")),
                source="file",
                engaged_at=engaged_at,
            )
    except OSError as exc:
        # Cannot determine the state. Assume the worst: a control whose
        # failure mode is "keep answering customers" is not a control.
        return KillSwitchState(
            engaged=True,
            reason=(f"Automatic answering is disabled because the kill switch "
                    f"state could not be read ({exc.__class__.__name__}). The "
                    f"system fails closed rather than assuming it is safe to "
                    f"continue."),
            source="check_failed",
        )

    return KillSwitchState(engaged=False)


def engage(note: str = "", switch_path: Path | None = None) -> Path:
    """
    Engage the switch. Takes effect on the very next ticket.

    Provided so the control can be exercised from a script or a test. In
    practice an operator creates the file directly, which is the point --
    the mechanism must not depend on this codebase being runnable.
    """
    path = switch_path or DEFAULT_SWITCH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    path.write_text(
        f"Engaged at {stamp}\n{note}\n"
        f"\nDelete this file to resume automatic answering.\n",
        encoding="utf-8",
    )
    return path


def release(switch_path: Path | None = None) -> bool:
    """Disengage. Returns True if a switch was actually engaged."""
    path = switch_path or DEFAULT_SWITCH_PATH
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError:
        pass
    return False
