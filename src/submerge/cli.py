"""Command line interface for submerge."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .extract import SubtitleExtractionError, extract_subtitles
from .merge import InvalidSubtitleError, MergeConfig, merge_bilingual
from .probe import NoSubtitleTracksError, ProbeError, list_subtitle_tracks

console = Console()
logger = logging.getLogger("submerge")


def validate_color(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Validate hexadecimal color format."""
    color = value.lstrip("#")
    if len(color) != 6:
        raise click.BadParameter(f"Invalid format: {value}. Expected: #RRGGBB (e.g., #FFFFFF)")
    try:
        int(color, 16)
    except ValueError:
        raise click.BadParameter(
            f"Invalid color: {value}. Use hexadecimal format #RRGGBB"
        ) from None
    return f"#{color}"


def setup_logging(verbose: bool) -> None:
    """Configure logging."""
    # Without -v: WARNING only, with -v: DEBUG
    level = logging.DEBUG if verbose else logging.WARNING

    # Configure parent logger so all submodules inherit
    root_logger = logging.getLogger("submerge")
    root_logger.setLevel(level)

    if verbose:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root_logger.addHandler(handler)


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """SubMerge - Bilingual subtitle generator.

    CLI tool to create synchronized bilingual subtitles
    from a video file and two subtitle files.
    """
    pass


@main.command("list-tracks")
@click.argument("video", type=click.Path(exists=True, path_type=Path))
def list_tracks_cmd(video: Path) -> None:
    """List subtitle tracks from a video file.

    VIDEO: Path to video file (MKV, MP4, etc.)
    """
    try:
        tracks = list_subtitle_tracks(video)
    except ProbeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except NoSubtitleTracksError as e:
        console.print(f"[yellow]Warning:[/yellow] {e}")
        sys.exit(1)

    table = Table(title=f"Subtitle tracks - {video.name}")
    table.add_column("Index", style="cyan", justify="right")
    table.add_column("Language", style="green")
    table.add_column("Codec", style="yellow")
    table.add_column("Title")
    table.add_column("Flags")

    for track in tracks:
        flags = []
        if track.is_default:
            flags.append("default")
        if track.is_forced:
            flags.append("forced")
        if not track.is_text:
            flags.append("[red]image[/red]")

        table.add_row(
            str(track.index),
            track.language or "?",
            track.codec,
            track.title or "",
            ", ".join(flags) if flags else "",
        )

    console.print(table)

    text_count = sum(1 for t in tracks if t.is_text)
    console.print(f"\n[dim]{text_count} usable text track(s)[/dim]")


@main.command("extract")
@click.argument("video", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output subtitle file (e.g., output.srt)",
)
@click.option(
    "--track",
    "-t",
    type=int,
    default=None,
    help="Track index to extract (from list-tracks output)",
)
@click.option(
    "--lang",
    "-l",
    type=str,
    default=None,
    help="Language code to extract (e.g., en, eng, fr)",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose mode")
def extract_cmd(
    video: Path,
    output: Path,
    track: int | None,
    lang: str | None,
    verbose: bool,
) -> None:
    """Extract a subtitle track from a video file.

    VIDEO: Path to video file (MKV, MP4, etc.)

    Use list-tracks to see available tracks, then extract by index or language.

    Examples:

    \b
        submerge extract movie.mkv -o english.srt --track 2
        submerge extract movie.mkv -o english.srt --lang en
    """
    setup_logging(verbose)

    try:
        with console.status(f"Extracting subtitles from [cyan]{video.name}[/cyan]..."):
            extract_subtitles(video, output, track_index=track, language=lang)
        console.print(f"[green]✓[/green] Subtitles extracted: [bold]{output}[/bold]")

    except NoSubtitleTracksError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except SubtitleExtractionError as e:
        console.print(f"[red]Extraction error:[/red] {e}")
        sys.exit(1)
    except ProbeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def check_ffsubsync_available() -> bool:
    """Check if ffsubsync is installed."""
    try:
        import ffsubsync  # noqa: F401

        return True
    except ImportError:
        return False


@main.command("sync")
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--ref",
    "-r",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Reference file for sub-to-sub sync",
)
@click.option(
    "--video",
    "-V",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Video file for audio sync",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose mode")
def sync_cmd(
    input: Path,
    ref: Path | None,
    video: Path | None,
    output: Path,
    verbose: bool,
) -> None:
    """Synchronize a subtitle file.

    INPUT: Subtitle file to synchronize

    Two modes available:

    \b
    - Sub-to-sub: --ref REFERENCE.srt (fast)
    - Audio sync: --video VIDEO.mkv (slower, no reference needed)

    Examples:

    \b
        submerge sync input.srt --ref reference.srt -o output.srt
        submerge sync input.srt --video movie.mkv -o output.srt
    """
    setup_logging(verbose)

    # Check ffsubsync
    if not check_ffsubsync_available():
        console.print("[red]Error:[/red] ffsubsync is not installed.")
        console.print("Install it with: [bold]pip install 'submerge[sync]'[/bold]")
        sys.exit(1)

    # Check that we have --ref or --video
    if ref is None and video is None:
        console.print("[red]Error:[/red] You must specify --ref or --video")
        sys.exit(1)

    if ref is not None and video is not None:
        console.print("[red]Error:[/red] Specify --ref OR --video, not both")
        sys.exit(1)

    # Late import (ffsubsync may not be installed)
    from .sync import FfsubsyncNotFoundError, SyncError, sync_subtitles, sync_subtitles_to_video

    try:
        if ref is not None:
            # Sub-to-sub mode
            with console.status(f"Synchronizing [cyan]{input.name}[/cyan]..."):
                sync_subtitles(ref, input, output)
            console.print(f"[green]✓[/green] {input.name} synchronized (sub-to-sub)")
        else:
            # Audio sync mode
            with console.status(f"Audio sync [cyan]{input.name}[/cyan] (1-2 min)..."):
                sync_subtitles_to_video(video, input, output)
            console.print(f"[green]✓[/green] {input.name} synchronized (audio)")

        console.print(f"\n[green]✓[/green] File created: [bold]{output}[/bold]")

    except FfsubsyncNotFoundError as e:
        console.print(f"\n[red]Error:[/red] {e}")
        console.print("Install with: [bold]pip install 'submerge[sync]'[/bold]")
        sys.exit(1)
    except SyncError as e:
        console.print(f"\n[red]Sync error:[/red] {e}")
        sys.exit(1)


@main.command("merge")
@click.argument("sub1", type=click.Path(exists=True, path_type=Path))
@click.argument("sub2", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="Output ASS file",
)
@click.option(
    "--color1",
    default="#FFFFFF",
    callback=validate_color,
    help="Color of first subtitle at bottom (default: white #FFFFFF)",
)
@click.option(
    "--color2",
    default="#FFFF00",
    callback=validate_color,
    help="Color of second subtitle at top (default: yellow #FFFF00)",
)
@click.option(
    "--fontsize",
    default=18,
    type=int,
    help="Font size (default: 18)",
)
@click.option(
    "--layout",
    type=click.Choice(["top-bottom", "stacked"]),
    default="top-bottom",
    help="Layout: top-bottom (default) or stacked (both at bottom)",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose mode")
def merge_cmd(
    sub1: Path,
    sub2: Path,
    output: Path,
    color1: str,
    color2: str,
    fontsize: int,
    layout: Literal["top-bottom", "stacked"],
    verbose: bool,
) -> None:
    """Merge two subtitle files into a bilingual ASS file.

    SUB1: Subtitles displayed at bottom (or first for stacked)

    SUB2: Subtitles displayed at top (or second for stacked)

    Examples:

    \b
        submerge merge french.srt polish.srt -o bilingual.ass
        submerge merge fr.srt pl.srt -o output.ass --layout stacked
    """
    setup_logging(verbose)

    config = MergeConfig(
        color_bottom=color1,
        color_top=color2,
        fontsize=fontsize,
        layout=layout,
    )

    try:
        with console.status("Creating bilingual file..."):
            merge_bilingual(sub1, sub2, output, config)
        console.print("[green]✓[/green] Bilingual file created")
        console.print(f"\n[green]✓[/green] File created: [bold]{output}[/bold]")

    except InvalidSubtitleError as e:
        console.print(f"\n[red]Subtitle error:[/red] {e}")
        sys.exit(1)


@main.command("serve")
@click.option("--host", default="127.0.0.1", help="Bind address (0.0.0.0 for Docker)")
@click.option("--port", default=8282, help="Port")
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(["debug", "info", "warning", "error"]),
    help="Log level",
)
def serve_cmd(host: str, port: int, log_level: str) -> None:
    """Start the API server for Bazarr.

    The server exposes a POST /hook endpoint to receive notifications
    from Bazarr when a subtitle is downloaded.

    Example:
        submerge serve --port 8282 --log-level debug
    """
    import uvicorn

    console.print(f"[green]Starting server on {host}:{port}[/green]")
    uvicorn.run("submerge.api:app", host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
