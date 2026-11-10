"""Точка входа: запуск из VS Code или через run.bat."""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].startswith("--self-test"):
        from app.diagnostics import command

        return command(sys.argv[1:])
    from app.bootstrap import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
