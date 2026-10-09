#!/usr/bin/env python3
"""
Extract comments from last-captured.html that are missing from
juror-thread-threaded.html, then append them as a new section.

Usage:  python3 extract-missing-comments.py
Output: juror-thread-threaded.html  (updated in place)
        missing-comments-debug.txt  (extraction log)
"""

import re
import sys
import hashlib
from html import unescape

SOURCE   = "last-captured.html"
TARGET   = "juror-thread-threaded.html"
DEBUG    = "missing-comments-debug.txt"

# ── Avatar color palette (matches existing file) ──────────────────────────────
COLORS = [
    "#1a7a45","#1a6494","#8e44ad","#c0392b","#0d7a65",
    "#2c3e50","#196e3c","#596a6b","#1a5276","#b84500",
    "#784212","#6c3483","#0a7260","#147535","#1a72b3",
]

def avatar_color(name: str) -> str:
    idx = int(hashlib.md5(name.encode()).hexdigest(), 16) % len(COLORS)
    return COLORS[idx]

def initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()

def strip_tags(html: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<[^>]*>', '', text, flags=re.DOTALL)
    # Remove any partial/unclosed tag at the end (happens when block is truncated)
    text = re.sub(r'\s*<[^>]*$', '', text, flags=re.DOTALL)
    return unescape(text).strip()

def sig(text: str) -> str:
    """40-char signature for dedup."""
    return re.sub(r'\s+', ' ', text.strip())[:40].lower()


# ── 1. Load existing signatures from target ───────────────────────────────────
print("Loading existing comment signatures …")
with open(TARGET, "r", errors="replace") as f:
    target_html = f.read()

existing_sigs: set[str] = set()
for m in re.finditer(r'<div class="comment-body">(.*?)</div>', target_html, re.DOTALL):
    text = strip_tags(m.group(1))
    if len(text) > 10:
        existing_sigs.add(sig(text))

print(f"  {len(existing_sigs)} existing signatures")


# ── 2. Stream source file, extract comment blocks ─────────────────────────────
# Each Facebook comment block looks like:
#   <span ... dir=auto lang=en>
#     <div class="xdj266r ...">
#       <div dir=auto ...>
#         <a href="https://www.facebook.com/PROFILE">NAME</a>
#         ... comment text ...
#       </div>
#     </div>
#   </span>
#
# Author name immediately precedes the comment span in a nearby <a> tag
# whose href points to a Facebook profile.

print(f"Scanning {SOURCE} …")

CHUNK   = 512 * 1024   # 512 KB
OVERLAP = 8  * 1024    # overlap to catch blocks spanning chunk boundaries

extracted: list[dict] = []
seen_sigs: set[str]   = set()
offset = 0

with open(SOURCE, "r", errors="replace") as f:
    leftover = ""
    while True:
        raw = f.read(CHUNK)
        if not raw:
            break
        chunk = leftover + raw

        # Find comment body spans: dir=auto lang=en
        for m in re.finditer(r'(<span[^>]+dir=auto[^>]+lang=en[^>]*>)', chunk):
            block_start = m.start()
            block = chunk[block_start: block_start + 6000]

            # ── Extract comment text first (raw plain text) ──────────────────
            text = strip_tags(block[:3000])
            # Drop UI chrome: timestamps, Like/Reply, reports, CSS class bleed
            text = re.sub(r'\s*(Like\s*·?\s*Reply\b.*|Reply\s+\d+.*|Hide or report.*|See translation.*|\bEdited\b.*)', '', text, flags=re.IGNORECASE)
            text = re.sub(r'\s*\bx[0-9a-z]{5,}\b\s*', ' ', text)
            text = re.sub(r'\s{2,}', ' ', text).strip()

            # ── Extract author from raw HTML ──────────────────────────────────
            # Structure: <a href="fb.com/..."><span><span><span>Name</span>...
            # The innermost <span> contains just the name, followed by </a> then text.
            author = "Unknown"
            auth_m = re.search(
                r'href="https://www\.facebook\.com/[^"]*"[^>]*>'
                r'(?:<span[^>]*>){1,5}([^<]{2,60})(?:</span>){1,5}</a>',
                block[:2000], re.DOTALL
            )
            if auth_m:
                candidate = unescape(auth_m.group(1)).strip()
                words = candidate.split()
                if (2 <= len(words) <= 4
                        and all(w[0].isupper() for w in words)
                        and not any(c.isdigit() for c in candidate)):
                    author = candidate

            # Strip author name from start of plain text
            if author != "Unknown":
                text = re.sub(r'^\s*' + re.escape(author) + r'\s*', '', text)

            # Remove any inline style bleed-through (from emoji/image fallback divs)
            text = re.sub(r"\s*'\s*style=\"[^\"]*\"\s*", ' ', text)
            text = re.sub(r'^\s*\d+[wdhm]\s*', '', text).strip()

            if len(text) < 20:
                continue

            s = sig(text)
            if s in existing_sigs or s in seen_sigs:
                continue

            # ── Extract timestamp ─────────────────────────────────────────────
            ts_m = re.search(r'dir=auto>(\d+[wdhm])</span>', block[:500])
            ts = ts_m.group(1) if ts_m else ""

            seen_sigs.add(s)
            extracted.append({"author": author, "text": text, "ts": ts})

        # Keep overlap for next iteration
        leftover = chunk[-OVERLAP:]
        offset += len(raw)
        print(f"  … {offset // 1_000_000} MB scanned, {len(extracted)} new comments so far", end="\r")

print(f"\nExtracted {len(extracted)} missing comments")


# ── 3. Write debug log ────────────────────────────────────────────────────────
with open(DEBUG, "w") as dbg:
    for i, c in enumerate(extracted, 1):
        dbg.write(f"[{i}] {c['author']} ({c['ts']})\n{c['text'][:200]}\n\n")
print(f"Debug log → {DEBUG}")


# ── 4. Build HTML for missing comments ───────────────────────────────────────
def build_comment_html(c: dict) -> str:
    author  = c["author"]
    text    = c["text"]
    ts      = c["ts"] or "?"
    color   = avatar_color(author)
    ini     = initials(author)
    # Escape for HTML
    safe_author = author.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    safe_text   = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return (
        f'\n<div class="comment" style="margin-left:0;border-left:2px solid #ddd;background:#fafafa;">\n'
        f'  <div class="comment-header">\n'
        f'    <span class="avatar" style="background:{color};">{ini}</span>\n'
        f'    <span class="author" style="color:{color};">{safe_author}</span>\n'
        f'    <span class="ts">{ts}</span>\n'
        f'  </div>\n'
        f'  <div class="comment-body">{safe_text}</div>\n'
        f'</div>'
    )

if not extracted:
    print("Nothing missing — file is up to date.")
    sys.exit(0)

new_html_blocks = "\n".join(build_comment_html(c) for c in extracted)

section = (
    "\n\n<!-- ═══ APPENDED MISSING COMMENTS ═══════════════════════════════ -->\n"
    f'<div style="margin-top:24px;padding-top:16px;border-top:2px dashed #ccc;">\n'
    f'  <p style="font-size:11px;color:#999;margin-bottom:8px;">'
    f'{len(extracted)} additional comments extracted from last-captured.html</p>\n'
    f'{new_html_blocks}\n'
    f'</div>\n'
)

# ── 5. Inject before </div> closing page-wrap ─────────────────────────────────
if "</div>\n</body>" in target_html:
    updated = target_html.replace("</div>\n</body>", section + "</div>\n</body>", 1)
elif "</body>" in target_html:
    updated = target_html.replace("</body>", section + "</body>", 1)
else:
    updated = target_html + section

with open(TARGET, "w") as f:
    f.write(updated)

print(f"Done — appended {len(extracted)} comments to {TARGET}")
