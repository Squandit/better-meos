"""
The better-meos version, the one place it's written down.

Bump it for every release (see CHANGELOG.md), then tag the commit with the
same number (``v1.2.0``): the build refuses a tag that doesn't match, and the
installer and the app's footer show this number.

Numbering: MAJOR for changes an operator has to relearn or that change the
event file in a way older versions can't read; MINOR for new features; PATCH
for fixes.
"""

__version__ = "1.2.0"
