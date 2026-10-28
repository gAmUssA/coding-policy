"""Pane text shared by the Claude `/usage` parser tests.

The strings are inline copies of captured or wrapped dialogs. Nothing here
reads a file, the clock, or the environment. The two stacked dialogs are not
the same capture: one keeps the settings row and the promo line, the other
is the rate-limit windows alone.
"""

# Verbatim `/usage` pane below 62 columns: the reset rides on the header
# after a middle dot, the bar is its own row, and `N% used` follows. Weekly
# timezones that do not fit wrap onto the next line.
LIVE_INLINE = """\
   Current session · Resets 5pm (America/New_York)
   ██▋

   5% used

   Current week (all models) · Resets Nov 2 at 10am
   (America/New_York)
   ████████████████████▋

   39% used

   Current week (Fable) · Resets Nov 2 at 10am
   (America/New_York)
   ███████████████████

   36% used
"""

# The same dialog word-wrapped at 35 columns. The weekly header breaks after
# `Resets`, so the reset text starts the next line.
WRAPPED_AFTER_RESETS_WORD = """\
   Current session · Resets 5pm
   (America/New_York)
   ██▋

   5% used

   Current week (all models) · Resets
   Nov 2 at 10am (America/New_York)
   ████████████████████▋

   39% used
"""

# Stacked layout at 62 columns or more, including the settings row and the
# promo line that sits between the account week and the Fable week.
OLD_STACKED_WITH_PROMO = """\
   Settings  Status   Config   Usage   Stats
   Current session
   ████                                               8% used
   Resets 12:49am (Europe/Oslo)
   Current week (all models)
   █                                                  2% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
   +50% weekly limits promo through Sep 13 · clau.de/cc-50-promo
   Current week (Fable)
   ██████████████████████████████████████████████   100% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
"""

# The same stacked windows without the settings row or the promo line.
OLD_STACKED_BARE = """\
   Current session
   ████                                               8% used
   Resets 12:49am (Europe/Oslo)
   Current week (all models)
   █                                                  2% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
   Current week (Fable)
   ██████████████████████████████████████████████   100% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
"""
