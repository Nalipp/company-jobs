# V5 Mermaid maps

This folder contains local, reproducible Mermaid diagrams generated from the
current V5 SQLite database.

Build both diagrams and their browser-ready outputs from the project root:

```bash
npm install --prefix mermaid
python3 mermaid/build_maps.py
python3 mermaid/render_mermaid.py mermaid/db-schema.md --output mermaid/output/db-schema.html
python3 mermaid/render_mermaid.py mermaid/db-counts.md --output mermaid/output/db-counts.html
```

Open either generated HTML file directly in a browser. The Mermaid source is
kept in the two Markdown files so an LLM or a reviewer can inspect and revise
the diagram definitions separately from rendering.
