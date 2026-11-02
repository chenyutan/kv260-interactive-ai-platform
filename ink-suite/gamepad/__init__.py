"""Dependency-free native controller input shared by the suite host."""

from .device import GamepadReader
from .pointer import DesktopPointer

__all__ = ["GamepadReader", "DesktopPointer"]
