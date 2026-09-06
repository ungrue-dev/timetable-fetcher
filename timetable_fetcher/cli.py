"""Command-line entry point for the local timetable web application."""

from __future__ import annotations

import argparse
import sys

from .server import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Запустить локальное веб-приложение расписания группы 653 БГУ."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Локальный порт (по умолчанию: 8000)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Не открывать приложение в браузере автоматически",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        build_parser().error("--port должен быть в диапазоне от 1 до 65535")
    try:
        serve(port=args.port, open_browser=not args.no_open)
    except OSError as error:
        print(f"Не удалось запустить приложение расписания: {error}", file=sys.stderr)
        return 1
    return 0
