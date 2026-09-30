"""Remove only the obsolete firewall tables from older JitterSim releases."""

from .commands import Command
from .executor import SubprocessExecutor

LEGACY_ACCESS_CLEANUP = """add table inet jittersim_access
delete table inet jittersim_access
add table netdev jittersim_access
delete table netdev jittersim_access
"""


def cleanup_command() -> Command:
    """Make missing old tables harmless while preserving real command failures."""

    return Command(
        executable="nft", arguments=("-f", "-"),
        purpose="remove obsolete JitterSim firewall tables",
        stdin=LEGACY_ACCESS_CLEANUP,
    )


if __name__ == "__main__":
    SubprocessExecutor().run(cleanup_command())
