"""OmniAgentOS Starter — an open agent operating system in miniature.

Hand it a goal; a Planner breaks it down, Workers do the work with Agent Skills,
a Critic checks it against a Definition of Done, a Verifier signs it off, and the
loop repairs and re-verifies until it passes or the round cap is reached.
"""

from .config import VERSION

__version__ = VERSION
__all__ = ["VERSION", "__version__"]
