import os
import json
import logging
import sqlite3
import datetime
import re
import html
import time
import random
import asyncio
from pathlib import Path
from dotenv import load_dotenv

print("🔄 Initializing NEET Master Bot...", flush=True)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or GEMINI_API_KEY in .env")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("neet_master_bot")
logging.getLogger("google_genai").setLevel(logging.ERROR)

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)
from google import genai
from google.genai import types

ai_client = genai.Client(api_key=GEMINI_API_KEY)
BASE_DIR = Path(__file__).resolve().parent
STORE_DIR = BASE_DIR / "biology_store"
INDEX_PATH = STORE_DIR / "index.json"
DB_FILE = BASE_DIR / "neet_tracker.db"

# ----------------- ASCII MEMES ----------------- #
MEMES = {
    "perfect": [
        "╔═══════════════════╗\n║  5/5 PERFECT!     ║\n║  You absolute     ║\n║  LEGEND!          ║\n╚═══════════════════╝",
        "  ________________\n |  NOBODY:       |\n |  ABSOLUTELY    |\n |  NOBODY:       |\n |  YOU: 100%     |\n |________________|",
    ],
    "good": [
        "  ┌──────────────────┐\n  │  Great job!      │\n  │  ( ͡° ͜ʖ ͡°)       │\n  │  One more push!  │\n  └──────────────────┘",
        "  You right now:\n  ┌──────────────┐\n  │  BIG BRAIN   │\n  │    ENERGY    │\n  └──────────────┘"
    ],
    "bad": [
        "  When you study 3 hrs\n  and still get half right:\n  ┌──────────────────┐\n  │  (╥_╥)           │\n  │  Pain.           │\n  │  But real ones   │\n  │  get back up!    │\n  └──────────────────┘",
        "  ┌──────────────────────┐\n  │ ME: I know everything│\n  │ NEET: lol nice try   │\n  │ ───────────────────  │\n  │ /review exists!      │\n  └──────────────────────┘"
    ]
}

# ----------------- LOAD NCERT INDEX ----------------- #
if not INDEX_PATH.exists():
    raise FileNotFoundError(f"Could not find {INDEX_PATH}. Run extract_ncert.py first!")

with open(INDEX_PATH, "r", encoding="utf-8") as f:
    CHAPTER_INDEX = json.load(f)

CHAPTER_LOOKUP = {}
for slug, meta in CHAPTER_INDEX.items():
    slug_l = slug.lower()
    CHAPTER_LOOKUP[slug_l] = slug
    CHAPTER_LOOKUP[meta["file_code"].lower()] = slug
    CHAPTER_LOOKUP[meta["title"].lower()] = slug
    ch_num = meta["chapter_number"]
    cls_num = meta["class"]
    if ch_num > 0:
        CHAPTER_LOOKUP[f"class {cls_num} ch {ch_num}"] = slug
        CHAPTER_LOOKUP[f"class {cls_num} ch{ch_num}"] = slug
        CHAPTER_LOOKUP[f"{cls_num} ch {ch_num}"] = slug
        CHAPTER_LOOKUP[f"{cls_num} ch{ch_num}"] = slug
        CHAPTER_LOOKUP[f"{cls_num}c{ch_num}"] = slug
        if f"ch{ch_num}" not in CHAPTER_LOOKUP:
            CHAPTER_LOOKUP[f"ch{ch_num}"] = slug

# ----------------- DATABASE SETUP ----------------- #
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS student_settings (
                chat_id INTEGER PRIMARY KEY,
                active_chapter TEXT DEFAULT 'class_12_ch_05_molecular_basis_of_inheritance'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS performance_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                chapter TEXT,
                subtopic TEXT,
                is_correct INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS active_polls (
                poll_id TEXT PRIMARY KEY,
                chat_id INTEGER,
                chapter TEXT,
                subtopic TEXT,
                correct_option_id INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS recent_chapters (
                chat_id INTEGER,
                chapter_slug TEXT,
                used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, chapter_slug)
            )
        """)

        for col, col_type in [("xp", "INTEGER DEFAULT 0"),
                              ("streak_count", "INTEGER DEFAULT 0"),
                              ("last_quiz_date", "TEXT DEFAULT ''")]:
            try:
                c.execute(f"ALTER TABLE student_settings ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

init_db()

def get_active_chapter(chat_id: int) -> str:
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT active_chapter FROM student_settings WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        return row[0] if row else "class_12_ch_05_molecular_basis_of_inheritance"

def set_active_chapter(chat_id: int, chapter_slug: str):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO student_settings (chat_id, active_chapter)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET active_chapter=excluded.active_chapter
        """, (chat_id, chapter_slug))

        c.execute("""
            INSERT INTO recent_chapters (chat_id, chapter_slug, used_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, chapter_slug) DO UPDATE SET used_at=CURRENT_TIMESTAMP
        """, (chat_id, chapter_slug))
        conn.commit()

def get_weak_topics(chat_id: int, chapter_slug: str) -> list[str]:
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT subtopic,
                   SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct,
                   COUNT(*) as total
            FROM performance_logs
            WHERE chat_id = ? AND chapter = ?
            GROUP BY subtopic
            HAVING (CAST(correct AS FLOAT) / total) < 0.65
        """, (chat_id, chapter_slug))
        return [row[0] for row in c.fetchall()]

def clean_active_polls():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM active_polls WHERE rowid NOT IN (SELECT rowid FROM active_polls ORDER BY rowid DESC LIMIT 1000)")
        conn.commit()

# ----------------- PARSE HELPERS ----------------- #
def format_ai_to_html(text: str) -> str:
    safe_text = html.escape(text)
    safe_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', safe_text)
    safe_text = re.sub(r'^#+\s*', '• ', safe_text, flags=re.MULTILINE)
    return safe_text

def get_level_info(xp: int):
    if xp < 100: return "Beginner", xp, 100
    if xp < 500: return "Bio Explorer", xp - 100, 400
    if xp < 1500: return "NEET Warrior", xp - 500, 1000
    if xp < 3000: return "Rank Holder", xp - 1500, 1500
    if xp < 10000: return "Biology God", xp - 3000, 7000
    if xp < 50000: return "NEET Deity", xp - 10000, 40000
    return "Cellular Legend", xp - 50000, 999999

def parse_time_arg(time_str: str) -> tuple[int, int] | None:
    time_str = time_str.strip().lower()
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)$', time_str)
    if m:
        h, mn, period = int(m.group(1)), int(m.group(2)), m.group(3)
        if period == 'am' and h == 12: h = 0
        elif period == 'pm' and h != 12: h += 12
        return (h, mn) if 0 <= h <= 23 and 0 <= mn <= 59 else None

    m = re.match(r'^(\d{1,2})\s*(am|pm)$', time_str)
    if m:
        h, period = int(m.group(1)), m.group(2)
        if period == 'am' and h == 12: h = 0
        elif period == 'pm' and h != 12: h += 12
        return (h, 0) if 0 <= h <= 23 else None

    m = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return (h, mn) if 0 <= h <= 23 and 0 <= mn <= 59 else None

    return None

# ----------------- GEMINI CORE ----------------- #
_pdf_upload_cache = {}

async def post_init(application):
    logger.info("Setting bot commands menu...")
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show help menu"),
        BotCommand("chapters", "Browse chapter menu"),
        BotCommand("chapter", "Change active chapter"),
        BotCommand("quiz", "Take a standard quiz"),
        BotCommand("diagramquiz", "Take a diagram-based quiz"),
        BotCommand("review", "Re-quiz topics you previously missed"),
        BotCommand("stats", "Overall performance stats"),
        BotCommand("leaderboard", "View your Global Rank"),
        BotCommand("accuracy", "View chapter accuracy"),
        BotCommand("weakness", "View weak topics"),
        BotCommand("schedule", "Schedule daily drill"),
        BotCommand("unschedule", "Cancel daily drill"),
        BotCommand("reset", "Wipe all your performance data")
    ]
    await application.bot.set_my_commands(commands)

    logger.info("Syncing remote Gemini file cache...")
    try:
        pager = await ai_client.aio.files.list()
        count = 0
        async for f in pager:
            if f.display_name and f.state.name == "ACTIVE":
                _pdf_upload_cache[f.display_name] = f
                count += 1
        logger.info(f"Loaded {count} active cached PDF documents from Gemini.")
    except Exception as e:
        logger.warning(f"Could not load Gemini file cache: {e}")

async def get_or_upload_pdf(pdf_path: Path):
    cache_key = pdf_path.name
    if cache_key in _pdf_upload_cache:
        cached_file = _pdf_upload_cache[cache_key]
        try:
            f = await ai_client.aio.files.get(name=cached_file.name)
            if f.state.name == "ACTIVE":
                return f
        except Exception:
            pass

    logger.info(f"Uploading PDF to Gemini: {pdf_path.name}...")
    uploaded = await ai_client.aio.files.upload(
        file=str(pdf_path),
        config=types.UploadFileConfig(display_name=pdf_path.name)
    )
    _pdf_upload_cache[cache_key] = uploaded
    return uploaded

async def get_chapter_payload(chapter_slug: str, require_pdf: bool = False):
    meta = CHAPTER_INDEX.get(chapter_slug)
    if not meta:
        return None, None

    pdf_path = Path(meta.get("pdf_path", ""))
    if not pdf_path.exists():
        pdf_path = STORE_DIR / "pdf" / f"{chapter_slug}.pdf"

    txt_path = Path(meta.get("text_path", ""))
    if not txt_path.exists():
        txt_path = STORE_DIR / "text" / f"{chapter_slug}.txt"

    if require_pdf and pdf_path.exists():
        uploaded = await get_or_upload_pdf(pdf_path)
        return uploaded, "pdf"

    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8"), "text"
    elif pdf_path.exists():
        uploaded = await get_or_upload_pdf(pdf_path)
        return uploaded, "pdf"

    return None, None

def _clean_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()

async def generate_neet_quiz(chapter_slug: str, target_subtopics: list[str] = None, count: int = 5, require_diagrams: bool = False) -> list[dict]:
    meta = CHAPTER_INDEX.get(chapter_slug)
    if not meta:
        raise ValueError(f"Chapter '{chapter_slug}' not found in index.")

    source_obj, source_type = await get_chapter_payload(chapter_slug, require_pdf=require_diagrams)
    if not source_obj:
        raise FileNotFoundError(f"Content for {chapter_slug} not found.")

    visual_note = ""
    if require_diagrams or source_type == "pdf":
        visual_note = "Focus heavily on diagram interpretation, cycle flowcharts, anatomical labeling, and histological structures."

    target_note = f"Prioritize generating questions strictly targeting these subtopics: {', '.join(target_subtopics)}" if target_subtopics else ""

    prompt = f"""
You are the Chief Academic Mentor for NEET-UG Biology.
Using ONLY the provided NCERT source text/document for '{meta['title']}' (Class {meta['class']}), generate {count} standard NEET-UG MCQs.

CRITICAL NEET FORMAT CONSTRAINTS:
1. Strict NCERT line-to-line accuracy. Formats: Assertion-Reason, Statement I & Statement II, or Find the Incorrect Statement.
2. Exactly 4 options per question (0 to 3 index). Each option strictly UNDER 95 characters.
3. The 'question' text MUST be strictly UNDER 280 characters to comply with Telegram limits.
4. Explanation MUST state the exact NCERT concept and be strictly UNDER 190 characters (Telegram limits).
5. Include a concise subtopic name (e.g., 'Lac Operon', 'Spermatogenesis', 'Counter-Current Mechanism').
{visual_note}
{target_note}

Return ONLY valid JSON with this exact array structure:
[
  {{
    "question": "Which of the following is correct regarding...",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_option_id": 0,
    "explanation": "NCERT states that...",
    "subtopic": "Transcription"
  }}
]
"""
    contents = [source_obj, prompt] if source_type == "pdf" else [f"NCERT SOURCE:\n{source_obj[:150000]}", prompt]

    response = await ai_client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    cleaned = _clean_json_text(response.text)
    data = json.loads(cleaned)
    if isinstance(data, dict) and "questions" in data:
        data = data["questions"]

    validated_questions = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            q_text = str(item.get("question", "")).strip()
            if not q_text:
                continue
            if len(q_text) > 300:
                q_text = q_text[:297] + "..."

            opts = item.get("options", [])
            if not isinstance(opts, list) or len(opts) < 2:
                continue

            valid_opts = []
            for opt in opts[:4]:
                opt_str = str(opt).strip()[:100]
                if opt_str:
                    valid_opts.append(opt_str)
            if len(valid_opts) < 2:
                continue

            cid = item.get("correct_option_id", 0)
            if isinstance(cid, str):
                if cid.isdigit():
                    cid = int(cid)
                elif cid.upper() in ["A", "B", "C", "D"]:
                    cid = ord(cid.upper()) - ord("A")
                else:
                    cid = 0
            try:
                cid = int(cid)
            except (ValueError, TypeError):
                cid = 0
            if cid < 0 or cid >= len(valid_opts):
                cid = 0

            expl = str(item.get("explanation", "")).strip()
            if len(expl) > 195:
                expl = expl[:192] + "..."

            validated_questions.append({
                "question": q_text,
                "options": valid_opts,
                "correct_option_id": cid,
                "explanation": expl,
                "subtopic": str(item.get("subtopic", "General")).strip()
            })

    return validated_questions

# ----------------- TELEGRAM RATE LIMITER ----------------- #
def check_cooldown(chat_id: int, context: ContextTypes.DEFAULT_TYPE, seconds: int = 10) -> bool:
    if getattr(context, "user_data", None) is None:
        return True

    if "last_request" not in context.user_data:
        context.user_data["last_request"] = 0
    now = time.time()
    if now - context.user_data["last_request"] < seconds:
        return False
    context.user_data["last_request"] = now
    return True

# ----------------- TELEGRAM BOT HANDLERS ----------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO student_settings (chat_id, xp, streak_count, last_quiz_date)
            VALUES (?, 0, 0, '')
        """, (chat_id,))
        conn.commit()

    current_slug = get_active_chapter(chat_id)
    title = CHAPTER_INDEX.get(current_slug, {}).get("title", current_slug)

    welcome_text = (
        "🩺 <b>Welcome to your NEET Master AI Mentor!</b>\n\n"
        f"📖 <b>Current Active Chapter:</b>\n<code>{html.escape(title)}</code>\n\n"
        "💡 <b>Hint:</b> Tap the <b>Menu button</b> or type <code>/</code> to see all available commands!\n\n"
        "💡 <b>Have doubts?</b> Just type any concept or ask a question directly, and I'll explain it with NCERT references!\n"
        "<i>(Tip: I automatically detect chapter names in your questions!)</i>"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠️ <b>NEET Master Command Guide</b>\n\n"
        "<b>📖 Chapters</b>\n"
        "<code>/chapters</code> — View all class 11 & 12 chapters\n"
        "<code>/chapter</code> — View chapter menu & recent chapters\n\n"
        "<b>📝 Practice</b>\n"
        "<code>/quiz [count]</code> — Practice standard MCQs (max 20)\n"
        "<code>/diagramquiz</code> — Practice diagram & flowchart MCQs\n"
        "<code>/review</code> — Targeted quiz on your past mistakes\n\n"
        "<b>📊 Performance & Game</b>\n"
        "<code>/stats</code> — Check your Level, XP, Streak, and Accuracy!\n"
        "<code>/leaderboard</code> — View the Global Top 10 Rank\n"
        "<code>/weakness</code> — Identify topics &lt; 65% accuracy\n"
        "<code>/accuracy [chapter]</code> — Accuracy for a specific chapter\n\n"
        "<b>⏰ Settings</b>\n"
        "<code>/schedule [time]</code> — Enable daily drills (e.g. <code>/schedule 10pm</code>)\n"
        "<code>/unschedule</code> — Disable daily drills\n"
        "<code>/reset</code> — Wipe your performance data\n\n"
        "<i>You can also directly type any concept for an instant tutoring session!</i>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")

# ----------------- CHAPTER INTERACTIVE MENU ----------------- #
async def list_chapters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Act as an alias to the interactive chapter interface
    await change_chapter(update, context)

async def change_chapter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.effective_chat.id

    if not context.args:
        # Show interactive menu with fast InlineKeyboard UI
        current_slug = get_active_chapter(chat_id)
        current_title = CHAPTER_INDEX.get(current_slug, {}).get("title", current_slug)
        cls = CHAPTER_INDEX.get(current_slug, {}).get("class", "?")

        keyboard = [
            [
                InlineKeyboardButton("📘 Class XI", callback_data="menu_c11"),
                InlineKeyboardButton("📙 Class XII", callback_data="menu_c12")
            ],
            [InlineKeyboardButton("⏱️ Recent Chapters", callback_data="menu_recent")]
        ]

        await update.message.reply_text(
            f"📖 <b>Active:</b> Class {cls}: <code>{html.escape(current_title)}</code>\n\n"
            "<b>Choose a category to browse chapters:</b>\n"
            "<i>(Or search manually:</i> <code>/chapter genetics</code><i>)</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # User manually typed a search query via /chapter <query>
    query = " ".join(context.args).strip().lower()
    matched_slug = None

    for key, slug in sorted(CHAPTER_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True):
        if key in query:
            matched_slug = slug
            break

    if matched_slug:
        set_active_chapter(chat_id, matched_slug)
        title = CHAPTER_INDEX[matched_slug]["title"]
        cls = CHAPTER_INDEX[matched_slug]["class"]
        await update.message.reply_text(
            f"✅ Active Chapter updated to:\n<b>Class {cls}: {html.escape(title)}</b>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Chapter not recognized.", parse_mode="HTML")

async def chapter_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    if data == "menu_c11":
        lines = ["📘 <b>Class XI Chapters:</b>\n<i>Tap a code below to switch instantly</i>\n"]
        for slug, meta in sorted(CHAPTER_INDEX.items(), key=lambda x: x[1]['chapter_number']):
            if meta["class"] == 11 and meta["chapter_number"] > 0:
                lines.append(f"/11c{meta['chapter_number']} — {html.escape(meta['title'])}")

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_c12":
        lines = ["📙 <b>Class XII Chapters:</b>\n<i>Tap a code below to switch instantly</i>\n"]
        for slug, meta in sorted(CHAPTER_INDEX.items(), key=lambda x: x[1]['chapter_number']):
            if meta["class"] == 12 and meta["chapter_number"] > 0:
                lines.append(f"/12c{meta['chapter_number']} — {html.escape(meta['title'])}")

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_recent":
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT chapter_slug FROM recent_chapters WHERE chat_id = ? ORDER BY used_at DESC LIMIT 5", (chat_id,))
            recents = [row[0] for row in c.fetchall()]

        if not recents:
            text = "No recent chapters found in history."
        else:
            text = "⏱️ <b>Your Recent Chapters:</b>\n<i>Tap a code below to jump back</i>\n\n"
            for slug in recents:
                meta = CHAPTER_INDEX.get(slug, {})
                title = meta.get("title", slug)
                cls = meta.get("class", "?")
                ch = meta.get("chapter_number", 0)
                if ch > 0:
                    text += f"/{cls}c{ch} — {html.escape(title)}\n"

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_back":
        current_slug = get_active_chapter(chat_id)
        current_title = CHAPTER_INDEX.get(current_slug, {}).get("title", current_slug)
        cls = CHAPTER_INDEX.get(current_slug, {}).get("class", "?")

        keyboard = [
            [
                InlineKeyboardButton("📘 Class XI", callback_data="menu_c11"),
                InlineKeyboardButton("📙 Class XII", callback_data="menu_c12")
            ],
            [InlineKeyboardButton("⏱️ Recent Chapters", callback_data="menu_recent")]
        ]
        await query.edit_message_text(
            f"📖 <b>Active:</b> Class {cls}: <code>{html.escape(current_title)}</code>\n\n"
            "<b>Choose a category to browse chapters:</b>\n"
            "<i>(Or search manually:</i> <code>/chapter genetics</code><i>)</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def chapter_shortcut_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This securely matches raw user messages starting with /11cX or /12cXB
    if not update.message or not update.message.text: return
    chat_id = update.effective_chat.id
    cmd = update.message.text.strip().lower()

    m = re.match(r'^/(11|12)c(\d+)', cmd)
    if m:
        cls = int(m.group(1))
        ch = int(m.group(2))
        matched_slug = None
        for slug, meta in CHAPTER_INDEX.items():
            if meta["class"] == cls and meta["chapter_number"] == ch:
                matched_slug = slug
                break

        if matched_slug:
            set_active_chapter(chat_id, matched_slug)
            title = CHAPTER_INDEX[matched_slug]["title"]
            await update.message.reply_text(f"✅ Active Chapter updated to:\n<b>Class {cls}: {html.escape(title)}</b>", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Chapter shortcut not found.", parse_mode="HTML")

# ----------------- QUIZ & GAMEPLAY ----------------- #
async def send_quiz_batch(chat_id: int, context: ContextTypes.DEFAULT_TYPE, count: int = 5, diagrams: bool = False, target_subtopics: list = None):
    chapter_slug = get_active_chapter(chat_id)
    meta = CHAPTER_INDEX.get(chapter_slug)
    if not meta:
        await context.bot.send_message(chat_id=chat_id, text="Please select a valid chapter first.", parse_mode="HTML")
        return

    if not check_cooldown(chat_id, context, seconds=10):
        await context.bot.send_message(chat_id=chat_id, text="⏳ Please wait a few seconds before requesting another quiz.", parse_mode="HTML")
        return

    target_str = ", ".join(target_subtopics) if target_subtopics else "Full Chapter"

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔬 <b>Generating {count} NEET Questions...</b>\n"
            f"<i>{html.escape(meta['title'])}</i>\n"
            f"Targeting: <code>{html.escape(target_str)}</code>"
        ),
        parse_mode="HTML"
    )

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        questions = await generate_neet_quiz(chapter_slug, target_subtopics=target_subtopics, count=count, require_diagrams=diagrams)
    except Exception as e:
        logger.error(f"Quiz generation failed: {e}", exc_info=True)
        await status_msg.edit_text("⚠️ Something went wrong while generating questions. Please try again later.", parse_mode="HTML")
        return

    sent_count = 0
    for q in questions:
        try:
            poll_msg = await context.bot.send_poll(
                chat_id=chat_id,
                question=q["question"],
                options=q["options"],
                type="quiz",
                correct_option_id=q["correct_option_id"],
                explanation=q["explanation"],
                is_anonymous=False
            )

            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO active_polls (poll_id, chat_id, chapter, subtopic, correct_option_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (poll_msg.poll.id, chat_id, chapter_slug, q.get("subtopic", "General"), q["correct_option_id"]))
                conn.commit()
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send poll: {e}")

    if sent_count > 0:
        chat_data = context.application.chat_data.setdefault(chat_id, {})
        chat_data["current_quiz"] = {"total": sent_count, "answered": 0, "correct": 0}
        await status_msg.edit_text(f"✅ Served {sent_count} questions for <i>{html.escape(meta['title'])}</i>!\n💡 <i>Hint: Correct answers yield +10 XP! Check /stats</i>", parse_mode="HTML")
    else:
        await status_msg.edit_text("⚠️ Could not render quiz polls. Please try running <code>/quiz</code> again.", parse_mode="HTML")

async def manual_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    count = 5
    if context.args and context.args[0].isdigit():
        count = min(max(int(context.args[0]), 1), 20)
    await send_quiz_batch(update.effective_chat.id, context, count=count, diagrams=False)

async def diagram_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    count = 5
    if context.args and context.args[0].isdigit():
        count = min(max(int(context.args[0]), 1), 10)
    await send_quiz_batch(update.effective_chat.id, context, count=count, diagrams=True)

async def review_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    chat_id = update.effective_chat.id
    chapter_slug = get_active_chapter(chat_id)
    weaks = get_weak_topics(chat_id, chapter_slug)

    if not weaks:
        await update.message.reply_text("🎉 You don't have any weak topics to review for this chapter!", parse_mode="HTML")
        return

    await send_quiz_batch(chat_id, context, count=5, diagrams=False, target_subtopics=weaks[:3])

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.poll_answer:
        return
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_choice = answer.option_ids[0] if answer.option_ids else -1

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT chat_id, chapter, subtopic, correct_option_id FROM active_polls WHERE poll_id = ?", (poll_id,))
        row = c.fetchone()

        if row:
            chat_id, chapter, subtopic, correct_id = row
            is_correct = 1 if user_choice == correct_id else 0

            c.execute("""
                INSERT INTO performance_logs (chat_id, chapter, subtopic, is_correct)
                VALUES (?, ?, ?, ?)
            """, (chat_id, chapter, subtopic, is_correct))

            c.execute("DELETE FROM active_polls WHERE poll_id = ?", (poll_id,))

            c.execute("SELECT xp, streak_count, last_quiz_date FROM student_settings WHERE chat_id = ?", (chat_id,))
            s_row = c.fetchone()
            if s_row:
                xp, streak, last_date = s_row
                today = datetime.date.today()
                today_str = today.isoformat()

                if last_date != today_str:
                    yesterday_str = (today - datetime.timedelta(days=1)).isoformat()
                    if last_date == yesterday_str:
                        streak += 1
                    else:
                        streak = 1
                    last_date = today_str

                if is_correct:
                    xp += 10

                c.execute("""
                    UPDATE student_settings
                    SET xp = ?, streak_count = ?, last_quiz_date = ?
                    WHERE chat_id = ?
                """, (xp, streak, last_date, chat_id))
            conn.commit()

            chat_data = context.application.chat_data.setdefault(chat_id, {})
            quiz_key = "current_quiz"
            if quiz_key in chat_data:
                chat_data[quiz_key]["answered"] += 1
                if is_correct:
                    chat_data[quiz_key]["correct"] += 1

                if chat_data[quiz_key]["answered"] >= chat_data[quiz_key]["total"]:
                    total = chat_data[quiz_key]["total"]
                    correct = chat_data[quiz_key]["correct"]
                    pct = (correct / total) * 100

                    if pct == 100:
                        summary = f"🏆 PERFECT! {correct}/{total}!\nYou're built different! 💪"
                        meme = random.choice(MEMES["perfect"])
                    elif pct >= 80:
                        summary = f"🔥 Great job! {correct}/{total}\nAlmost there!"
                        meme = random.choice(MEMES["good"])
                    elif pct >= 50:
                        summary = f"📊 {correct}/{total}\nNot bad! Check <code>/weakness</code> to improve."
                        meme = random.choice(MEMES["good"])
                    else:
                        summary = f"💪 {correct}/{total}\nTough round! Run <code>/review</code> to target weak spots."
                        meme = random.choice(MEMES["bad"])

                    msg = f"{summary}\n\n<pre>{meme}</pre>"
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                    except Exception:
                        pass

                    del chat_data[quiz_key]

# ----------------- TEACHER Q&A ----------------- #
async def teacher_qa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    original_query = update.message.text.strip()
    query = original_query.lower()

    # Graceful fallback: If they type "11c2" without a slash, treat it as a chapter switch!
    m = re.match(r'^(11|12)c(\d+)$', query)
    if m:
        update.message.text = f"/{query}"
        return await chapter_shortcut_handler(update, context)

    if len(query) < 12 and "?" not in query:
        return

    chat_id = update.effective_chat.id

    detected_slug = None
    for key, slug in sorted(CHAPTER_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True):
        if key in query:
            detected_slug = slug
            break

    chapter_slug = detected_slug or get_active_chapter(chat_id)
    meta = CHAPTER_INDEX.get(chapter_slug)

    if not meta:
        await update.message.reply_text("Please set an active chapter first using /chapter.", parse_mode="HTML")
        return

    if getattr(context, "user_data", None) is not None:
        if not check_cooldown(chat_id, context, seconds=6):
            await update.message.reply_text("⏳ Give me a moment to think...", parse_mode="HTML")
            return

    visual_keywords = ["diagram", "figure", "label", "structure", "cycle", "chart", "pathway", "graph", "pedigree"]
    needs_pdf = any(word in query for word in visual_keywords)

    try:
        source_obj, source_type = await get_chapter_payload(chapter_slug, require_pdf=needs_pdf)
    except Exception as e:
        logger.error(f"Failed to fetch payload: {e}")
        await update.message.reply_text("We're having trouble accessing the chapter files right now.", parse_mode="HTML")
        return

    if not source_obj:
        await update.message.reply_text("Chapter content not available.", parse_mode="HTML")
        return

    if detected_slug and detected_slug != get_active_chapter(chat_id):
        await update.message.reply_text(f"📖 <i>Answering based on detected chapter: <b>{html.escape(meta['title'])}</b>...</i>", parse_mode="HTML")

    await update.message.reply_chat_action("typing")

    prompt = f"""
Role & Persona:
You are an elite, disciplined NEET-UG Biology Master Faculty.
Your job is to resolve this student's doubt with precision, strictly grounded in NCERT Biology for '{meta['title']}' (Class {meta['class']}).

Source & Grounding Rules:
• Rely strictly on official NCERT facts, lines, and data. Do NOT extrapolate or introduce irrelevant MBBS-level trivia.
• If the question belongs to a completely different chapter, answer the core fact concisely in 1-2 lines, then advise: "For in-depth details, switch to that chapter using /chapter <name>."
• Never hallucinate or assume facts outside NCERT.

Formatting Guidelines (Telegram Mobile Optimized):
• Do NOT use markdown headers (# or ##).
• You MAY use bold text (**keyword**) only to highlight crucial scientific names, enzymes, or values.
• Use '•' for bullet points. Avoid nested indentations.
• Keep answers concise, high-yield, and easy to read on a mobile screen.

Pedagogical Structure:
1. Core NCERT Answer: Direct, crisp answer referencing the exact NCERT concept or figure landmark.
2. NEET Trap Alert (if applicable): Highlight common trick questions or wordplay NTA uses (e.g., "all vs most", "promoter vs terminator polarity").
3. Quick Mnemonic / Recall Hack (if helpful): A memory anchor to retain the fact.

Active Chapter: {meta['title']} (NCERT Class {meta['class']})
Student Doubt: {original_query}
"""
    contents = [source_obj, prompt] if source_type == "pdf" else [f"NCERT CONTENT:\n{source_obj[:120000]}", prompt]

    try:
        response = await ai_client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(temperature=0.1)
        )
        reply = format_ai_to_html(response.text or "I'm sorry, I couldn't form an answer for that.")

        chunk_size = 4000
        for i in range(0, len(reply), chunk_size):
            await update.message.reply_text(reply[i:i + chunk_size], parse_mode="HTML")

    except Exception as e:
        logger.error(f"QA Failed: {e}", exc_info=True)
        await update.message.reply_text("Sorry, I ran into a technical issue generating your answer. Please try again later.", parse_mode="HTML")

# ----------------- ADMIN & GLOBAL ----------------- #
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or str(update.effective_user.id) != str(ADMIN_ID):
        return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT chat_id FROM student_settings")
        users = c.fetchall()

    sent = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u[0], text=f"📢 <b>Announcement:</b>\n{html.escape(message)}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05) # Prevent flood limits
        except Exception:
            pass

    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")

async def get_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT chat_id, xp, streak_count FROM student_settings WHERE xp > 0 ORDER BY xp DESC LIMIT 10")
        top_users = c.fetchall()

        c.execute("SELECT COUNT(*) FROM student_settings WHERE xp > (SELECT xp FROM student_settings WHERE chat_id = ?)", (chat_id,))
        rank_row = c.fetchone()
        user_rank = rank_row[0] + 1 if rank_row else "?"

    if not top_users:
        await update.message.reply_text("No leaderboard data yet. Start studying!", parse_mode="HTML")
        return

    msg = "🏆 <b>NEET Global Leaderboard</b> 🏆\n\n"
    for i, u in enumerate(top_users, 1):
        uid, xp, streak = u
        marker = "⭐️ <b>(You)</b>" if uid == chat_id else "👤 Scholar"
        msg += f"<b>#{i}.</b> {marker} — <b>{xp} XP</b> (🔥{streak})\n"

    msg += f"\n👉 <b>Your Global Rank: #{user_rank}</b>"
    await update.message.reply_text(msg, parse_mode="HTML")

# ----------------- SCHEDULED DRILL ----------------- #
async def scheduled_quiz_job(context: ContextTypes.DEFAULT_TYPE):
    if not context.job:
        return
    chat_id = context.job.chat_id
    await send_quiz_batch(chat_id, context, count=10, diagrams=False)

async def set_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message or not context.job_queue:
        return
    chat_id = update.effective_chat.id
    job_name = f"daily_drill_{chat_id}"

    existing_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in existing_jobs:
        job.schedule_removal()

    hour, minute = 8, 0
    if context.args:
        parsed = parse_time_arg(context.args[0])
        if parsed is not None:
            hour, minute = parsed
        else:
            await update.message.reply_text(
                "Could not understand that time.\n\n"
                "Use formats like:\n"
                "<code>/schedule 10am</code>\n"
                "<code>/schedule 10:30pm</code>\n"
                "<code>/schedule 22:00</code>\n\n"
                "Or just <code>/schedule</code> for default 8:00 AM IST.",
                parse_mode="HTML"
            )
            return

    ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    target_time = datetime.time(hour=hour, minute=minute, second=0, tzinfo=ist_tz)

    context.job_queue.run_daily(
        scheduled_quiz_job,
        time=target_time,
        chat_id=chat_id,
        name=job_name
    )

    if hour == 0: display = f"12:{minute:02d} AM"
    elif hour < 12: display = f"{hour}:{minute:02d} AM"
    elif hour == 12: display = f"12:{minute:02d} PM"
    else: display = f"{hour-12}:{minute:02d} PM"

    await update.message.reply_text(
        f"⏰ <b>Daily drill scheduled for {display} IST every day!</b>\n"
        "Use <code>/unschedule</code> to turn this off.",
        parse_mode="HTML"
    )

async def unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not context.job_queue:
        return
    chat_id = update.effective_chat.id
    job_name = f"daily_drill_{chat_id}"

    existing_jobs = context.job_queue.get_jobs_by_name(job_name)
    if not existing_jobs:
        await update.message.reply_text("You don't currently have a daily drill scheduled.", parse_mode="HTML")
        return

    for job in existing_jobs:
        job.schedule_removal()
    await update.message.reply_text("🚫 <b>Daily drill cancelled.</b>", parse_mode="HTML")

# ----------------- MISC ----------------- #
async def get_accuracy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chapter_slug = get_active_chapter(chat_id)

    if context.args:
        query = " ".join(context.args).strip().lower()
        matched_slug = None
        for key, slug in CHAPTER_LOOKUP.items():
            if query in key:
                matched_slug = slug
                break
        if matched_slug:
            chapter_slug = matched_slug

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT SUM(is_correct), COUNT(*)
            FROM performance_logs
            WHERE chat_id = ? AND chapter = ?
        """, (chat_id, chapter_slug))
        row = c.fetchone()

    meta = CHAPTER_INDEX.get(chapter_slug)
    title = meta['title'] if meta else chapter_slug

    if not row or row[1] == 0:
        await update.message.reply_text(f"📉 You haven't attempted any questions for <b>{html.escape(title)}</b> yet.", parse_mode="HTML")
        return

    correct, total = row
    accuracy = (correct / total) * 100

    msg = f"📊 <b>Accuracy for {html.escape(title)}</b>\n"
    msg += f"Correct: {correct} / {total}\n"
    msg += f"Score: <b>{accuracy:.1f}%</b>"
    await update.message.reply_text(msg, parse_mode="HTML")

async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT SUM(is_correct), COUNT(*)
            FROM performance_logs
            WHERE chat_id = ?
        """, (chat_id,))
        p_row = c.fetchone()

        c.execute("SELECT xp, streak_count FROM student_settings WHERE chat_id = ?", (chat_id,))
        s_row = c.fetchone()

    total = p_row[1] if p_row and p_row[1] else 0
    correct = p_row[0] if p_row and p_row[0] else 0
    acc = (correct / total * 100) if total > 0 else 0.0

    xp = s_row[0] if s_row else 0
    streak = s_row[1] if s_row else 0

    lvl_name, curr_prog, lvl_cap = get_level_info(xp)
    bars = int((curr_prog / lvl_cap) * 10)
    prog_bar = "█" * bars + "░" * (10 - bars)

    msg = f"🏆 <b>Your Profile & Stats</b>\n\n"
    msg += f"🏅 <b>Level:</b> {lvl_name}\n"
    msg += f"✨ <b>XP:</b> <code>{prog_bar}</code> ({curr_prog}/{lvl_cap})\n"
    msg += f"🔥 <b>Streak:</b> {streak} Days\n\n"
    msg += f"📈 <b>Accuracy:</b> {acc:.1f}%\n"
    msg += f"✅ <b>Total Questions Solved:</b> {total}\n\n"
    msg += "<i>Tip: Earn 10 XP for every correct answer! Build your streak by playing daily.</i>"

    await update.message.reply_text(msg, parse_mode="HTML")

async def reset_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text("⚠️ <b>Warning</b>\nThis will wipe all your performance logs, XP, Streaks, and history. To proceed, type:\n<code>/reset confirm</code>", parse_mode="HTML")
        return

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM performance_logs WHERE chat_id = ?", (chat_id,))
        c.execute("DELETE FROM active_polls WHERE chat_id = ?", (chat_id,))
        c.execute("DELETE FROM student_settings WHERE chat_id = ?", (chat_id,))
        c.execute("DELETE FROM recent_chapters WHERE chat_id = ?", (chat_id,))
        c.execute("""
            INSERT OR IGNORE INTO student_settings (chat_id, xp, streak_count, last_quiz_date)
            VALUES (?, 0, 0, '')
        """, (chat_id,))
        conn.commit()

    await update.message.reply_text("✅ All your performance, XP, and settings data has been erased.", parse_mode="HTML")

async def view_weakness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    chapter_slug = get_active_chapter(chat_id)
    meta = CHAPTER_INDEX.get(chapter_slug)
    if not meta:
        return

    weaks = get_weak_topics(chat_id, chapter_slug)
    if not weaks:
        await update.message.reply_text(
            f"🎉 <b>No critical weak topics recorded for:</b>\n<code>{html.escape(meta['title'])}</code>\n\nKeep solving quizzes to calibrate!",
            parse_mode="HTML"
        )
        return

    msg = f"⚠️ <b>Diagnosis for <code>{html.escape(meta['title'])}</code> (&lt;65% Accuracy):</b>\n\n"
    for w in weaks:
        msg += f"• <code>{html.escape(w)}</code>\n"
    msg += "\n💡 <b>Prescription:</b> Focus revision here, or run <code>/review</code>."
    await update.message.reply_text(msg, parse_mode="HTML")

# ----------------- MAIN ----------------- #
def create_app():
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("chapters", list_chapters))
    app.add_handler(CommandHandler("chapter", change_chapter))
    app.add_handler(MessageHandler(filters.Regex(re.compile(r'^/(11|12)c(\d+)', re.IGNORECASE)), chapter_shortcut_handler))
    app.add_handler(CallbackQueryHandler(chapter_menu_callback))
    app.add_handler(CommandHandler("quiz", manual_quiz))
    app.add_handler(CommandHandler("diagramquiz", diagram_quiz))
    app.add_handler(CommandHandler("review", review_quiz))
    app.add_handler(CommandHandler("stats", get_stats))
    app.add_handler(CommandHandler("leaderboard", get_leaderboard))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("accuracy", get_accuracy))
    app.add_handler(CommandHandler("weakness", view_weakness))
    app.add_handler(CommandHandler("schedule", set_schedule))
    app.add_handler(CommandHandler("unschedule", unschedule))
    app.add_handler(CommandHandler("reset", reset_data))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, teacher_qa))

    return app

def main():
    print("📡 Connecting to Telegram Bot API...", flush=True)
    app = create_app()
    print(f"🩺 NEET Master Bot is running! Loaded {len(CHAPTER_INDEX)} chapters from index.json. Polling for updates...", flush=True)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()