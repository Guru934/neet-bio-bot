# NEET Master Bot

NEET Master Bot is a Telegram study assistant for NCERT Biology. It uses the
Google Gemini API to answer student questions from a selected NCERT chapter and
generate NEET-style multiple-choice quizzes. Chapter content is prepared from
local NCERT PDFs; student settings and quiz performance are stored in a local
SQLite database.

## Features

- Answers Biology questions using the active chapter, with automatic chapter
  detection when a chapter is mentioned.
- Generates standard MCQ quizzes and diagram-focused quizzes.
- Tracks quiz answers, accuracy, weak topics, XP, study streaks, and a
  leaderboard.
- Recommends review quizzes for topics with low accuracy.
- Sends scheduled daily quizzes in India Standard Time (IST).
- Supports interactive browsing of Class XI and Class XII chapters.

## Telegram commands

| Command | Description |
| --- | --- |
| `/start` | Start the bot and show the active chapter |
| `/help` | Show the command guide |
| `/chapters` | Browse the chapter menu |
| `/chapter` | Open the chapter menu; use `/chapter <name>` to select a chapter |
| `/11c<number>` or `/12c<number>` | Switch to a Class XI or XII chapter, such as `/12c5` |
| `/quiz [count]` | Generate a standard quiz (1-20 questions; default 5) |
| `/diagramquiz [count]` | Generate a diagram-focused quiz (1-10 questions; default 5) |
| `/review` | Quiz on weak topics from the active chapter |
| `/stats` | Show XP, level, streak, and overall accuracy |
| `/accuracy [chapter]` | Show accuracy for a chapter |
| `/weakness` | Show weak topics in the active chapter |
| `/leaderboard` | Show the top 10 users by XP |
| `/schedule [time]` | Schedule a daily quiz; defaults to 8:00 AM IST |
| `/unschedule` | Cancel your scheduled daily quiz |
| `/reset confirm` | Erase your performance data and settings |

You can also send a Biology question directly in chat. The `/broadcast <message>`
command is available only to the configured administrator.

## Requirements

- Python 3.10 or newer
- A Telegram bot token from [BotFather](https://t.me/BotFather)
- A Google Gemini API key
- Local NCERT Biology chapter PDFs that you are authorized to use
- `pdftotext` is optional; when installed, the extraction script uses it before
  falling back to `pypdf`

## Setup

1. Clone this repository and create a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows, activate it with `.venv\Scripts\activate`.

2. Install the Python dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Create a `.env` file in the project directory:

   ```dotenv
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   GEMINI_API_KEY=your_gemini_api_key
   ADMIN_TELEGRAM_ID=your_telegram_user_id
   ```

   `ADMIN_TELEGRAM_ID` is optional; without it, the admin-only broadcast
   command is unavailable. Keep this file private and never commit real API
   keys or tokens.

4. Prepare the chapter data. By default, `extract_ncert.py` looks for PDFs in
   `/home/guru/Documents/XI Biology/` and
   `/home/guru/Documents/XII Biology/`. Edit `SRC_CLASS_XI` and `SRC_CLASS_XII`
   near the top of that script to point to the directories containing your
   PDFs. The filenames should use the NCERT codes recognized in
   `CHAPTER_MAP` (for example, `kebo101.pdf` for Class XI Chapter 1 and
   `lebo105.pdf` for Class XII Chapter 5). Then run:

   ```bash
   python extract_ncert.py
   ```

   This creates `biology_store/index.json` and the local `biology_store/pdf/`
   and `biology_store/text/` chapter files. The generated content is local and
   is not included in this repository. `bot.py` requires the index and chapter
   files to be present before it starts.

5. Start the bot:

   ```bash
   python bot.py
   ```

The bot uses Telegram long polling, so keep the process running while you want
the bot to respond. Scheduled quizzes are managed by the running process and
are not persisted across restarts.

## Local data and privacy

The bot creates `neet_tracker.db` locally to store chat IDs, chapter choices,
quiz responses, XP, and streak data. Do not publish this database, your `.env`
file, logs, or generated chapter files. The leaderboard shares participating
users' XP and streaks with other users of the same bot.
