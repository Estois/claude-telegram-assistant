"""Shared logger for the ykbot package.

A single fixed logger name (``"ykbot"``) so log lines read the same whether the
bot is launched via ``python bot.py`` or imported by the test suite.
"""

import logging

logger = logging.getLogger("ykbot")
