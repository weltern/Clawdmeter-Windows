"""Render README.md -> readme_preview.html (dark theme) for local review.

Images use the README's relative paths, so open the HTML from the repo root.
"""
import os

import markdown

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src = os.path.join(ROOT, "README.md")
out = os.path.join(ROOT, "readme_preview.html")

with open(src, encoding="utf-8") as f:
    body = markdown.markdown(
        f.read(),
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
    )

CSS = """
:root { color-scheme: dark; }
body { background:#0d1117; color:#c9d1d9; font:16px/1.6 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;
       max-width:860px; margin:0 auto; padding:40px 24px 120px; }
h1,h2,h3 { color:#e6edf3; line-height:1.25; margin-top:1.6em; }
h1 { border-bottom:1px solid #21262d; padding-bottom:.3em; }
h2 { border-bottom:1px solid #21262d; padding-bottom:.3em; margin-top:2em; }
a { color:#CE7D6B; text-decoration:none; } a:hover { text-decoration:underline; }
code { background:#161b22; padding:.15em .4em; border-radius:6px; font-size:85%;
       font-family:Consolas,Menlo,monospace; }
pre { background:#161b22; padding:14px 16px; border-radius:8px; overflow:auto; border:1px solid #21262d; }
pre code { background:none; padding:0; }
img { max-width:100%; height:auto; border-radius:8px; border:1px solid #21262d; }
p[align="center"], div[align="center"] { text-align:center; }
table { border-collapse:collapse; margin:1em 0; }
th,td { border:1px solid #30363d; padding:6px 13px; }
th { background:#161b22; }
blockquote { border-left:3px solid #CE7D6B; margin:1em 0; padding:.2em 1em; color:#9da3af; background:#11151b; }
hr { border:0; border-top:1px solid #21262d; }
"""

html = (f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Clawdmeter README preview</title><style>{CSS}</style></head>"
        f"<body>{body}</body></html>")

with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("wrote", out)
