"""Запуск мозга: python -m brain (из корня проекта). Под pythonw пишет всё в brain/data/brain.log."""
import logging
import os
import sys

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # иначе Pydantic AI печатает баннер в stdout

from .config import LOG_PATH, load_config  # noqa: E402


def main() -> None:
    cfg = load_config()
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stdout is None or sys.stderr is None:  # pythonw: потоков вывода нет, случайный print не должен падать
        sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)

    import uvicorn

    from .server import create_app

    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cfg.port, log_config=None, access_log=False))

    def stop() -> None:
        server.should_exit = True

    app.state.brain.should_exit = stop
    server.run()


if __name__ == "__main__":
    main()
