"""Own a physical worker's admission until it has actually finished."""

import threading


def start_admitted_worker(
    slots,
    action,
    *,
    thread_factory=threading.Thread,
    daemon=True,
    register=None,
    unregister=None,
):
    """Release known creation failures and retain ambiguous partial starts."""
    lock = threading.Lock()
    finished = False

    def release_once():
        nonlocal finished
        with lock:
            if not finished:
                finished = True
                slots.release()
                if unregister is not None:
                    unregister(worker)

    def run():
        try:
            action()
        finally:
            release_once()

    worker = None
    try:
        worker = thread_factory(target=run, daemon=daemon)
        if register is not None:
            register(worker)
    except BaseException:
        # Thread construction cannot create the native worker.
        release_once()
        raise
    try:
        worker.start()
    except BaseException as error:
        # CPython reports this RuntimeError when native creation failed. An
        # interrupted start may already have a physical worker. That worker
        # owns admission until its finally runs, even if start() raised.
        if (
            type(error) is RuntimeError
            and str(error) == "can't start new thread"
            and worker.ident is None
        ):
            release_once()
        raise
    return worker
