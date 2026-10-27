#!/usr/bin/env python3
"""Render Mermaid fences from Markdown into a self-contained local HTML file."""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERMAID_DIR = Path(__file__).resolve().parent


def find_mermaid_binary() -> str | None:
    configured = os.environ.get("MERMAID_BIN")
    candidates = [
        configured,
        str(MERMAID_DIR / "node_modules" / ".bin" / "mmdc"),
        str(ROOT / "node_modules" / ".bin" / "mmdc"),
        shutil.which("mmdc"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def extract_document(source: str) -> tuple[str, list[str]]:
    pattern = re.compile(r"```mermaid\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
    diagrams = [match.group(1).strip() for match in pattern.finditer(source)]
    if not diagrams:
        raise ValueError("No ```mermaid fenced diagrams found")
    prose = pattern.sub("", source)
    return prose, diagrams


def simple_markdown(source: str) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.append(f"<p>{html.escape(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue
        if line.startswith("# "):
            flush_paragraph(); close_list()
            output.append(f"<h1>{html.escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            flush_paragraph(); close_list()
            output.append(f"<h2>{html.escape(line[3:].strip())}</h2>")
        elif line.startswith("- "):
            flush_paragraph()
            if not in_list:
                output.append("<ul>")
                in_list = True
            item = html.escape(line[2:].strip())
            item = re.sub(r"`([^`]+)`", r"<code>\1</code>", item)
            item = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", item)
            output.append(f"<li>{item}</li>")
        else:
            close_list()
            paragraph.append(line)
    flush_paragraph()
    close_list()
    return "\n".join(output)


def render(source_path: Path, output_path: Path) -> Path:
    binary = find_mermaid_binary()
    if not binary:
        raise RuntimeError(
            "Mermaid CLI was not found. Install it with: "
            "npm install --prefix mermaid --save-dev @mermaid-js/mermaid-cli"
        )
    prose, diagrams = extract_document(source_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v5-mermaid-") as temporary:
        temporary_path = Path(temporary)
        svgs: list[str] = []
        for index, diagram in enumerate(diagrams, start=1):
            source_file = temporary_path / f"diagram-{index}.mmd"
            svg_file = temporary_path / f"diagram-{index}.svg"
            source_file.write_text(diagram + "\n", encoding="utf-8")
            subprocess.run(
                [binary, "-i", str(source_file), "-o", str(svg_file), "-b", "transparent"],
                check=True,
                cwd=ROOT,
            )
            svgs.append(svg_file.read_text(encoding="utf-8"))

    sections = [f'<section class="diagram">{svg}</section>' for svg in svgs]
    title_match = re.search(r"^#\s+(.+)$", prose, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else source_path.stem
    document = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111; background: #fff; }}
    body {{ max-width: 1600px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    h2 {{ margin: 26px 0 8px; font-size: 18px; }}
    p {{ margin: 8px 0; line-height: 1.5; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    ul {{ line-height: 1.6; }}
    .diagram {{ overflow: auto; margin: 20px 0 30px; padding: 14px; border: 1px solid #ddd; border-radius: 8px; background: #fff; }}
    .diagram svg {{ display: block; max-width: none; height: auto; margin: 0 auto; }}
  </style>
</head>
<body>
  {simple_markdown(prose)}
  {''.join(sections)}
</body>
</html>
'''
    output_path.write_text(document, encoding="utf-8")
    if len(svgs) == 1:
        output_path.with_suffix(".svg").write_text(svgs[0], encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(render(args.source, args.output))


if __name__ == "__main__":
    main()
