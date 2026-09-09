"""teamlead - load-balance coding agents running inside Herdr.

Python 3 standard library only. The `herdr` CLI is the sole external
dependency and is reached through :mod:`teamlead.herdr`, which is injected
into every caller so tests never touch the real binary.
"""

__version__ = "0.1.0"

SCHEMA_VERSION = 1
