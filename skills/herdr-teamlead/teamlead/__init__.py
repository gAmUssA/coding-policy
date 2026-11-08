"""teamlead - load-balance coding agents running inside Herdr or Rex.

Python 3 standard library only. The core speaks the `herdr` command line
through :mod:`teamlead.herdr`, which is injected into every caller so tests
never touch a real binary. That command line is Herdr itself, or the Rex
adapter :mod:`teamlead.rex_herdr` (`rex-herdr.sh`), which drives the `rex`
CLI; `TEAMLEAD_BACKEND` names which one answers.
"""

__version__ = "0.1.0"

SCHEMA_VERSION = 1
