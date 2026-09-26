"""`tgexport serve` — view the archive over a loopback HTTP server.

Chat viewers load their JSON data with fetch(), which browsers refuse for
file:// pages. Only the output and media directories are exposed; the
session file and database living next to the media are never served.
"""

from __future__ import annotations

import functools
import io
import logging
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

import click

from tgexport.cli._helpers import resolve_config

logger = logging.getLogger(__name__)


class _ArchiveHandler(SimpleHTTPRequestHandler):
    allowed_roots: tuple[Path, ...] = ()

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        resolved = Path(self.translate_path(self.path)).resolve()
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        return super().send_head()

    def log_message(self, format: str, *args: object) -> None:
        logger.debug(format, *args)


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=8000, show_default=True, help="Port to listen on.")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """Serve the generated archive locally (needed for the chat viewers)."""
    cfg = resolve_config(ctx, require_api=False)
    output = cfg.output_dir.resolve()
    media = cfg.media_dir.resolve()
    root = Path(os.path.commonpath([output, media.parent]))

    class Handler(_ArchiveHandler):
        allowed_roots = (output, media)

    url_path = output.relative_to(root).as_posix()
    with ThreadingHTTPServer(
        (host, port), functools.partial(Handler, directory=str(root))
    ) as httpd:
        click.echo(
            f"Serving archive at http://{host}:{port}/{url_path}/index.html (Ctrl+C to stop)"
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
