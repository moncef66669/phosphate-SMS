"""Render review HTML and all-page QA images using installed Chrome."""
from pathlib import Path
import fitz
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "generated_outputs/project_review"
QA = OUT / "visual_qa"
QA.mkdir(exist_ok=True)
stem = OUT / "MPH-Project-Review-and-Business-Case"
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", headless=True)
    page = browser.new_page()
    page.goto(stem.with_suffix(".html").as_uri(), wait_until="networkidle")
    page.pdf(path=str(stem.with_suffix(".pdf")), format="A4", print_background=True, prefer_css_page_size=True)
    browser.close()
doc = fitz.open(stem.with_suffix(".pdf"))
thumbs = []
for n, page in enumerate(doc, 1):
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
    path = QA / f"review-final-{n:02d}.png"
    pix.save(path)
    im = Image.open(path).convert("RGB")
    im.thumbnail((420, 590))
    tile = Image.new("RGB", (450, 620), "#dce5ec")
    tile.paste(im, ((450-im.width)//2, 20))
    ImageDraw.Draw(tile).text((12, 600), f"Page {n}", fill="black")
    thumbs.append(tile)
contact = Image.new("RGB", (450*3, 620*((len(thumbs)+2)//3)), "#dce5ec")
for i, tile in enumerate(thumbs):
    contact.paste(tile, ((i%3)*450, (i//3)*620))
contact.save(QA / "review-final-contact.png")
print(f"Rendered {len(doc)} pages: {stem.with_suffix('.pdf')}")
