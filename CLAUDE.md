Project: Control (repo and older files: better-meos)
Purpose: Web-based orienteering event management system

IMPORTANT: At the start of every new session, read HANDOFF.md first. It is the
running record of what has recently changed (current state, in-progress work,
uncommitted changes, and next steps) and is the source of truth where it
disagrees with this file.

Stack:
- Python / Flask backend (served by waitress in the launcher/exe)
- SQLite database (one .bmeos file per event)
- HTML/CSS/JS frontend (vanilla, no framework)
- Server-Sent Events for live updates
- sportident PyPI library for SI card reader on COM5

Key conventions:
- Git: commit and push straight to main. Only use a separate branch for a
  feature that might break something (and say so); the owner merges those.
- Versions: `version.py` holds the number (semver, see CHANGELOG.md). On a
  major change or a batch of features worth handing to operators: bump
  version.py, add a CHANGELOG.md entry, commit, then tag `vX.Y.Z` and push the
  tag. CI refuses a tag that doesn't match version.py and attaches the
  installer + exe to a GitHub Release.
- Timestamps use Python datetime objects, not seconds since midnight
- Mock SI card data lives in MOCK_CARD_DATA dict for hardware-free development
- Long-term project, prioritise correctness and readability over cleverness

Domain context:
- Orienteering: competitors carry SI cards that punch checkpoints (controls)
- Finish download = reading the SI card to get split times
- Classes = competitive categories (e.g. M21E, W18)
- The user knows the domain well, skip basic explanations