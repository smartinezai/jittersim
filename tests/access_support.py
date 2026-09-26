"""A service double for testing durable access changes without host commands."""

import re

from jittersim.networking.commands import CommandResult
from jittersim.networking.executor import CommandExecutionError, FakeExecutor


class AccessExecutor(FakeExecutor):
    def __init__(self, directory):
        super().__init__()
        self.directory = directory
        self.fail_restarts = 0
        self.digest = None

    def run(self, command):
        result = super().run(command)
        if command.executable == "systemctl" and command.arguments[0] == "restart":
            if self.fail_restarts:
                self.fail_restarts -= 1
                raise CommandExecutionError("simulated DNS restart failure")
            config = (self.directory / "unbound.conf").read_text()
            self.digest = re.search(r'TXT "([a-f0-9]{64})"', config).group(1)
        if command.executable == "dig":
            return CommandResult(return_code=0, stdout=f'"{self.digest}"\n')
        if command.executable == "hostapd_cli":
            return CommandResult(return_code=0, stdout="OK\n")
        return result
