"""Retain fixed log templates without exception text or string arguments."""

import logging
import math
import sys
import threading

from .diagnostic_records import send_fields

# Only fixed messages audited in the current source may enter the journal.
SAFE_MESSAGES = frozenset((
    "could not save completed operation history",
    "Unexpected service failure",
    "Request worker failed",
    'Ad permission could not be confirmed before household changes.',
    'DNS observation failed',
    'DNS watching stopped: %s',
    'Homepage helper socket unavailable. Control socket remains available.',
    'Homepage pairing listener unavailable. Dashboard controls remain available.',
    'Household rules applied but ad permission could not be renewed.',
    'Panic state was not readable. Household rules remain disabled.',
    'Pending Wi-Fi recovery failed. Repair jittersim-ap through management access.',
    'Profiles started but ad-blocking resume could not be confirmed.',
    'Saved access rules were not restored. The test network stays closed. Use Normal access to open it.',
    'These website names could not be looked up in time and are left out of the rules until their lookup works: %s',
    'Wi-Fi name reset failed: %s',
    'access point configuration failed',
    'active experiment stopped after a router gateway outage',
    'browser tunnel closed for %s',
    'browser tunnel rejected: %s',
    'configured router gateway is unreachable',
    'configured router gateway probe is unavailable',
    'control-plane request for %s failed',
    'control-plane response for %s failed',
    'could not build control-plane response for %s',
    'could not check which addresses saved devices use',
    'could not clean up the completed release upload',
    'could not clear the active plan after apply failure',
    'could not disable household restrictions',
    'could not inspect connected clients',
    'could not inspect the upstream connection',
    'could not persist control-plane request for %s',
    'could not prune randomized website history',
    'could not publish the active network plan',
    'could not read client names from the station snapshot',
    'could not read device labels for the activity log',
    'could not read group quota counter before reset',
    'could not read live traffic counters',
    'could not read progression traffic counters',
    'could not read queue statistics for the impact check',
    'could not read quota counter before reset',
    'could not read quota counters before an import',
    'could not read the gateway state',
    'could not record action history for %s',
    'could not record action history for the release upload',
    'could not record activity',
    'could not record scheduled network failure',
    'could not refresh access device addresses',
    'could not repeat an external network reset',
    'could not resolve %s',
    'could not save observed websites to a profile',
    'could not save pattern history',
    'could not save randomized website history',
    'could not save scheduled Panic cleanup',
    'could not scan DNS observations',
    'could not start the daily release check',
    'could not stop the unused filtered DNS service',
    'could not undo a scheduled apply that Panic overtook',
    'could not undo an apply that Panic overtook',
    'could not watch DNS answers: %s',
    'could not write the uploaded release',
    'dashboard client disconnected',
    'dashboard connection event',
    'dashboard request rejected or timed out',
    'dashboard response status=%s',
    'dashboard shutdown is incomplete, shared state remains owned',
    'dashboard worker startup failed',
    'device %s moved from %s to %s',
    'device %s now uses %s, which saved device %s keeps; not moving it',
    'gateway persistence setting did not reach the networking helper',
    'gateway persistence state could not be read',
    'gateway persistence state could not be saved',
    'gateway recovery state was unavailable during dashboard startup',
    'helper worker startup failed',
    'household cleanup failed after Panic state save failure',
    'household cleanup failed after stop state save failure',
    'jittersim dashboard listening on %s:%s',
    'network cleanup failed after plan publication error',
    'network helper apply failed: %s',
    'network helper did not answer, so network state was not recovered',
    'network helper health check failed',
    'network helper reset failed: %s',
    'network plan apply failed at step %d: %s; exit code %s; timed out %s; error output: %s; cleanup: %s',
    'network reset after a router outage failed',
    'network reset failed',
    'network state recovery did not complete successfully',
    'online release check could not be handled',
    'persistent Panic stop state could not be read',
    'persistent Panic stop state could not be saved',
    'persistent access apply failed',
    'persistent access operation failed',
    'persistent access recovery failed',
    'queue statistics inspection failed',
    'queue statistics unavailable: %s',
    'quota traffic sample failed',
    'randomized website history omitted choices because the tick reached its limit',
    'release operation failed',
    'router Wi-Fi change failed (%s)',
    'router Wi-Fi change failed while inspecting the wired connection',
    'router Wi-Fi change failed: %s',
    'router Wi-Fi confirmation failed: step=%s reason=%s enabled=%s attempts=%s elapsed=%.1f radios=%s',
    'router Wi-Fi copy failed: %s',
    'router Wi-Fi credential read failed (%s)',
    'router Wi-Fi credential read failed while inspecting the wired connection',
    'router Wi-Fi import failed',
    'router Wi-Fi import failed (%s)',
    'router Wi-Fi import failed while updating the Pi access point',
    'router Wi-Fi import failed: %s',
    'router password check failed (%s)',
    'router password check failed while inspecting the wired connection',
    'router password check failed: %s',
    'saved ad-blocking choices could not be confirmed during dashboard startup',
    'saved devices changed during the address check; checking again later',
    'saved gateway persistence setting did not reach the helper',
    'saved recovery remains unconfirmed. Retry Panic before Start',
    'scheduled network plan failed (%s); trying again in %.0f s and then less often',
    'scheduled network plan failed again (%s); trying again in %.0f s',
    'scheduled network plan works again after %d failed %s',
    'scheduler tick failed unexpectedly',
    'service address refresh failed',
    'service address update failed',
    'service addresses could not be sent: %s',
    'service quota %s has no currently resolvable observed domains',
    'service quota %s references a missing device',
    'socket marking is unavailable: %s',
    'the applied plan could not be read back',
    'the global block list did not reach the filtered resolver',
    'time quota %s has no currently resolvable observed domains',
    'time quota %s references a missing device',
    'traffic counter inspection failed',
    'traffic progression sample failed: %s',
    'update retry could not be confirmed',
    'uploaded release could not be staged',
    'uploaded release refused: %s',
    'upstream connection inspection failed',
    'watching DNS answers on %s',
))


def _argument(value):
    if type(value) in (int, float) and abs(value) <= 2**53 - 1 and (type(value) is int or math.isfinite(value)):
        return value
    if type(value) is bool:
        return value
    return "[detail omitted]"


class SafeFormatter(logging.Formatter):
    def format(self, record):
        try:
            message = record.msg if record.msg in SAFE_MESSAGES else "Event details omitted"
            if record.args and message != "Event details omitted":
                if isinstance(record.args, tuple):
                    message = message % tuple(_argument(v) for v in record.args)
                else:
                    message = "Event details omitted"
            if not isinstance(message, str):
                message = "Event details omitted"
            level = {logging.DEBUG: "DEBUG", logging.INFO: "INFO", logging.WARNING: "WARNING",
                     logging.ERROR: "ERROR", logging.CRITICAL: "CRITICAL"}.get(record.levelno, "INFO")
            return (level + ": " + message)[:2048]
        except Exception:
            return "Event details omitted"


class JournalHandler(logging.Handler):
    def handle(self, record):
        # Native journal writes and rate admission never wait on logging locks.
        self.emit(record)
        return True

    def emit(self, record):
        try:
            priority = "3" if record.levelno >= logging.ERROR else "4" if record.levelno >= logging.WARNING else "6"
            send_fields({"MESSAGE": SafeFormatter().format(record), "SYSLOG_IDENTIFIER": "jittersim",
                         "PRIORITY": priority})
        except Exception:
            pass


class SafeServerErrors:
    def handle_error(self, request, client_address):
        # Socket server defaults print exception text and client addresses.
        logging.getLogger("jittersim").error("Request worker failed")


def _unhandled_failure(*_):
    logging.getLogger("jittersim").error("Unexpected service failure")


def configure_logging(level=logging.INFO):
    if sys.platform.startswith("linux"):
        handler = JournalHandler()
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(SafeFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    sys.excepthook = _unhandled_failure
    threading.excepthook = _unhandled_failure
