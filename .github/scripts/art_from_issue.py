"""Turn a labelled GitHub issue into an entry in art/art.json.

Reads the issue from the workflow event payload, pulls the first attached
image into art/images/, and appends the piece to the gallery data.
"""

import io
import json
import os
import re
import sys
import unicodedata
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "art", "art.json")
IMAGES = os.path.join(ROOT, "art", "images")
TRIGGER_LABEL = "art"
MAX_EDGE = 1600

IMAGE_PATTERNS = [
    re.compile(r"!\[[^\]]*\]\((\S+?)\)"),          # ![alt](url)
    re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']"),  # <img src="url">
]
DATE_LINE = re.compile(r"^\s*date\s*[:=]\s*(\d{4}-\d{2}-\d{2})\s*$", re.I | re.M)
EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


def fail(message):
    print("::error::" + message)
    # Surfaced back to the issue by the workflow.
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write("status=failed\n")
        fh.write("message=%s\n" % message)
    sys.exit(0)


def slugify(text, fallback):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or fallback


def find_image_url(body):
    for pattern in IMAGE_PATTERNS:
        match = pattern.search(body or "")
        if match:
            return match.group(1)
    return None


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "art-bot"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), response.headers.get("Content-Type", "").split(";")[0].strip()


def process_image(raw, content_type, stem):
    """Downscale, fix orientation, drop EXIF. Returns the written path."""
    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(raw))
    image = ImageOps.exif_transpose(image)

    if max(image.size) > MAX_EDGE:
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        image = image.convert("RGBA")
        path = os.path.join(IMAGES, stem + ".png")
        image.save(path, "PNG", optimize=True)
    else:
        image = image.convert("RGB")
        path = os.path.join(IMAGES, stem + ".jpg")
        image.save(path, "JPEG", quality=82, optimize=True, progressive=True)
    return path


def main():
    with open(os.environ["GITHUB_EVENT_PATH"]) as fh:
        event = json.load(fh)
    issue = event["issue"]
    number = issue["number"]

    labels = [l["name"] for l in issue.get("labels", [])]
    if TRIGGER_LABEL not in labels:
        print("No %r label, nothing to do." % TRIGGER_LABEL)
        return
    art_labels = sorted(l for l in labels if l != TRIGGER_LABEL)

    entries = []
    if os.path.exists(DATA):
        with open(DATA) as fh:
            entries = json.load(fh) or []
    if any(entry.get("issue") == number for entry in entries):
        print("Issue #%d is already in the gallery." % number)
        return

    body = issue.get("body") or ""
    url = find_image_url(body)
    if not url:
        fail("No image found in the issue body. Attach a photo and re-apply the label.")

    # An optional `Date: 2026-03-14` line overrides the posting date,
    # since art is usually made well before it gets posted.
    date = issue["created_at"][:10]
    match = DATE_LINE.search(body)
    if match:
        date = match.group(1)
        body = DATE_LINE.sub("", body, count=1)

    description = re.sub(r"!\[[^\]]*\]\(\S+?\)", "", body)
    description = re.sub(r"<img[^>]*>", "", description)
    description = "\n".join(
        line.strip() for line in description.strip().splitlines() if line.strip()
    )

    title = (issue["title"] or "").strip() or "Untitled"
    stem = "%s-%s" % (date, slugify(title, "piece-%d" % number))

    try:
        raw, content_type = download(url)
    except Exception as exc:  # noqa: BLE001 - reported back to the issue
        fail("Could not download the attached image: %s" % exc)

    if content_type and not content_type.startswith("image/"):
        fail("Attachment is %s, not an image." % content_type)

    os.makedirs(IMAGES, exist_ok=True)
    try:
        path = process_image(raw, content_type, stem)
    except Exception as exc:  # noqa: BLE001
        fail("Could not process the image: %s" % exc)

    entries.append({
        "slug": stem,
        "title": title,
        "date": date,
        "image": "images/" + os.path.basename(path),
        "description": description,
        "labels": art_labels,
        "issue": number,
    })
    entries.sort(key=lambda e: (e.get("date", ""), e.get("issue", 0)), reverse=True)

    with open(DATA, "w") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write("status=added\n")
        fh.write("title=%s\n" % title)
        fh.write("image=art/%s\n" % ("images/" + os.path.basename(path)))
    print("Added %r (%s) from issue #%d" % (title, date, number))


if __name__ == "__main__":
    main()
