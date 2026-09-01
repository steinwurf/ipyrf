from __future__ import annotations

from pathlib import Path
from typing import List, Union

from .loader import ReportError
from .plots import build_export_figures, require_plotly


def write_images(
    data,
    directory: Union[str, Path],
    fmt: str,
) -> List[Path]:
    """Export each report plot as PNG or SVG. Requires the kaleido package."""
    if fmt not in ("png", "svg"):
        raise ReportError(f"unsupported image format {fmt!r}")
    require_plotly()
    _require_kaleido(fmt)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    figures = build_export_figures(data)
    for name, fig in figures.items():
        path = directory / f"{name}.{fmt}"
        try:
            fig.write_image(str(path), format=fmt)
        except Exception as e:
            raise ReportError(f"failed to write {path}: {e}") from e
        written.append(path)
    return written


def _require_kaleido(fmt: str) -> None:
    try:
        import kaleido  # noqa: F401
    except ImportError as e:
        raise ReportError(
            f"{fmt.upper()} export requires the kaleido package. "
            "Install it with: python3 -m pip install 'ipyrf[report-export]'"
        ) from e
