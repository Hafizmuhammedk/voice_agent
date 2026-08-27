"""Command-line launcher for the FastAPI backend."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Run the API and bundled frontend on the local development server."""
    uvicorn.run(
        "backend.app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
