# Food Science MCQ Master (Windows)

## Quick start
1. Install Python 3.12 or 3.13 (64-bit) from https://www.python.org/downloads/windows/
2. Extract this ZIP to a folder you can write to, e.g. Documents\FoodScienceMCQ.
3. Double-click `Run Food Science MCQ Master.bat`.
4. The first run needs internet to install Python packages. Later runs can work offline as long as the packages remain installed.
5. A browser tab should open. If not, copy the local address shown in the terminal (usually http://localhost:8501) into Edge or Chrome.

## Import questions
- For selectable-text PDF: use Import Questions and preview the detected questions before importing.
- For CSV: download the template inside the app. Columns: chapter, question, A, B, C, D, correct, explanation.
- For JSON: either a list of question objects or {"questions": [...]}.
- Answer key must be A/B/C/D. Blank answers are not scored. The app does not invent answers.

## Important limitations
- PDF parsing is heuristic. Book layouts vary, so verify question/option boundaries and answers against your copy.
- Scanned PDFs need OCR; OCR is not included in this starter.
- The database `mcq_master.db` is created beside app.py and stores question bank and progress locally.
- Do not share or redistribute copyrighted question text extracted from your book. Use your lawful copy for personal study.
- This is a starter version; keep backups using Export / Backup and copy the database file when the app is closed.
