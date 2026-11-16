"""The static site: credits pages, index, 404, privacy pages, shortlinks."""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path

from reelline.config import conf


def _e(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _link(text, url) -> str:
    return f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(text)}</a>' if url else _e(text)


STYLE = """*{{box-sizing:border-box}}body{{margin:0;background:{night};color:{text};font:17px/1.55 system-ui,
-apple-system,"Segoe UI","Noto Sans Georgian",sans-serif}}header{{background:{yellow};color:{ink};padding:14px 20px;
font-weight:800;font-size:22px}}header a{{color:{ink};text-decoration:none}}main{{max-width:760px;margin:0 auto;
padding:20px}}h1{{font-size:24px;margin:8px 0 4px}}.sub{{opacity:.8;margin:0 0 20px}}ol{{list-style:none;padding:0;
margin:0}}li{{background:{night_2};border-left:6px solid {yellow};border-radius:8px;padding:14px 16px;margin:0 0 14px}}
h2{{font-size:18px;margin:0 0 8px;word-break:break-word}}dl{{display:grid;grid-template-columns:max-content 1fr;
gap:4px 14px;margin:0}}dt{{opacity:.75}}dd{{margin:0;word-break:break-word}}a{{color:{yellow}}}
section h2{{margin:22px 0 6px}}hr{{border:0;border-top:2px solid {yellow};margin:32px 0;opacity:.6}}
footer{{max-width:760px;margin:0 auto;padding:10px 20px 30px;font-size:14px;opacity:.8}}"""


def page_html(reel_id: int, images: list[dict], subtitle: str | None) -> str:
    L = conf().publish.credits_page.labels
    items = []
    for im in images:
        # Snapshots from before generated_author have no author for an AI illustration.
        author = im.get("author") or (L["generated_author"] if im.get("source") == "generated" else L["unknown"])
        rows = [(L["author"], _link(author, im.get("author_url")))]
        if im.get("source") != "generated":
            rows.append((L["license"], _link(im.get("license") or L["unknown"], im.get("license_url"))))
        rows.append((L["source"], _link(im.get("source_name") or im.get("page_url"), im.get("page_url"))))
        items.append(
            f"<li><h2>{_e(im['title'])}</h2><dl>"
            + "".join(f"<dt>{_e(k)}</dt><dd>{v}</dd>" for k, v in rows)
            + "</dl></li>"
        )
    sub = f'<p class="sub">{_e(subtitle)}</p>' if subtitle else ""
    return _doc(
        f"{L['heading']} · {L['site']} #{reel_id}",
        f"<h1>{_e(L['heading'])}</h1>{sub}<ol>{''.join(items)}</ol>",
        marker=reel_id,
    )


def footer() -> str:
    links = " · ".join(
        f'<a href="{_e(path)}">{_e(text)}</a>' for path, text in conf().publish.credits_page.footer.items()
    )
    return f"<footer>{links}</footer>"


def _doc(title: str, body: str, marker=None, page: str | None = None) -> str:
    meta = f'<meta name="{conf().channel.name}-reel" content="{_e(marker)}">' if marker is not None else ""
    if page:
        meta += f'<meta name="{conf().channel.name}-page" content="{_e(page)}">'
    style = STYLE.format(**conf().publish.credits_page.colors).replace("\n", "")
    return (
        f'<!doctype html><html lang="{conf().channel.language}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">{meta}<title>{_e(title)}</title>'
        f'<style>{style}</style></head><body><header><a href="/">{_e(conf().publish.credits_page.labels["site"])}</a>'
        f"</header><main>{body}</main>{footer()}</body></html>\n"
    )


def build_site(conn: sqlite3.Connection, out_dir: Path | None = None) -> list[Path]:
    """Every snapshotted reel's page, an index and a 404, into `out_dir` (emptied first)."""
    import shutil

    out_dir = out_dir or conf().storage.credits_site_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "c").mkdir(parents=True)
    written = []
    rows = conn.execute("""SELECT c.reel_id, c.images, (SELECT youtube_title FROM captions WHERE script_id = r.script_id
                               ORDER BY id DESC LIMIT 1) AS title
                           FROM credits c JOIN reels r ON r.id = c.reel_id ORDER BY c.reel_id""").fetchall()
    for r in rows:
        path = out_dir / "c" / f"{r['reel_id']}.html"  # served at /c/<id>
        path.write_text(page_html(r["reel_id"], json.loads(r["images"]), r["title"]), encoding="utf-8")
        written.append(path)
    from reelline.pipeline.site_pages import pages

    for name, (title, body) in pages().items():  # /privacy, /data-deletion
        path = out_dir / f"{name}.html"
        path.write_text(_doc(title, body, page=name), encoding="utf-8")
        written.append(path)
    L = conf().publish.credits_page.labels
    (out_dir / "index.html").write_text(
        _doc(L["site"], f'<h1>{_e(L["site"])}</h1><p class="sub">{_e(L["heading"])}: /c/&lt;id&gt;</p>'),
        encoding="utf-8",
    )
    (out_dir / "404.html").write_text(_doc("404", "<h1>404</h1>"), encoding="utf-8")
    from reelline.pipeline.shortlinks import write_pages

    written += write_pages(conn, out_dir)  # /s/<code>, every link ever made
    (out_dir / "_headers").write_text(
        "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n", encoding="utf-8"
    )
    return written
