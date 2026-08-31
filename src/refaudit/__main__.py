"""Entry point for ``python -m refaudit``.

Guarded so that importing this module -- which documentation generators and
test collectors do -- cannot execute the command line application.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
