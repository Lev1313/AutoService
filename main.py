"""Точка входа: запуск из VS Code или через run.bat."""

import sys


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("--self-test", "--self-test-reopen"):
        from app.diagnostics import command

        return command(args)
    if args:
        return 2

    from app.bootstrap import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
