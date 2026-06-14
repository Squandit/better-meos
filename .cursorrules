Project: better-meos
Purpose: Web-based orienteering event management system

Stack:
- Python / FastAPI backend
- SQLite database
- HTML/CSS/JS frontend (vanilla, no framework)
- WebSockets for live updates
- sportident PyPI library for SI card reader on COM5

Key conventions:
- Timestamps use Python datetime objects, not seconds since midnight
- Mock SI card data lives in MOCK_CARD_DATA dict for hardware-free development
- Long-term project, prioritise correctness and readability over cleverness

Domain context:
- Orienteering: competitors carry SI cards that punch checkpoints (controls)
- Finish download = reading the SI card to get split times
- Classes = competitive categories (e.g. M21E, W18)
- The user knows the domain well, skip basic explanations