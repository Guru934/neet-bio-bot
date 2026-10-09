import os
import json
import shutil
import subprocess
from pathlib import Path
import pypdf
import pypdf.filters

# Disable pypdf decompression limits for safe local NCERT PDFs
pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH = 0

# ----------------- DIRECTORIES ----------------- #
BASE_DIR = Path(__file__).resolve().parent
SRC_CLASS_XI = Path("/home/guru/Documents/XI Biology/")
SRC_CLASS_XII = Path("/home/guru/Documents/XII Biology/")

DEST_BASE = BASE_DIR / "biology_store"
DEST_PDF = DEST_BASE / "pdf"
DEST_TEXT = DEST_BASE / "text"

# ----------------- NCERT CHAPTER MAPPINGS ----------------- #
CHAPTER_MAP = {
    # Class 11 (kebo101 - kebo119)
    "kebo101": {"class": 11, "num": 1, "title": "The Living World"},
    "kebo102": {"class": 11, "num": 2, "title": "Biological Classification"},
    "kebo103": {"class": 11, "num": 3, "title": "Plant Kingdom"},
    "kebo104": {"class": 11, "num": 4, "title": "Animal Kingdom"},
    "kebo105": {"class": 11, "num": 5, "title": "Morphology of Flowering Plants"},
    "kebo106": {"class": 11, "num": 6, "title": "Anatomy of Flowering Plants"},
    "kebo107": {"class": 11, "num": 7, "title": "Structural Organisation in Animals"},
    "kebo108": {"class": 11, "num": 8, "title": "Cell - The Unit of Life"},
    "kebo109": {"class": 11, "num": 9, "title": "Biomolecules"},
    "kebo110": {"class": 11, "num": 10, "title": "Cell Cycle and Cell Division"},
    "kebo111": {"class": 11, "num": 11, "title": "Photosynthesis in Higher Plants"},
    "kebo112": {"class": 11, "num": 12, "title": "Respiration in Plants"},
    "kebo113": {"class": 11, "num": 13, "title": "Plant Growth and Development"},
    "kebo114": {"class": 11, "num": 14, "title": "Breathing and Exchange of Gases"},
    "kebo115": {"class": 11, "num": 15, "title": "Body Fluids and Circulation"},
    "kebo116": {"class": 11, "num": 16, "title": "Excretory Products and their Elimination"},
    "kebo117": {"class": 11, "num": 17, "title": "Locomotion and Movement"},
    "kebo118": {"class": 11, "num": 18, "title": "Neural Control and Coordination"},
    "kebo119": {"class": 11, "num": 19, "title": "Chemical Coordination and Integration"},
    "kebo1ps":  {"class": 11, "num": 0, "title": "Class 11 Index and Prelims"},

    # Class 12 (lebo101 - lebo113)
    "lebo101": {"class": 12, "num": 1, "title": "Sexual Reproduction in Flowering Plants"},
    "lebo102": {"class": 12, "num": 2, "title": "Human Reproduction"},
    "lebo103": {"class": 12, "num": 3, "title": "Reproductive Health"},
    "lebo104": {"class": 12, "num": 4, "title": "Principles of Inheritance and Variation"},
    "lebo105": {"class": 12, "num": 5, "title": "Molecular Basis of Inheritance"},
    "lebo106": {"class": 12, "num": 6, "title": "Evolution"},
    "lebo107": {"class": 12, "num": 7, "title": "Human Health and Disease"},
    "lebo108": {"class": 12, "num": 8, "title": "Microbes in Human Welfare"},
    "lebo109": {"class": 12, "num": 9, "title": "Biotechnology - Principles and Processes"},
    "lebo110": {"class": 12, "num": 10, "title": "Biotechnology and its Applications"},
    "lebo111": {"class": 12, "num": 11, "title": "Organisms and Populations"},
    "lebo112": {"class": 12, "num": 12, "title": "Ecosystem"},
    "lebo113": {"class": 12, "num": 13, "title": "Biodiversity and Conservation"},
    "lebo1ps":  {"class": 12, "num": 0, "title": "Class 12 Index and Prelims"},
}

def clean_slug(title: str) -> str:
    """Creates a clean identifier string."""
    return title.lower().replace(" - ", "_").replace(" ", "_").replace("'", "")

def extract_pdf_text_fast(pdf_path: Path, cls_num: int, title: str) -> str:
    """Extracts text using pdftotext CLI (fast & robust), falling back to pypdf."""
    pdftotext_cmd = shutil.which("pdftotext")
    if pdftotext_cmd:
        try:
            res = subprocess.run(
                [pdftotext_cmd, str(pdf_path), "-"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            raw_pages = res.stdout.split("\x0c")
            # Filter out trailing empty page from last form feed
            if raw_pages and not raw_pages[-1].strip():
                raw_pages.pop()

            extracted_pages = []
            for idx, page_text in enumerate(raw_pages, start=1):
                extracted_pages.append(f"=== [NCERT Class {cls_num} | {title} | Page {idx}] ===\n{page_text.strip()}")
            return "\n\n".join(extracted_pages)
        except Exception as e:
            print(f"    [!] pdftotext failed ({e}), trying pypdf...")

    # Fallback to pypdf
    reader = pypdf.PdfReader(pdf_path)
    extracted_pages = []
    for idx, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        extracted_pages.append(f"=== [NCERT Class {cls_num} | {title} | Page {idx}] ===\n{page_text.strip()}")
    return "\n\n".join(extracted_pages)

def process_book(source_dir: Path, class_label: str, chapter_registry: dict):
    if not source_dir.exists():
        print(f"❌ Error: Directory not found -> {source_dir}")
        return

    print(f"\n==========================================")
    print(f"📂 Processing {class_label} Biology from: {source_dir}")
    print(f"==========================================")

    pdf_files = sorted(list(source_dir.glob("*.pdf")))
    if not pdf_files:
        print(f"⚠️ No PDF files found in {source_dir}")
        return

    for pdf_path in pdf_files:
        file_key = pdf_path.stem.lower()

        # Check if mapped, otherwise generate fallback
        if file_key in CHAPTER_MAP:
            info = CHAPTER_MAP[file_key]
            cls_num = info["class"]
            ch_num = info["num"]
            title = info["title"]
        else:
            cls_num = 11 if "xi" in class_label.lower() else 12
            ch_num = 99
            title = file_key

        prefix = f"class_{cls_num}_ch_{ch_num:02d}_" if ch_num > 0 else f"class_{cls_num}_"
        chapter_slug = f"{prefix}{clean_slug(title)}"

        target_pdf = DEST_PDF / f"{chapter_slug}.pdf"
        target_txt = DEST_TEXT / f"{chapter_slug}.txt"

        print(f"\n[+] Processing: {pdf_path.name} -> {chapter_slug}")

        # 1. Copy PDF if changed or doesn't exist
        if not target_pdf.exists() or target_pdf.stat().st_size != pdf_path.stat().st_size:
            shutil.copy2(pdf_path, target_pdf)
            print(f"    ✓ Copied PDF ({target_pdf.stat().st_size:,} bytes)")
        else:
            print(f"    ✓ PDF up-to-date")

        # 2. Extract Text
        try:
            full_text = extract_pdf_text_fast(pdf_path, cls_num, title)
            target_txt.write_text(full_text, encoding="utf-8")
            print(f"    ✓ Extracted to TXT ({len(full_text):,} chars)")
        except Exception as e:
            print(f"    ✗ Error extracting text from {pdf_path.name}: {e}")
            continue

        # Add to registry with project-relative and absolute paths
        chapter_registry[chapter_slug] = {
            "file_code": file_key,
            "class": cls_num,
            "chapter_number": ch_num,
            "title": title,
            "pdf_path": str(target_pdf.resolve()),
            "text_path": str(target_txt.resolve()),
            "rel_pdf_path": f"pdf/{target_pdf.name}",
            "rel_text_path": f"text/{target_txt.name}"
        }

def main():
    DEST_PDF.mkdir(parents=True, exist_ok=True)
    DEST_TEXT.mkdir(parents=True, exist_ok=True)

    chapter_registry = {}

    process_book(SRC_CLASS_XI, "Class XI", chapter_registry)
    process_book(SRC_CLASS_XII, "Class XII", chapter_registry)

    # Save index for bot lookup
    index_file = DEST_BASE / "index.json"
    index_file.write_text(json.dumps(chapter_registry, indent=2), encoding="utf-8")

    print("\n" + "="*50)
    print(f"✅ Extraction finished!")
    print(f"📁 Total processed chapters: {len(chapter_registry)}")
    print(f"📄 Output Directory: {DEST_BASE.resolve()}")
    print(f"🗂 Index file created: {index_file.resolve()}")
    print("="*50)

if __name__ == "__main__":
    main()