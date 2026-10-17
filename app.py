import io
import json
import random
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd
import fitz  # PyMuPDF

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "mcq_master.db"
st.set_page_config(page_title="Food Science MCQ Master", page_icon="📚", layout="wide")

# ---------- Database ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter TEXT NOT NULL DEFAULT 'Uncategorised',
            question TEXT NOT NULL,
            option_a TEXT NOT NULL DEFAULT '',
            option_b TEXT NOT NULL DEFAULT '',
            option_c TEXT NOT NULL DEFAULT '',
            option_d TEXT NOT NULL DEFAULT '',
            correct TEXT NOT NULL DEFAULT '',
            explanation TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            source_page INTEGER,
            UNIQUE(question, option_a, option_b, option_c, option_d)
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            chosen TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            mode TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            FOREIGN KEY(question_id) REFERENCES questions(id)
        );
        CREATE TABLE IF NOT EXISTS bookmarks (
            question_id INTEGER PRIMARY KEY,
            FOREIGN KEY(question_id) REFERENCES questions(id)
        );
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            total INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            completed_at TEXT NOT NULL
        );
        """)
init_db()

def q_count():
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]

def load_questions(chapter=None):
    with get_conn() as c:
        if chapter and chapter != "All chapters":
            rows = c.execute("SELECT * FROM questions WHERE chapter=? ORDER BY id", (chapter,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM questions ORDER BY id").fetchall()
    return [dict(r) for r in rows]

def chapters():
    with get_conn() as c:
        rows = c.execute("SELECT DISTINCT chapter FROM questions ORDER BY chapter").fetchall()
    return [r[0] for r in rows]

def save_question(q):
    with get_conn() as c:
        c.execute("""INSERT OR IGNORE INTO questions
        (chapter, question, option_a, option_b, option_c, option_d, correct, explanation, source, source_page)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (q.get("chapter","Uncategorised"), q["question"].strip(),
         q.get("A","").strip(), q.get("B","").strip(), q.get("C","").strip(),
         q.get("D","").strip(), q.get("correct","").strip().upper(),
         q.get("explanation","").strip(), q.get("source",""), q.get("source_page")))
        return c.total_changes

def record_attempt(qid, chosen, is_correct, mode):
    with get_conn() as c:
        c.execute("INSERT INTO attempts(question_id, chosen, is_correct, mode, attempted_at) VALUES(?,?,?,?,?)",
                  (qid, chosen, int(is_correct), mode, datetime.now().isoformat(timespec="seconds")))

def is_bookmarked(qid):
    with get_conn() as c:
        return c.execute("SELECT 1 FROM bookmarks WHERE question_id=?", (qid,)).fetchone() is not None

def toggle_bookmark(qid):
    with get_conn() as c:
        if c.execute("SELECT 1 FROM bookmarks WHERE question_id=?", (qid,)).fetchone():
            c.execute("DELETE FROM bookmarks WHERE question_id=?", (qid,))
        else:
            c.execute("INSERT OR IGNORE INTO bookmarks(question_id) VALUES(?)", (qid,))

def parse_pdf(uploaded_file):
    """Best-effort parser for selectable-text PDFs. Review imported rows before relying on them."""
    data = uploaded_file.getvalue()
    doc = fitz.open(stream=data, filetype="pdf")
    extracted = []
    for page_no, page in enumerate(doc, start=1):
        txt = page.get_text("text")
        if txt.strip():
            extracted.append((page_no, txt))
    return extracted

def parse_mcqs(text, chapter, source, page_no):
    """
    Best-effort parser for formats such as:
    1. Question text?
    (A) option / A. option / a) option
    Answer: B
    Explanation: ...
    This cannot reliably parse every book layout. Review the preview.
    """
    normalized = text.replace("\u00a0", " ")
    # Split at question numbers at line starts.
    chunks = re.split(r"(?m)^\s*(?:Q(?:uestion)?\s*)?(\d{1,4})\s*[\).:-]\s+", normalized)
    results = []
    if len(chunks) < 3:
        return results
    for i in range(1, len(chunks), 2):
        if i + 1 >= len(chunks):
            continue
        body = chunks[i + 1].strip()
        # Keep only content before next obvious section heading if no options.
        opt_matches = list(re.finditer(r"(?im)^\s*[\(\[]?([A-D])[\)\].:-]\s*", body))
        if len(opt_matches) < 2:
            continue
        question = body[:opt_matches[0].start()].strip()
        question = re.sub(r"\s+", " ", question)
        options = {}
        for j, m in enumerate(opt_matches):
            start = m.end()
            end = opt_matches[j + 1].start() if j + 1 < len(opt_matches) else len(body)
            value = body[start:end].strip()
            value = re.split(r"(?im)^\s*(?:answer|correct answer|ans\.?|explanation|solution)\s*[:\-]", value)[0]
            options[m.group(1).upper()] = re.sub(r"\s+", " ", value).strip()
        ans_match = re.search(r"(?im)^\s*(?:correct\s+)?(?:answer|ans\.?)\s*[:\-]\s*[\(\[]?([A-D])", body)
        exp_match = re.search(r"(?ims)^\s*(?:explanation|solution)\s*[:\-]\s*(.+)$", body)
        # Trim question noise if the first option was not actually an option line.
        if len(question) < 5 or not options:
            continue
        results.append({
            "chapter": chapter or "Uncategorised",
            "question": question,
            "A": options.get("A",""), "B": options.get("B",""),
            "C": options.get("C",""), "D": options.get("D",""),
            "correct": ans_match.group(1).upper() if ans_match else "",
            "explanation": exp_match.group(1).strip() if exp_match else "",
            "source": source, "source_page": page_no
        })
    return results

def import_json_or_csv(file):
    name = file.name.lower()
    if name.endswith(".json"):
        data = json.loads(file.getvalue().decode("utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("questions", [])
        return [normalize_import_row(x, file.name) for x in data]
    df = pd.read_csv(file)
    return [normalize_import_row(row, file.name) for row in df.fillna("").to_dict(orient="records")]

def normalize_import_row(row, source):
    # Accept headers: question, A/B/C/D or option_a/option_b/etc, correct/answer, chapter, explanation
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    def get(*keys, default=""):
        for k in keys:
            if k in lower and str(lower[k]).strip():
                return str(lower[k]).strip()
        return default
    answer = get("correct","answer","correct_answer","answer_key").upper()
    answer_match = re.search(r"\b([ABCD])\b", answer)
    return {
        "chapter": get("chapter", default="Uncategorised"),
        "question": get("question","question_text","mcq"),
        "A": get("a","option_a","option a"),
        "B": get("b","option_b","option b"),
        "C": get("c","option_c","option c"),
        "D": get("d","option_d","option d"),
        "correct": answer_match.group(1) if answer_match else "",
        "explanation": get("explanation","solution"),
        "source": source,
        "source_page": None,
    }

def save_import_batch(items):
    saved = 0
    for item in items:
        if item.get("question","").strip():
            before = q_count()
            save_question(item)
            if q_count() > before:
                saved += 1
    return saved

def show_question(q, key_prefix, mode):
    st.markdown(f"**{q['chapter']}** · Question ID {q['id']}")
    st.subheader(q["question"])
    options = {letter: q.get(f"option_{letter.lower()}","") for letter in "ABCD"}
    options = {k:v for k,v in options.items() if v.strip()}
    if not options:
        st.warning("This question has no options. Edit the question in Manage Questions.")
        return None
    choice = st.radio("Choose one answer", list(options.keys()),
                      format_func=lambda k: f"{k}. {options[k]}",
                      key=f"{key_prefix}_choice_{q['id']}", index=None)
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🔖 " + ("Remove bookmark" if is_bookmarked(q["id"]) else "Bookmark"),
                     key=f"{key_prefix}_bookmark_{q['id']}"):
            toggle_bookmark(q["id"])
            st.rerun()
    with c2:
        st.caption(f"Source: {q.get('source') or 'Not specified'}" +
                   (f", page {q['source_page']}" if q.get("source_page") else ""))
    return choice

# ---------- Sidebar ----------
st.sidebar.title("📚 Food Science MCQ Master")
page = st.sidebar.radio("Navigation", [
    "Dashboard", "Import Questions", "Chapter Practice", "Timed Mock Test",
    "Wrong Answers", "Bookmarks", "Manage Questions", "Export / Backup"
])
st.sidebar.caption("Local-first study app · Your database stays in this folder")
st.sidebar.metric("Questions in bank", q_count())

# ---------- Dashboard ----------
if page == "Dashboard":
    st.title("📚 Food Science MCQ Master")
    st.write("Your personal practice desk for Food Science and Food Technology.")
    with get_conn() as c:
        attempts = c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        correct = c.execute("SELECT COUNT(*) FROM attempts WHERE is_correct=1").fetchone()[0]
        wrong_qs = c.execute("SELECT COUNT(DISTINCT question_id) FROM attempts WHERE is_correct=0").fetchone()[0]
        tests = c.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
    cols = st.columns(4)
    cols[0].metric("Question bank", q_count())
    cols[1].metric("Answers submitted", attempts)
    cols[2].metric("Overall accuracy", f"{(100*correct/attempts):.1f}%" if attempts else "—")
    cols[3].metric("Mock tests completed", tests)
    st.info("Start with Import Questions to add a PDF, CSV or JSON question bank. PDF extraction is best-effort; check the preview and verify the original answer key.")
    if q_count():
        st.subheader("Your chapters")
        for ch in chapters():
            st.write(f"• {ch}: {len(load_questions(ch))} questions")
    st.subheader("Privacy")
    st.write(f"Question bank and progress are saved in `{DB_PATH.name}` beside this app. Do not put copyrighted book files in a shared folder or distribute extracted questions.")

# ---------- Import ----------
elif page == "Import Questions":
    st.title("Import your question bank")
    st.write("Import a selectable-text PDF, or a reviewed CSV/JSON file. The PDF parser is a starting point, not a guarantee.")
    with st.expander("Recommended CSV format", expanded=False):
        st.code("chapter,question,A,B,C,D,correct,explanation\nFood Chemistry,Which vitamin is fat-soluble?,Vitamin C,Vitamin A,Vitamin B1,Vitamin B12,B,")
        st.write("The `correct` field should contain A, B, C or D. Leave it blank if the original answer key does not provide an answer.")
        st.download_button("Download CSV template",
            data="chapter,question,A,B,C,D,correct,explanation\nFood Chemistry,Example question?,Option 1,Option 2,Option 3,Option 4,B,Example explanation\n",
            file_name="mcq_template.csv", mime="text/csv")
    uploaded = st.file_uploader("Choose PDF, CSV or JSON", type=["pdf","csv","json"])
    chapter = st.text_input("Chapter / topic label for this import", value="Uncategorised")
    if uploaded:
        if uploaded.name.lower().endswith(".pdf"):
            if st.button("Extract MCQs from PDF"):
                with st.spinner("Reading PDF text…"):
                    try:
                        pages = parse_pdf(uploaded)
                        if not pages:
                            st.error("No selectable text found. This may be a scanned PDF; OCR is not included in this first version.")
                        else:
                            found = []
                            for page_no, text in pages:
                                found.extend(parse_mcqs(text, chapter.strip() or "Uncategorised", uploaded.name, page_no))
                            st.session_state["pending_import"] = found
                            st.session_state["pending_source"] = uploaded.name
                            st.success(f"Found {len(found)} possible MCQs. Review the preview before importing.")
                    except Exception as e:
                        st.error(f"Could not read this PDF: {e}")
        else:
            if st.button("Load CSV / JSON for review"):
                try:
                    uploaded.seek(0)
                    rows = import_json_or_csv(uploaded)
                    st.session_state["pending_import"] = rows
                    st.session_state["pending_source"] = uploaded.name
                    st.success(f"Loaded {len(rows)} rows. Review before importing.")
                except Exception as e:
                    st.error(f"Could not read file: {e}")
    pending = st.session_state.get("pending_import")
    if pending is not None:
        st.subheader("Import preview")
        if not pending:
            st.warning("No MCQs were detected. Try the CSV template or adjust the PDF layout/parser.")
        else:
            preview = []
            for x in pending[:100]:
                preview.append({"chapter":x.get("chapter"),"question":x.get("question"),
                                "A":x.get("A"),"B":x.get("B"),"C":x.get("C"),"D":x.get("D"),
                                "correct":x.get("correct") or "(not found)","page":x.get("source_page")})
            st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
            st.caption("Showing up to 100 rows. Verify question boundaries, options and answers against your copy of the book.")
            if st.button("Import these questions into my local bank", type="primary"):
                count = save_import_batch(pending)
                st.success(f"Imported {count} new questions. Existing duplicates were skipped.")
                st.session_state.pop("pending_import", None)
                st.rerun()

# ---------- Chapter practice ----------
elif page == "Chapter Practice":
    st.title("Chapter-wise practice")
    if not q_count():
        st.warning("Your question bank is empty. Go to Import Questions first.")
    else:
        choices = ["All chapters"] + chapters()
        selected_ch = st.selectbox("Choose chapter", choices)
        pool = load_questions(selected_ch)
        st.write(f"{len(pool)} questions available")
        if "practice_order" not in st.session_state or st.session_state.get("practice_chapter") != selected_ch:
            st.session_state["practice_order"] = [q["id"] for q in pool]
            st.session_state["practice_pos"] = 0
            st.session_state["practice_chapter"] = selected_ch
        if st.button("Shuffle question order"):
            random.shuffle(st.session_state["practice_order"])
            st.session_state["practice_pos"] = 0
        order = st.session_state["practice_order"]
        pos = st.session_state["practice_pos"]
        by_id = {q["id"]:q for q in pool}
        if not order:
            st.info("No questions in this selection.")
        elif pos >= len(order):
            st.success("You've reached the end of this practice set.")
            if st.button("Start again"):
                st.session_state["practice_pos"] = 0
                st.rerun()
        else:
            q = by_id.get(order[pos])
            if q:
                st.progress((pos+1)/len(order), text=f"Question {pos+1} of {len(order)}")
                choice = show_question(q, "practice", "practice")
                if st.button("Check answer", type="primary", key="practice_check"):
                    if not choice:
                        st.warning("Select an option first.")
                    else:
                        correct = q.get("correct","")
                        if correct:
                            is_correct = choice == correct
                            record_attempt(q["id"], choice, is_correct, "practice")
                            st.session_state["practice_feedback"] = (q["id"], choice, is_correct)
                        else:
                            st.session_state["practice_feedback"] = (q["id"], choice, None)
                        st.rerun()
                fb = st.session_state.get("practice_feedback")
                if fb and fb[0] == q["id"]:
                    if fb[2] is None:
                        st.warning("No answer key was imported for this question. Check your book's original answer key; this response was not scored.")
                    elif fb[2]:
                        st.success("Correct!")
                    else:
                        st.error(f"Not quite. The recorded answer is {q['correct']}.")
                    if q.get("explanation"):
                        st.info(q["explanation"])
                    if st.button("Next question →", key="practice_next"):
                        st.session_state["practice_pos"] += 1
                        st.session_state.pop("practice_feedback", None)
                        st.rerun()

# ---------- Timed mock test ----------
elif page == "Timed Mock Test":
    st.title("Timed mock test")
    pool = load_questions()
    if not pool:
        st.warning("Import questions before starting a mock test.")
    else:
        with st.form("mock_setup"):
            n = st.number_input("Number of questions", min_value=1, max_value=max(1,len(pool)), value=min(25,len(pool)), step=1)
            mins = st.number_input("Time limit (minutes)", min_value=1, max_value=300, value=30, step=5)
            chapter_filter = st.selectbox("Question pool", ["All chapters"] + chapters())
            start = st.form_submit_button("Start mock test")
        if start:
            test_pool = load_questions(chapter_filter)
            if not test_pool:
                st.error("No questions in that chapter.")
            else:
                selected = random.sample(test_pool, min(int(n),len(test_pool)))
                st.session_state["mock"] = {
                    "ids":[q["id"] for q in selected],
                    "start":time.time(),
                    "seconds":int(mins)*60,
                    "answers":{},
                    "done":False,
                }
                st.rerun()
        mock = st.session_state.get("mock")
        if mock and not mock.get("done"):
            remaining = max(0, mock["seconds"] - int(time.time()-mock["start"]))
            st.metric("Time remaining", f"{remaining//60:02d}:{remaining%60:02d}")
            if remaining == 0:
                st.warning("Time is up. Submit your test now.")
            qmap = {q["id"]:q for q in load_questions()}
            with st.form("mock_answers"):
                for idx, qid in enumerate(mock["ids"], start=1):
                    q = qmap.get(qid)
                    if not q:
                        continue
                    st.markdown(f"**{idx}. {q['question']}**")
                    opts = {letter:q.get(f"option_{letter.lower()}","") for letter in "ABCD"}
                    opts = {k:v for k,v in opts.items() if v.strip()}
                    current = mock["answers"].get(str(qid))
                    selected_answer = st.radio("Options", list(opts.keys()),
                        format_func=lambda k, opts=opts: f"{k}. {opts[k]}",
                        index=list(opts.keys()).index(current) if current in opts else None,
                        key=f"mock_{qid}", horizontal=False)
                    if selected_answer:
                        mock["answers"][str(qid)] = selected_answer
                    st.divider()
                submit = st.form_submit_button("Submit mock test", type="primary")
            if submit or remaining == 0:
                total = 0
                correct_n = 0
                scored_n = 0
                for qid in mock["ids"]:
                    q = qmap.get(qid)
                    chosen = mock["answers"].get(str(qid), "")
                    if q and q.get("correct"):
                        scored_n += 1
                        is_correct = chosen == q["correct"]
                        correct_n += int(is_correct)
                        if chosen:
                            record_attempt(qid, chosen, is_correct, "mock")
                with get_conn() as c:
                    c.execute("INSERT INTO test_results(mode,total,correct,completed_at) VALUES(?,?,?,?)",
                              ("mock", scored_n, correct_n, datetime.now().isoformat(timespec="seconds")))
                mock["done"] = True
                mock["score"] = correct_n
                mock["scored"] = scored_n
                st.rerun()
        elif mock and mock.get("done"):
            scored = mock.get("scored",0)
            score = mock.get("score",0)
            st.success("Mock test submitted.")
            st.metric("Score", f"{score}/{scored}" if scored else "No answer-keyed questions to score")
            st.metric("Accuracy", f"{score/scored*100:.1f}%" if scored else "—")
            qmap = {q["id"]:q for q in load_questions()}
            with st.expander("Review answers"):
                for qid in mock["ids"]:
                    q = qmap.get(qid)
                    if q:
                        st.write(q["question"])
                        st.write(f"Your answer: {mock['answers'].get(str(qid),'Not answered')} · Answer key: {q.get('correct') or 'Not available'}")
            if st.button("Clear finished test"):
                st.session_state.pop("mock", None)
                st.rerun()

# ---------- Wrong answers ----------
elif page == "Wrong Answers":
    st.title("Wrong-answer revision")
    with get_conn() as c:
        rows = c.execute("""
        SELECT q.*, MAX(a.attempted_at) AS last_attempt, COUNT(*) AS wrong_count
        FROM questions q JOIN attempts a ON a.question_id=q.id
        WHERE a.is_correct=0
        GROUP BY q.id ORDER BY last_attempt DESC
        """).fetchall()
    items = [dict(r) for r in rows]
    if not items:
        st.info("No wrong answers recorded yet. Attempt some questions in practice mode or a mock test.")
    else:
        st.write(f"{len(items)} questions to revise")
        for q in items:
            with st.expander(f"{q['question'][:120]}  ·  Wrong attempts: {q['wrong_count']}"):
                st.write(f"Chapter: {q['chapter']}")
                for letter in "ABCD":
                    val = q.get(f"option_{letter.lower()}","")
                    if val:
                        st.write(f"{letter}. {val}")
                st.write(f"Answer key: {q['correct'] or 'Not available'}")
                if q.get("explanation"):
                    st.info(q["explanation"])
                if st.button("Remove from wrong-answer list (clear its attempt history)", key=f"clearwrong_{q['id']}"):
                    with get_conn() as c:
                        c.execute("DELETE FROM attempts WHERE question_id=?", (q["id"],))
                    st.rerun()

# ---------- Bookmarks ----------
elif page == "Bookmarks":
    st.title("Bookmarked questions")
    with get_conn() as c:
        rows = c.execute("SELECT q.* FROM questions q JOIN bookmarks b ON b.question_id=q.id ORDER BY q.chapter,q.id").fetchall()
    items = [dict(r) for r in rows]
    if not items:
        st.info("Bookmark questions from Chapter Practice or Manage Questions.")
    for q in items:
        with st.expander(f"{q['chapter']} · {q['question'][:120]}"):
            for letter in "ABCD":
                val = q.get(f"option_{letter.lower()}","")
                if val:
                    st.write(f"{letter}. {val}")
            st.write(f"Answer key: {q['correct'] or 'Not available'}")
            if q.get("explanation"):
                st.info(q["explanation"])
            if st.button("Remove bookmark", key=f"unmark_{q['id']}"):
                toggle_bookmark(q["id"])
                st.rerun()

# ---------- Manage ----------
elif page == "Manage Questions":
    st.title("Manage question bank")
    items = load_questions()
    if not items:
        st.info("No questions to manage yet.")
    else:
        df = pd.DataFrame([{
            "id":q["id"],"chapter":q["chapter"],"question":q["question"],
            "A":q["option_a"],"B":q["option_b"],"C":q["option_c"],"D":q["option_d"],
            "correct":q["correct"],"explanation":q["explanation"],
            "source":q["source"],"page":q["source_page"]
        } for q in items])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.subheader("Edit or delete a question")
        ids = [q["id"] for q in items]
        chosen_id = st.selectbox("Question ID", ids)
        q = next(x for x in items if x["id"] == chosen_id)
        with st.form("edit_q"):
            chapter = st.text_input("Chapter", q["chapter"])
            question = st.text_area("Question", q["question"])
            cols = st.columns(2)
            with cols[0]:
                a = st.text_input("Option A", q["option_a"])
                b = st.text_input("Option B", q["option_b"])
            with cols[1]:
                copt = st.text_input("Option C", q["option_c"])
                d = st.text_input("Option D", q["option_d"])
            correct = st.selectbox("Correct answer (leave blank if unknown)", ["", "A","B","C","D"],
                                   index=["","A","B","C","D"].index(q["correct"] if q["correct"] in ["A","B","C","D"] else ""))
            explanation = st.text_area("Explanation", q["explanation"])
            save = st.form_submit_button("Save changes")
        if save:
            with get_conn() as c:
                c.execute("""UPDATE questions SET chapter=?,question=?,option_a=?,option_b=?,option_c=?,option_d=?,correct=?,explanation=? WHERE id=?""",
                          (chapter,question,a,b,copt,d,correct,explanation,chosen_id))
            st.success("Question updated.")
            st.rerun()
        if st.button("Delete selected question", type="secondary"):
            st.session_state["confirm_delete"] = chosen_id
        if st.session_state.get("confirm_delete") == chosen_id:
            st.warning("This permanently deletes the selected question and its linked history/bookmark.")
            if st.button("Confirm permanent deletion", type="primary"):
                with get_conn() as c:
                    c.execute("DELETE FROM attempts WHERE question_id=?", (chosen_id,))
                    c.execute("DELETE FROM bookmarks WHERE question_id=?", (chosen_id,))
                    c.execute("DELETE FROM questions WHERE id=?", (chosen_id,))
                st.session_state.pop("confirm_delete", None)
                st.rerun()

# ---------- Export / Backup ----------
elif page == "Export / Backup":
    st.title("Export and backup")
    with get_conn() as c:
        qrows = c.execute("SELECT * FROM questions ORDER BY id").fetchall()
        arows = c.execute("SELECT * FROM attempts ORDER BY id").fetchall()
        brows = c.execute("SELECT * FROM bookmarks ORDER BY question_id").fetchall()
        trows = c.execute("SELECT * FROM test_results ORDER BY id").fetchall()
    questions_json = json.dumps([dict(r) for r in qrows], ensure_ascii=False, indent=2)
    st.download_button("Download question bank (JSON)", questions_json, "mcq_questions_backup.json", "application/json")
    attempts_csv = pd.DataFrame([dict(r) for r in arows]).to_csv(index=False)
    st.download_button("Download attempt history (CSV)", attempts_csv, "mcq_attempts.csv", "text/csv")
    st.write("For a complete backup including progress, stop the app and copy `mcq_master.db` to a safe folder.")
    st.warning("The database includes your questions and progress. Keep backups private.")
