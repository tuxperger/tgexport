"""tgexport CLI entry point."""

from __future__ import annotations

import logging
from pathlib import Path

import click
from rich.logging import RichHandler

from tgexport.cli._console import console


@click.group()
@click.option(
    "--env",
    "env_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to .env file (default: ./.env).",
)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Override directory for session, DB, and media.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Override directory for generated HTML.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Trace progress: -v enables tgexport debug logging, -vv also telethon's.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    env_file: Path | None,
    data_dir: Path | None,
    output_dir: Path | None,
    verbose: int,
) -> None:
    """tgexport — archive your Telegram account into an offline HTML archive."""
    logging.basicConfig(
        level="DEBUG" if verbose >= 2 else "INFO",
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    if verbose == 1:
        logging.getLogger("tgexport").setLevel(logging.DEBUG)
    ctx.ensure_object(dict)
    ctx.obj["env_file"] = env_file
    ctx.obj["data_dir"] = data_dir
    ctx.obj["output_dir"] = output_dir


def _register_commands() -> None:
    from tgexport.cli.cmd_chats import chats
    from tgexport.cli.cmd_dataset import dataset
    from tgexport.cli.cmd_export import export
    from tgexport.cli.cmd_login import login
    from tgexport.cli.cmd_render import render
    from tgexport.cli.cmd_serve import serve
    from tgexport.cli.cmd_watch import watch

    cli.add_command(login)
    cli.add_command(chats)
    cli.add_command(export)
    cli.add_command(render)
    cli.add_command(watch)
    cli.add_command(serve)
    cli.add_command(dataset)


_register_commands()


if __name__ == "__main__":
    cli()
