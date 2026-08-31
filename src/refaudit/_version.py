"""Single source of truth for the version.

Kept in its own module so ``pyproject.toml`` (via hatch), the package's
``__version__`` and the User-Agent we send to every service all read the same
value. A hardcoded User-Agent silently misreports us to the people whose rate
limits we are trying to respect.
"""

__version__ = "0.3.0"
