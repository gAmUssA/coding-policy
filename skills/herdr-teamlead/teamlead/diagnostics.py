"""The one place diagnostics go.

stdout carries the command's JSON document and nothing else, so every warning
teamlead emits goes to stderr. Modules take an injected `warn` callable that
defaults to `stderr_warn`, which is what lets tests capture warnings instead
of printing them.
"""

import sys

#: Prefix on every line, so a warning is attributable when it lands in a log
#: interleaved with herdr's own output.
PREFIX = "teamlead: "


def stderr_warn(message):
    """Write one diagnostic line to stderr."""
    print("{}{}".format(PREFIX, message), file=sys.stderr)
