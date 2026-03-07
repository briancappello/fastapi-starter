"""CLI test fixtures and configuration."""

import nest_asyncio


# Allow nested event loops so CLI commands using asyncio.run()
# can work within pytest's async test infrastructure
nest_asyncio.apply()
