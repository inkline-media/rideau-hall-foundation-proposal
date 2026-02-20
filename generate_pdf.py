#!/usr/bin/env python3
"""
Generate a paginated 8.5×14 (US Legal) PDF from the RHF Website Consolidation proposal HTML.

Three-stage process:
  1. Playwright + Chromium renders HTML to PDF (11×14, no footer)
  2. PyMuPDF post-processes to add branded footer on every page
  3. PyMuPDF screenshots every page for visual QA
"""

import asyncio, re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright
import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "index.html"
OUTPUT_PATH = BASE_DIR / "Inkline-RHF-Website-Consolidation-Proposal.pdf"
SCREENSHOT_DIR = Path("/sessions/hopeful-focused-feynman/pdf_pages")

# ---------------------------------------------------------------------------
# Page setup  -  8.5 × 14 inches  (US Legal)
# ---------------------------------------------------------------------------
PAGE_WIDTH = "8.5in"
PAGE_HEIGHT = "14in"

# Footer dimensions
FOOTER_H_MM = 10          # footer strip height in mm
FOOTER_H_PT = FOOTER_H_MM * 72 / 25.4   # ~28 points

# Margins
MARGIN_TOP = "14mm"
MARGIN_RIGHT = "0mm"
MARGIN_BOTTOM = f"{FOOTER_H_MM + 4}mm"   # footer + 4mm breathing room
MARGIN_LEFT = "0mm"

# ---------------------------------------------------------------------------
# Colours  (RGB 0-1 floats for PyMuPDF)
# ---------------------------------------------------------------------------
COBALT = (0 / 255, 87 / 255, 184 / 255)         # #0057B8
GREY_MED = (142 / 255, 142 / 255, 142 / 255)    # #8E8E8E
CERULEAN = (19 / 255, 153 / 255, 204 / 255)     # #1399CC
BLACK = (16 / 255, 16 / 255, 16 / 255)          # #101010

# ---------------------------------------------------------------------------
# Print CSS  -  injected into the page before PDF generation
# ---------------------------------------------------------------------------
PRINT_CSS = """
<style id="pdf-print-styles">
  /* ---- Force new page before each major section ---- */
  .pdf-section-break {
    break-before: page !important;
    page-break-before: always !important;
  }

  /* ---- Hide elements not needed in PDF ---- */
  .side-nav,
  .toc,
  .portfolio-lightbox,
  .pdf-download-btn,
  .footer,
  .pdf-hide {
    display: none !important;
  }

  /* ---- Card-level break-inside avoidance ---- */
  tr,
  .portfolio-card,
  .scope-card,
  .req-card,
  .site-card,
  .flag-card,
  .stat-card,
  .team-card,
  .next-card,
  .consideration-card,
  .timeline-phase,
  .timeline-item,
  .cost-highlight,
  .journey-container {
    break-inside: avoid !important;
    page-break-inside: avoid !important;
  }

  /* ---- Phase/section label rows stay with following card ---- */
  .phase-header,
  .req-section-label {
    break-after: avoid !important;
    page-break-after: avoid !important;
  }

  /* ---- Headings stay with following content ---- */
  h1, h2, h3, h4, h5, h6,
  .section-label {
    break-after: avoid !important;
    page-break-after: avoid !important;
  }

  /* ---- Remove hero animation for cleaner render ---- */
  .hero::before {
    animation: none !important;
  }

  /* ---- Ensure images don't overflow ---- */
  img {
    max-width: 100% !important;
  }

  /* ---- Remove body overflow constraints ---- */
  body {
    overflow: visible !important;
  }

  /* ============================================================
     PDF DENSITY: Scale down typography & spacing for 8.5×14
     ============================================================ */
  body {
    font-size: 14px !important;
    line-height: 1.45 !important;
  }
  h1 { font-size: 1.9rem !important; margin-bottom: 0.5rem !important; }
  h2 { font-size: 1.5rem !important; margin-bottom: 0.4rem !important; }
  h3 { font-size: 1.15rem !important; margin-bottom: 0.3rem !important; }
  h4, h5, h6 { font-size: 0.95rem !important; }
  p { margin-bottom: 0.6rem !important; }
  section { padding-top: 1.5rem !important; padding-bottom: 1.5rem !important; }

  /* Tighten section headings and labels */
  .section-label { font-size: 0.7rem !important; }

  /* Tighter card gaps */
  .scope-grid, .portfolio-grid, .considerations-grid {
    gap: 1rem !important;
  }

  /* Smaller stat cards in hero */
  .stat-card { padding: 0.75rem !important; }
  .stat-card .stat-number { font-size: 1.5rem !important; }
  .stat-card .stat-label { font-size: 0.65rem !important; }

  /* ---- Compact cost-highlight for PDF so it doesn't push to next page ---- */
  .cost-highlight {
    padding: 1.25rem 2rem !important;
    margin: 1rem 0 !important;
    border-radius: 12px !important;
  }
  .cost-highlight .cost-range {
    font-size: 2rem !important;
    margin-bottom: 0.25rem !important;
  }
  .cost-highlight .cost-label {
    font-size: 0.9rem !important;
  }
  .cost-highlight .cost-note {
    font-size: 0.75rem !important;
    margin-top: 0.25rem !important;
  }

  /* ---- Content padding (backgrounds bleed edge-to-edge, text gets margin) ---- */
  .container {
    padding-left: 2.5rem !important;
    padding-right: 2.5rem !important;
  }

  /* ---- Hub-spoke diagram: keep together ---- */
  .strategy-diagram {
    break-inside: avoid !important;
    page-break-inside: avoid !important;
  }
  .hub-spoke {
    transform: scale(0.85) !important;
    transform-origin: top center !important;
  }

  /* ---- Navigation architecture levels: avoid splitting ---- */
  .nav-level {
    break-inside: avoid !important;
    page-break-inside: avoid !important;
  }

  /* ---- Mega menu 4-col grid: tighten for 8.5" width ---- */
  .nav-level span[style*="font-size:1rem"],
  .nav-level span { font-size: 0.82rem !important; }
  .nav-level .spoke-icon { width: 18px !important; height: 18px !important; }

  /* ---- Portfolio grid: single column for expanded hybrid cards ---- */
  .portfolio-grid {
    grid-template-columns: 1fr !important;
    gap: 2rem !important;
  }

  /* ---- Expanded portfolio card layout for PDF (image top, text below) ---- */
  .portfolio-card.pdf-expanded {
    display: block !important;
    cursor: default !important;
    border: 1px solid #E5E7EB !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    background: #fff !important;
  }
  .portfolio-card.pdf-expanded .portfolio-thumb {
    width: 100% !important;
    height: auto !important;
    max-height: 240px !important;
    object-fit: cover !important;
    object-position: top center !important;
    border-radius: 0 !important;
    display: block !important;
  }
  .portfolio-card.pdf-expanded .portfolio-info {
    padding: 1rem 1.25rem !important;
  }
  .portfolio-card.pdf-expanded .portfolio-name {
    font-size: 1rem !important;
  }
  .portfolio-card.pdf-expanded .portfolio-org-type {
    font-size: 0.65rem !important;
  }
  .portfolio-card.pdf-expanded .portfolio-tags {
    margin-top: 0.3rem !important;
  }
  .portfolio-card.pdf-expanded .portfolio-tag {
    font-size: 0.65rem !important;
    padding: 0.1rem 0.4rem !important;
  }
  .portfolio-card.pdf-expanded .portfolio-desc {
    display: none !important;
  }
  .pdf-narrative {
    font-size: 0.82rem !important;
    line-height: 1.45 !important;
    color: #374151 !important;
    margin: 0.4rem 0 !important;
  }
  .pdf-relevance-label {
    font-size: 0.65rem !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    color: #0891B2 !important;
    margin-top: 0.5rem !important;
    margin-bottom: 0.2rem !important;
  }
  .pdf-relevance {
    font-size: 0.78rem !important;
    line-height: 1.4 !important;
    color: #6B7280 !important;
    margin-bottom: 0.4rem !important;
  }
  .pdf-refs {
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 0.35rem !important;
    margin-top: 0.5rem !important;
  }
  .pdf-ref-tag {
    display: inline-block !important;
    padding: 0.1rem 0.4rem !important;
    background: #F0F9FF !important;
    border: 1px solid #BAE6FD !important;
    border-radius: 3px !important;
    font-size: 0.6rem !important;
    color: #0369A1 !important;
    font-weight: 500 !important;
  }

  /* ---- Scope grid: 2 columns for PDF ---- */
  .scope-grid {
    grid-template-columns: repeat(2, 1fr) !important;
  }

  /* ---- Next Steps glassmorphism cards: make visible in print ---- */
  section[style*="linear-gradient"] div[style*="backdrop-filter"] {
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
    background: rgba(255,255,255,0.2) !important;
    border: 1px solid rgba(255,255,255,0.3) !important;
  }
</style>
"""


# ===========================================================================
#  Stage 2: Add branded footer to every page via PyMuPDF
# ===========================================================================
def add_branded_footer(pdf_path: Path):
    doc = fitz.open(str(pdf_path))
    total = len(doc)

    for i in range(total):
        page = doc[i]
        r = page.rect

        # Footer rectangle at the very bottom
        fr = fitz.Rect(0, r.height - FOOTER_H_PT, r.width, r.height)
        page.draw_rect(fr, color=None, fill=BLACK)

        # Vertical centre of footer strip
        cy = r.height - FOOTER_H_PT / 2 + 2

        # -- Left text --
        left_txt = "Inkline + Attention Strategy  \u00b7  Rideau Hall Foundation Website Consolidation  \u00b7  Confidential"
        page.insert_text(fitz.Point(28, cy), left_txt,
                         fontname="helv", fontsize=6.5, color=GREY_MED)

        # -- Right text: date saved + page number --
        version_ts = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        prefix = f"Version: {version_ts}  \u00b7  "
        pgtxt = f"Page {i + 1} of {total}"
        pw = fitz.get_text_length(prefix, fontname="helv", fontsize=6.5)
        gw = fitz.get_text_length(pgtxt, fontname="helv", fontsize=6.5)
        rx = r.width - 28 - pw - gw

        page.insert_text(fitz.Point(rx, cy), prefix,
                         fontname="helv", fontsize=6.5, color=GREY_MED)
        page.insert_text(fitz.Point(rx + pw, cy), pgtxt,
                         fontname="helv", fontsize=6.5, color=CERULEAN)

    # Save to temp then replace
    tmp_out = str(pdf_path) + ".tmp"
    doc.save(tmp_out, deflate=True)
    doc.close()
    Path(tmp_out).replace(pdf_path)
    print(f"  Footer added to {total} pages")


# ===========================================================================
#  Stage 3: Screenshot every page for visual QA
# ===========================================================================
def screenshot_pages(pdf_path: Path, out_dir: Path, dpi: int = 110):
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("page_*.png"):
        f.unlink()

    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    count = len(doc)
    for i in range(count):
        pix = doc[i].get_pixmap(matrix=mat)
        out = out_dir / f"page_{i + 1:02d}.png"
        pix.save(str(out))

    doc.close()
    print(f"  Saved {count} page screenshots to {out_dir}")


# ===========================================================================
#  Stage 1: Playwright PDF generation
# ===========================================================================
async def generate_pdf():
    print("=== Stage 1: Playwright PDF generation (8.5\u00d714 Legal) ===")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1100, "height": 900})

        # Load HTML from its actual location so relative paths (images, logos) work
        file_url = f"file://{HTML_PATH.resolve()}"
        print(f"  Loading: {file_url}")
        await page.goto(file_url, wait_until="networkidle", timeout=60000)

        print("  Waiting for page render...")
        await page.wait_for_timeout(3000)

        # Remove lazy loading from all images so they render in PDF
        lazy_fixed = await page.evaluate("""() => {
            let count = 0;
            document.querySelectorAll('img[loading="lazy"]').forEach(img => {
                img.removeAttribute('loading');
                // Force reload by re-setting src
                const src = img.src;
                img.src = '';
                img.src = src;
                count++;
            });
            return count;
        }""")
        print(f"  Removed lazy loading from {lazy_fixed} images")

        # Wait for local images to load
        await page.wait_for_timeout(2000)

        # Inject print CSS
        await page.evaluate("""(css) => {
            document.head.insertAdjacentHTML('beforeend', css);
        }""", PRINT_CSS)

        # Add page breaks before major sections only (not every section)
        marked = await page.evaluate("""() => {
            // IDs of sections that should start on a new page
            const breakIds = [
                'approach',        // Strategic Approach
                'ia',              // Information Architecture
                'seo',             // SEO & AEO
                'considerations',  // Key Considerations
                'scope',           // Technical Scope
                'effort',          // Effort & Investment
                'company',         // About Inkline
                'next-steps',      // Next Steps
            ];
            let marked = 0;
            breakIds.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.classList.add('pdf-section-break');
                    marked++;
                }
            });

            // Also break before the next-steps gradient section
            const nextSection = document.querySelector('section[style*="linear-gradient"]');
            if (nextSection && !nextSection.classList.contains('pdf-section-break')) {
                nextSection.classList.add('pdf-section-break');
                marked++;
            }

            return marked;
        }""")
        print(f"  Marked {marked} sections with page breaks")

        # Expand portfolio cards into hybrid layout with narrative + relevance
        # portfolioData is inside an IIFE, so extract it from the script tag text
        expanded = await page.evaluate("""() => {
            // portfolioData is in an IIFE — extract via brace counting
            let portfolioData = null;
            const scripts = document.querySelectorAll('script:not([src])');
            for (const s of scripts) {
                const txt = s.textContent;
                const idx = txt.indexOf('const portfolioData = {');
                if (idx < 0) continue;
                const startIdx = txt.indexOf('{', idx);
                let braceCount = 0;
                let endIdx = startIdx;
                for (let i = startIdx; i < txt.length; i++) {
                    if (txt[i] === '{') braceCount++;
                    if (txt[i] === '}') braceCount--;
                    if (braceCount === 0) { endIdx = i; break; }
                }
                const objStr = txt.substring(startIdx, endIdx + 1);
                try { portfolioData = eval('(' + objStr + ')'); } catch(e) { console.error(e); }
                break;
            }
            if (!portfolioData) return 0;
            const cards = document.querySelectorAll('.portfolio-card');
            let count = 0;
            cards.forEach(card => {
                const nameEl = card.querySelector('.portfolio-name');
                if (!nameEl) return;
                const name = nameEl.textContent.trim();
                const data = portfolioData[name];
                if (!data) return;

                card.classList.add('pdf-expanded');

                const info = card.querySelector('.portfolio-info');
                if (!info) return;

                // Add narrative
                const narDiv = document.createElement('div');
                narDiv.className = 'pdf-narrative';
                narDiv.textContent = data.narrative;
                info.appendChild(narDiv);

                // Add relevance label + text
                const relLabel = document.createElement('div');
                relLabel.className = 'pdf-relevance-label';
                relLabel.textContent = 'Relevance to RHF Project';
                info.appendChild(relLabel);

                const relDiv = document.createElement('div');
                relDiv.className = 'pdf-relevance';
                relDiv.textContent = data.relevance;
                info.appendChild(relDiv);

                // Add RFP reference tags
                if (data.refs && data.refs.length) {
                    const refsDiv = document.createElement('div');
                    refsDiv.className = 'pdf-refs';
                    data.refs.forEach(r => {
                        const tag = document.createElement('span');
                        tag.className = 'pdf-ref-tag';
                        tag.textContent = r;
                        refsDiv.appendChild(tag);
                    });
                    info.appendChild(refsDiv);
                }
                count++;
            });
            return count;
        }""")
        print(f"  Expanded {expanded} portfolio cards for PDF")

        print("  Rendering PDF...")
        await page.pdf(
            path=str(OUTPUT_PATH),
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            print_background=True,
            display_header_footer=False,
            margin={
                "top": MARGIN_TOP,
                "right": MARGIN_RIGHT,
                "bottom": MARGIN_BOTTOM,
                "left": MARGIN_LEFT,
            },
            prefer_css_page_size=False,
        )
        await browser.close()

    sz = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    print(f"  PDF: {OUTPUT_PATH.name}  ({sz:.1f} MB)")

    # --- Stage 2: Footer ---
    print("\n=== Stage 2: PyMuPDF branded footer ===")
    add_branded_footer(OUTPUT_PATH)

    # --- Stage 3: Screenshots ---
    print("\n=== Stage 3: Page screenshots for QA ===")
    screenshot_pages(OUTPUT_PATH, SCREENSHOT_DIR)

    final_sz = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    doc = fitz.open(str(OUTPUT_PATH))
    print(f"\n  Final: {len(doc)} pages, {final_sz:.1f} MB")
    print(f"  Page size: {doc[0].rect.width:.0f} x {doc[0].rect.height:.0f} pts "
          f"({doc[0].rect.width / 72:.1f}\" x {doc[0].rect.height / 72:.1f}\")")
    doc.close()


if __name__ == "__main__":
    asyncio.run(generate_pdf())
