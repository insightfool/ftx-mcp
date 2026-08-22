"""Allow `python -m service` to launch the entrypoint."""
import sys

from .main import main

if __name__ == "__main__":
    # Pass argv through. `python -m service` runs THIS file, not main.py, so
    # main.py's own `if __name__ == "__main__"` block never fires -- calling
    # main() bare here silently dropped every command-line flag (--hide-console
    # was parsed by a main() that could never see it).
    raise SystemExit(main(sys.argv[1:]))
