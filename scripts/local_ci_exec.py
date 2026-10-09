#!/usr/bin/env python3
"""Run one command for scripts/local-ci.sh.

It does three things the shell cannot do portably on macOS:

* restore Ctrl-C (SIGINT) for background runs, because a shell starts a background job with
  SIGINT ignored and every child inherits that, which breaks tests that send Ctrl-C to scripts;
* stop the command and everything it started when a time limit passes (macOS has no `timeout`);
* copy the command's output into a log file.

Usage:
    local_ci_exec.py --exec -- COMMAND...                       restore Ctrl-C, then become COMMAND
    local_ci_exec.py [--timeout SECONDS] [--log FILE] [--echo] [--interrupt-grace SECONDS] -- COMMAND...

After Ctrl-C or SIGTERM the signal goes to the command's whole process group. A command that is
still running --interrupt-grace seconds later (default 10) is stopped with SIGTERM, then SIGKILL.

Exit status: the command's own status; 124 when the time limit stopped it (like GNU timeout);
130 when this process was interrupted; 127 when the command cannot start. A command that dies
from a signal gives 128 plus the signal number.
"""

import argparse
import os
import selectors
import signal
import subprocess
import sys
import time

TIMED_OUT = 124
INTERRUPTED = 130
NOT_FOUND = 127
GRACE_SECONDS = 5.0
INTERRUPT_SECONDS = 10.0


def split_command(argv):
    if "--" not in argv:
        return argv, []
    index = argv.index("--")
    return argv[:index], argv[index + 1:]


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        # macOS answers a group that holds only finished processes with PermissionError.
        return False
    return True


def stop_group(child, grace=GRACE_SECONDS):
    """Ask the whole process group to stop, then force it after a short grace period."""

    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        child.poll()  # collect the leader so only running members keep the group alive
        if not group_alive(child.pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(command, timeout, log, echo, interrupt_grace=INTERRUPT_SECONDS):
    started = time.monotonic()
    log_file = open(log, "ab") if log else None

    def write(data):
        if log_file:
            log_file.write(data)
            log_file.flush()
        if echo:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    write(("$ %s\n# started %s\n" % (" ".join(command), time.strftime("%Y-%m-%d %H:%M:%S %z"))).encode())
    children = []

    def forward(number, _frame):
        if not received:
            first_signal.append(time.monotonic())
        received.append(number)
        for child in children:
            try:
                os.killpg(child.pid, number)
            except (ProcessLookupError, PermissionError):
                pass

    received = []
    first_signal = []
    # Install the handlers before the command starts. A handled signal is reset to the default
    # in a child, so the command gets a working Ctrl-C even when this process started with
    # SIGINT ignored (which is how a shell starts a background job).
    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    try:
        child = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        write(("# cannot start the command: %s\n" % error).encode())
        if log_file:
            log_file.close()
        return NOT_FOUND
    children.append(child)

    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    deadline = started + timeout if timeout else None
    timed_out = False
    open_pipe = True
    forced = False
    while True:
        if deadline and time.monotonic() > deadline:
            timed_out = True
            break
        if first_signal and not forced and child.poll() is None \
                and time.monotonic() - first_signal[0] > interrupt_grace:
            # The command did not stop after being told to. Do not wait for it forever.
            forced = True
            write(("# the command is still running %d seconds after the signal: stopping it\n"
                   % interrupt_grace).encode())
            stop_group(child)
        events = selector.select(0.25) if open_pipe else []
        for key, _ in events:
            chunk = os.read(key.fd, 65536)
            if chunk:
                write(chunk)
            else:
                selector.unregister(key.fileobj)
                open_pipe = False
        if child.poll() is not None and not events:
            break
        if not open_pipe:
            time.sleep(0.1)
    if timed_out:
        write(("# stopped: the %d second limit passed\n" % timeout).encode())
        stop_group(child)
        child.wait()
        code = TIMED_OUT
    else:
        code = child.wait()
        if code < 0:
            code = 128 - code
        # Anything the command left running in its own group would stay on the machine.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            write(b"# stopped processes the command left running\n")
        except (ProcessLookupError, PermissionError):
            pass
        if received:
            code = INTERRUPTED if code == 0 else code
    write(("# finished with status %d after %.1f seconds\n" % (code, time.monotonic() - started)).encode())
    if log_file:
        log_file.close()
    return code


def main(argv):
    own, command = split_command(argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exec", action="store_true", dest="replace")
    parser.add_argument("--timeout", type=float, default=0)
    parser.add_argument("--log")
    parser.add_argument("--echo", action="store_true")
    parser.add_argument("--interrupt-grace", type=float, default=INTERRUPT_SECONDS,
                        help="seconds a command may take to stop after Ctrl-C or SIGTERM")
    args = parser.parse_args(own)
    if not command:
        parser.error("a command is required after --")
    if args.replace:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            os.execvp(command[0], command)
        except OSError as error:
            print("cannot start %s: %s" % (command[0], error), file=sys.stderr)
            return NOT_FOUND
    return run(command, args.timeout, args.log, args.echo, args.interrupt_grace)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
