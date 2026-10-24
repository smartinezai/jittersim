"""Owned, bounded processes for the release key-expiry fixture."""

import os
import pty
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def run_owned(command, *, terminal=False, timeout=15, **kwargs):
    """Keep any terminal and timed-out descendants inside this fixture's session."""

    check = kwargs.pop("check", False)
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    master = slave = None
    requested = command
    if terminal:
        master, slave = pty.openpty()
        kwargs["stdin"] = slave
        # Do not run Python callbacks after fork in a suite that also uses threads.
        command = [sys.executable, "-c",
                   "import fcntl, os, sys, termios; "
                   "fcntl.ioctl(0, termios.TIOCSCTTY, 0); os.execvp(sys.argv[1], sys.argv[1:])",
                   *command]
    else:
        kwargs["stdin"] = subprocess.DEVNULL
    kwargs["start_new_session"] = True
    try:
        with subprocess.Popen(command, **kwargs) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(requested, timeout, output=stdout, stderr=stderr)
            result = subprocess.CompletedProcess(requested, process.returncode, stdout, stderr)
            if check:
                result.check_returncode()
            return result
    finally:
        if slave is not None:
            os.close(slave)
            os.close(master)


@contextmanager
def isolated_agent():
    """Use a foreground fixture agent, never the owner's socket or agent PID."""

    environment = dict(os.environ)
    for name in ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "DISPLAY"):
        environment.pop(name, None)
    # Agent sockets have a short platform limit, unlike pytest's nested test paths.
    with tempfile.TemporaryDirectory(prefix="jt-expiry-", dir="/tmp") as directory:
        socket = Path(directory) / "agent"
        with subprocess.Popen(
            ["ssh-agent", "-D", "-a", str(socket)], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True,
        ) as agent:
            try:
                deadline = time.monotonic() + 5
                while not socket.exists():
                    if agent.poll() is not None:
                        _, error = agent.communicate(timeout=5)
                        raise RuntimeError("fixture ssh-agent did not start: " + error.decode()[:2000])
                    if time.monotonic() >= deadline:
                        raise TimeoutError("fixture ssh-agent did not create its socket")
                    time.sleep(0.01)
                yield dict(environment, SSH_AUTH_SOCK=str(socket), SSH_AGENT_PID=str(agent.pid))
            finally:
                if agent.poll() is None:
                    os.killpg(agent.pid, signal.SIGTERM)
                try:
                    agent.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(agent.pid, signal.SIGKILL)
                    agent.communicate()
