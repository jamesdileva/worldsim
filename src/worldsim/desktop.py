"""Desktop launcher (Sprint 54): native window over the local web API.

pywebview opens an OS-native webview (Edge WebView2 on Windows) pointed
at the same FastAPI app `worldsim serve` exposes — no bundled browser,
no Node. Optional dependency: `pip install pywebview`.
"""

from __future__ import annotations

import threading


def launch_desktop(
    host: str = "127.0.0.1",
    port: int = 8600,
    db_path: str | None = None,
    world_id: str | None = None,
    width: int = 1280,
    height: int = 800,
    llm: bool = False,
    llm_model: str | None = None,
) -> int:
    """Start the API server + open the desktop window. Blocks until the
    window closes."""
    try:
        import webview  # pywebview
    except ImportError:
        print(
            "pywebview is not installed — run:\n"
            "  pip install pywebview\n"
            "or use `worldsim serve` and open http://127.0.0.1:%d in a "
            "browser." % port,
        )
        return 1

    from .db import WorldStore
    from .webapp import WorldSession, create_app

    store = WorldStore(db_path) if db_path else WorldStore()
    session = WorldSession(store=store)
    if llm:
        if session.enable_llm(model=llm_model):
            print(f"LLM advisor enabled"
                  f"{f' (model: {llm_model})' if llm_model else ''}")
        else:
            print("LLM deps unavailable — running rules-only")
    if world_id:
        try:
            session.load(world_id)
        except Exception as exc:
            print(f"could not load {world_id!r}: {exc}")
            return 1
    app = create_app(session)

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    url_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    webview.create_window(
        "WorldSim",
        f"http://{url_host}:{port}",
        width=width,
        height=height,
    )
    try:
        webview.start()
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the packaged executable."""
    import argparse

    from .cli import DEFAULT_DB_PATH

    parser = argparse.ArgumentParser(
        prog="WorldSim", description="The World Simulator desktop app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--world-id", default=None)
    parser.add_argument("--llm", action="store_true",
                        help="attach a background Ollama advisor")
    parser.add_argument("--llm-model", default=None,
                        help="Ollama model override "
                             "(try llama3.2:3b for speed)")
    args = parser.parse_args(argv)
    return launch_desktop(
        host=args.host, port=args.port, db_path=args.db,
        world_id=args.world_id, llm=args.llm, llm_model=args.llm_model)


if __name__ == "__main__":
    raise SystemExit(main())
