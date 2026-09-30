"""Start the Whisper proof of concept: `python run.py` (add --no-browser to skip opening a tab)."""
import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the KBC Whisper proof of concept")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open the demo in a browser tab")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Whisper demo   {url}\n  API docs       {url}/docs\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
