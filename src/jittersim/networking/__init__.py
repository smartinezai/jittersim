"""Boundaries for executing validated network operations."""

from .commands import Command, CommandResult
from .executor import CommandExecutionError, FakeExecutor

__all__ = ["Command", "CommandExecutionError", "CommandResult", "FakeExecutor"]
