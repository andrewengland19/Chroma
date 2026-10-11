# =============================================================================
# hub/paths.py — where CHROMA keeps its state.
#
# Default ~/.chroma. CHROMA_HOME overrides it (dev, tests, `chromad --fake`), so a
# development engine never reads or writes the live hub's files. Unset == the
# exact paths every module used before.
# =============================================================================

import os


def chroma_dir() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get("CHROMA_HOME") or "~/.chroma"))


CHROMA_DIR = chroma_dir()
