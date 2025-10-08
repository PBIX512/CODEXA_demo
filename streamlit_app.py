import streamlit as st
import os, io, re, json, csv, shutil, hashlib
from datetime import datetime
import streamlit.components.v1 as components

# =========================
# Optional deps (graceful if missing)
# =========================
try:
    from pypdf import PdfReader
    HAVE_PDF = True
except Exception:
    HAVE_PDF = False

try:
    import docx as docx_mod
    HAVE_DOCX = True
except Exception:
    HAVE_DOCX = False

try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except Exception:
    HAVE_BS4 = False

try:
    from langdetect import detect as lang_detect
    HAVE_LANG = True
except Exception:
    HAVE_LANG = False

try:
    from streamlit_pdf_viewer import pdf_viewer
    HAVE_PDF_VIEWER = True
except Exception:
    HAVE_PDF_VIEWER = False

try:
    import mammoth  # DOCX -> HTML
    HAVE_MAMMOTH = True
except Exception:
    HAVE_MAMMOTH = False


# =========================
# Config / constants
# =========================
st.set_page_config(page_title="Codexa Prototype", layout="wide")

CLEAN_ON_UPLOAD = True
PII_SCRUB_ON_UPLOAD = True

BASE = "storage"
ORIG = os.path.join(BASE, "original")
STD = os.path.join(BASE, "standard")        # cleaned text files live here
INDEX_PATH = os.path.join(BASE, "dedupe_index.json")
CONTRACTS_DIR = "contracts"  # optional: markdown files like training_only.md
os.makedirs(ORIG, exist_ok=True)
os.makedirs(STD, exist_ok=True)

WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
YEAR_RE = re.compile(r"(19|20)\d{2}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
URL_RE = re.compile(r"https?://\S+")

# Admin password (set env CODEXA_ADMIN_PASSWORD in Cloud; defaults to "admin" locally)
ADMIN_PASSWORD = os.environ.get("CODEXA_ADMIN_PASSWORD", "admin")

# Contract labels ↔ keys
CONTRACT_LABELS = ["Training Only", "Training + AI-Reading", "Full Access"]
CONTRACT_KEY = {
    "Training Only": "training_only",
    "Training + AI-Reading": "training_plus_ai",
    "Full Access": "full_access",
}

CLEAN_ON_UPLOAD = True
PII_SCRUB_ON_UPLOAD = True

DEFAULT_CONTRACT_TEXT = {
    "Training Only": """### Codexa Contract — Training Only

**Scope:** You grant Codexa and its customers a non-exclusive license to use this file **only for model training and evaluation**. No public display or retrieval access is permitted.

- You retain ownership of your content.
- Codexa standardizes and aggregates content for training manifests.
- No public reading, sharing, or redistribution of the original file.
- Basic metadata (size, estimated tokens, timestamps) may be shown.

*Version v1.0 (prototype)*""",
    "Training + AI-Reading": """### Codexa Contract — Training + AI-Reading

**Scope:** Includes **Training Only**, plus permission for customers to index and reference this content in **retrieval-augmented systems** (AI assistants) that may quote short excerpts.

- You retain ownership of your content.
- No bulk public reproduction of the file.
- Short quotations/snippets may appear in AI responses.

*Version v1.0 (prototype)*""",
    "Full Access": """### Codexa Contract — Full Access

**Scope:** Includes **Training + AI-Reading**, plus permission for **public display** (e.g., in the Codexa library UI).

- You retain ownership of your content.
- The file may be viewable by others via Codexa.
- The file may be included in publicly accessible manifests.

*Version v1.0 (prototype)*""",
}

# Manual metadata choices
LANGUAGE_CHOICES = ["English", "Spanish", "French", "German", "Chinese", "Japanese", "Korean", "Arabic", "Hindi", "Other"]
GENRE_CHOICES = ["Academic", "Legal", "Medical", "Technical", "Historical", "News/Journalism", "Fiction", "Nonfiction", "Finance", "Other"]


# =========================
# Helpers
# =========================
def load_index() -> dict:
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_index(ix: dict) -> None:
    os.makedirs(BASE, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(ix, f, ensure_ascii=False, indent=2)

def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

def human_size(n_bytes: int) -> str:
    units = ["bytes", "KB", "MB", "GB", "TB"]
    size = float(n_bytes)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{int(size)} {u}" if u == "bytes" else f"{size:.1f} {u}"
        size /= 1024.0

def estimate_tokens_from_text(text: str) -> dict:
    words = len(WORD_RE.findall(text or ""))
    tokens = int(round(words / 0.75))
    return {"words": words, "tokens": tokens}

def estimate_tokens_from_bytes(n_bytes: int) -> dict:
    tokens = int(round(n_bytes / 4))
    words = int(round(tokens * 0.75))
    return {"words": words, "tokens": tokens}

def load_contract_text(label: str) -> str:
    key = CONTRACT_KEY.get(label, "training_only")
    p = os.path.join(CONTRACTS_DIR, f"{key}.md")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    return DEFAULT_CONTRACT_TEXT[label]

def parse_tags(tag_input: str):
    return [t.strip() for t in (tag_input or "").split(",") if t.strip()]

def safe_decode(raw: bytes) -> str:
    for enc in ("utf-8", "latin1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")

# ---------- Extraction per type ----------
def extract_text_from_bytes(filename: str, raw: bytes) -> tuple[str, list[str]]:
    """
    Returns (text, warnings[])
    """
    warnings = []
    ext = os.path.splitext(filename.lower())[1]

    if ext in (".txt", ".md", ".csv", ".json", ".html", ".htm"):
        pass
    elif ext == ".pdf":
        if not HAVE_PDF:
            warnings.append("pypdf not installed; cannot extract PDF.")
            return ("", warnings)
        try:
            from tempfile import NamedTemporaryFile
            with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(raw); tmp_path = tmp.name
            reader = PdfReader(tmp_path)
            out = []
            for page in reader.pages:
                try:
                    out.append(page.extract_text() or "")
                except Exception:
                    continue
            text = "\n".join(out).strip()
            if not text:
                warnings.append("PDF contained no extractable text (likely scanned).")
            return (text, warnings)
        except Exception as e:
            warnings.append(f"PDF parse error: {e}")
            return ("", warnings)
    elif ext == ".docx":
        if not HAVE_DOCX:
            warnings.append("python-docx not installed; cannot extract DOCX.")
            return ("", warnings)
        try:
            from tempfile import NamedTemporaryFile
            with NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                tmp.write(raw); tmp_path = tmp.name
            doc = docx_mod.Document(tmp_path)
            text = "\n".join(p.text for p in doc.paragraphs).strip()
            return (text, warnings)
        except Exception as e:
            warnings.append(f"DOCX parse error: {e}")
            return ("", warnings)

    if ext in (".txt", ".md"):
        return (safe_decode(raw), warnings)

    if ext in (".html", ".htm"):
        if not HAVE_BS4:
            warnings.append("beautifulsoup4 not installed; cannot extract HTML.")
            return ("", warnings)
        html = safe_decode(raw)
        try:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n")
            return (text, warnings)
        except Exception as e:
            warnings.append(f"HTML parse error: {e}")
            return ("", warnings)

    if ext == ".json":
        try:
            obj = json.loads(safe_decode(raw))
            flat = []
            def walk(x, prefix=""):
                if isinstance(x, dict):
                    for k,v in x.items():
                        walk(v, f"{prefix}{k}.")
                elif isinstance(x, list):
                    for i,v in enumerate(x):
                        walk(v, f"{prefix}{i}.")
                else:
                    flat.append(str(x))
            walk(obj, "")
            return ("\n".join(flat), warnings)
        except Exception as e:
            warnings.append(f"JSON parse error: {e}")
            return ("", warnings)

    if ext == ".csv":
        try:
            text = safe_decode(raw)
            reader = csv.reader(io.StringIO(text))
            lines = []
            for row in reader:
                lines.append(" | ".join(row))
            return ("\n".join(lines), warnings)
        except Exception as e:
            warnings.append(f"CSV parse error: {e}")
            return ("", warnings)

    return (safe_decode(raw), warnings)

# ---------- Cleaning ----------
def clean_text(text: str, pii: bool=False) -> str:
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"(?<!\n)\n(?!\n)", " ", s)       # unwrap single newlines
    s = re.sub(r"(\w)-\s+(\w)", r"\1\2", s)      # fix hyphenation at line breaks
    s = re.sub(r"\n{3,}", "\n\n", s)
    if pii:
        s = EMAIL_RE.sub("[EMAIL]", s)
        s = PHONE_RE.sub("[PHONE]", s)
        s = URL_RE.sub("[URL]", s)
    return s.strip()

# ---------- Auto-metadata ----------
def auto_metadata(filename: str, text: str) -> dict:
    if HAVE_LANG:
        try:
            lang = lang_detect(text[:2000]) if text else "unknown"
        except Exception:
            lang = "unknown"
    else:
        lang = "unknown"
    m = YEAR_RE.search(filename)
    year = int(m.group(0)) if m else None
    if not year and text:
        m2 = YEAR_RE.search(text[:5000])
        if m2:
            year = int(m2.group(0))
    title = None
    if text:
        first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
        title = first[:120] if first else None
    if not title:
        title = os.path.splitext(os.path.basename(filename))[0][:120]
    counts = estimate_tokens_from_text(text or "")
    return {
        "language_auto": lang,
        "year_auto": year,
        "title_auto": title,
        "words_auto": counts["words"],
        "tokens_auto": counts["tokens"],
    }

# ---------- Index utilities ----------
def index_to_rows(ix: dict, include_non_ok: bool=False):
    rows = []
    for sha, meta in ix.items():
        if not include_non_ok and meta.get("status", "ok") != "ok":
            continue
        rows.append({
            "filename": meta.get("original_name",""),
            "contract_label": meta.get("contract_label", meta.get("contract","Training Only")),
            "contract_key": meta.get("contract_key", CONTRACT_KEY.get(meta.get("contract","Training Only"), "training_only")),
            "language": meta.get("language",""),
            "genre": meta.get("genre",""),
            "tags": meta.get("tags",[]),
            "tags_display": ", ".join(meta.get("tags",[])),
            "language_auto": meta.get("metadata",{}).get("language_auto",""),
            "year_auto": meta.get("metadata",{}).get("year_auto"),
            "title_auto": meta.get("metadata",{}).get("title_auto",""),
            "size_bytes": meta.get("size_bytes",0),
            "size_pretty": human_size(meta.get("size_bytes",0)),
            "est_tokens": meta.get("est_tokens",0),
            "uploaded_at": meta.get("uploaded_at",""),
            "path": meta.get("path",""),
            "clean_path": meta.get("clean_path",""),
            "cleaned": bool(meta.get("clean_path")),
            "sha256": sha,
            "status": meta.get("status","ok"),
            "uploader_id": meta.get("uploader_id","unknown"),
        })
    return rows

def aggregate_stats(rows):
    total_files = len([r for r in rows if r["status"] == "ok"])
    total_bytes = sum(r["size_bytes"] for r in rows if r["status"] == "ok")
    total_tokens = sum(r["est_tokens"] for r in rows if r["status"] == "ok")
    duplicates = len([r for r in rows if r["status"] != "ok"])
    by_contract = {}
    for r in rows:
        if r["status"] != "ok": continue
        label = r["contract_label"] or "Unknown"
        by_contract.setdefault(label, {"files":0, "est_tokens":0, "size_bytes":0})
        by_contract[label]["files"] += 1
        by_contract[label]["est_tokens"] += r["est_tokens"]
        by_contract[label]["size_bytes"] += r["size_bytes"]
    return {"total_files": total_files, "total_tokens": total_tokens, "total_bytes": total_bytes,
            "duplicates": duplicates, "by_contract": by_contract}


# =========================
# Sidebar nav + auth toggles
# =========================
with st.sidebar:
    st.markdown("### CODEXA")
    # ---- Lightweight Sign-In (per session) ----
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""
    st.text_input("Sign in (email or handle)", key="user_id", placeholder="you@domain.com")
    if not st.session_state.user_id:
        st.info("Not signed in. Uploads will be attributed to **unknown**.")
    else:
        st.success(f"Signed in as **{st.session_state.user_id}**")

    page = st.radio("Navigation", ["Profile", "Contribute", "Library", "Admin"], index=1)
    st.markdown("---")
    if "auto_clean" not in st.session_state:
        st.session_state.auto_clean = True
    if "pii_scrub" not in st.session_state:
        st.session_state.pii_scrub = False
   st.session_state.auto_clean = CLEAN_ON_UPLOAD
st.session_state.pii_scrub = PII_SCRUB_ON_UPLOAD

if "admin_authed" not in st.session_state:
    st.session_state.admin_authed = False


# =========================
# Profile (PER-USER view)
# =========================
if page == "Profile":
    st.markdown("## Profile")
    user_id = st.session_state.user_id or "unknown"

    ix = load_index()
    rows_all = index_to_rows(ix, include_non_ok=True)
    your_rows = [r for r in rows_all if r["uploader_id"] == user_id]
    all_ok = [r for r in rows_all if r["status"]=="ok"]

    your_tokens = sum(r["est_tokens"] for r in your_rows if r["status"]=="ok")
    total_tokens_ok = sum(r["est_tokens"] for r in all_ok)
    your_files = len([r for r in your_rows if r["status"]=="ok"])
    your_bytes = sum(r["size_bytes"] for r in your_rows if r["status"]=="ok")
    your_pct = (your_tokens / total_tokens_ok * 100.0) if total_tokens_ok > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Your Files", f"{your_files}")
    c2.metric("Your Tokens (est.)", f"{your_tokens:,}")
    c3.metric("Your Storage", human_size(your_bytes))
    c4.metric("Your % of Total Tokens", f"{your_pct:.2f}%")

    st.caption("Only your uploads are shown below. (Older files without an uploader are counted as 'unknown' and not included here.)")
    recent = sorted(your_rows, key=lambda r: r["uploaded_at"], reverse=True)[:50]
    if recent:
        st.dataframe(recent, use_container_width=True)
    else:
        st.info("No uploads yet for this user. Go to **Contribute** to upload.")


# =========================
# Contribute
# =========================
elif page == "Contribute":
    st.markdown("## CODEXA — Contributor")
    st.caption("Select contract → read & accept → set metadata → upload → dedupe → clean/auto-metadata → manifest")

    contract_label = st.selectbox("Contract", CONTRACT_LABELS, index=0)
    contract_key = CONTRACT_KEY[contract_label]

    with st.expander("📄 Read Contract", expanded=False):
        st.markdown(load_contract_text(contract_label))
    accepted_terms = st.checkbox("I have read and accept the contract terms")

    st.write("### Metadata (manual)")
    c1, c2 = st.columns(2)
    with c1:
        lang_choice = st.selectbox("Language", LANGUAGE_CHOICES, index=0)
        lang_other = st.text_input("If 'Other', specify language", "")
        language = lang_other.strip() if lang_choice == "Other" and lang_other.strip() else lang_choice
    with c2:
        genre_choice = st.selectbox("Genre", GENRE_CHOICES, index=0)
        genre_other = st.text_input("If 'Other', specify genre", "")
        genre = genre_other.strip() if genre_choice == "Other" and genre_other.strip() else genre_choice
    tags_input = st.text_input("Tags (comma-separated)", placeholder="e.g., law, case, appeals")
    tags = parse_tags(tags_input)

    st.write("### Upload files")
    files = st.file_uploader(
        "Drag & drop or click to browse",
        type=["pdf","docx","txt","md","html","htm","json","csv"],
        accept_multiple_files=True,
        label_visibility="collapsed"
    )

    process = st.button("Upload", disabled=not (accepted_terms and files))

    if process:
        index = load_index()
        accepted_rows, duplicate_rows = [], []
        total_tokens = 0
        uploader_id = st.session_state.user_id or "unknown"

        prog = st.progress(0); total = len(files)
        for i, f in enumerate(files, start=1):
            raw = f.getvalue()
            sha_raw = sha256_bytes(raw)

            if sha_raw in index:
                prev = index[sha_raw]
                size_b = len(raw)
                counts = estimate_tokens_from_bytes(size_b)
                duplicate_rows.append({
                    "filename": f.name, "size_bytes": size_b, "size_pretty": human_size(size_b),
                    "est_tokens": counts["tokens"], "duplicate_of": prev.get("path"),
                    "uploaded_at_original": prev.get("uploaded_at"),
                    "contract": prev.get("contract_label","Training Only"),
                    "language": prev.get("language",""), "genre": prev.get("genre",""), "tags": prev.get("tags",[]),
                    "uploader_id": prev.get("uploader_id","unknown")
                })
            else:
                ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                clean_name = f.name.replace(" ", "_")
                save_path = os.path.join(ORIG, f"{ts}__{clean_name}")
                with open(save_path, "wb") as out:
                    out.write(raw)

                extracted_text, warns = extract_text_from_bytes(f.name, raw)

                clean_txt = ""
                auto_meta = {}
                if st.session_state.auto_clean and extracted_text:
                    clean_txt = clean_text(extracted_text, pii=st.session_state.pii_scrub)
                    auto_meta = auto_metadata(f.name, clean_txt)
                    clean_base = f"{ts}__{os.path.splitext(clean_name)[0]}.clean.txt"
                    clean_path = os.path.join(STD, clean_base)
                    with open(clean_path, "w", encoding="utf-8") as c:
                        c.write(clean_txt)
                else:
                    auto_meta = auto_metadata(f.name, extracted_text or "")

                if clean_txt:
                    counts = estimate_tokens_from_text(clean_txt)
                elif extracted_text:
                    counts = estimate_tokens_from_text(extracted_text)
                else:
                    counts = estimate_tokens_from_bytes(len(raw))
                total_tokens += counts["tokens"]

                entry = {
                    "path": save_path,
                    "original_name": f.name,
                    "contract_label": contract_label,
                    "contract_key": contract_key,
                    "uploaded_at": ts,
                    "size_bytes": len(raw),
                    "est_tokens": counts["tokens"],
                    "est_words": counts["words"],
                    "status": "ok",
                    "language": language,
                    "genre": genre,
                    "tags": tags,
                    "metadata": auto_meta,
                    "uploader_id": uploader_id,  # NEW
                }
                if clean_txt:
                    entry["clean_path"] = clean_path

                index[sha_raw] = entry

                accepted_rows.append({
                    "filename": f.name, "size_bytes": len(raw), "size_pretty": human_size(len(raw)),
                    "est_words": counts["words"], "est_tokens": counts["tokens"], "saved_as": save_path, "uploaded_at": ts,
                    "contract": contract_label, "language": language, "genre": genre, "tags": ", ".join(tags),
                    "extraction_warnings": "; ".join(warns) if warns else "",
                    "uploader_id": uploader_id,
                })
            prog.progress(i/total)

        save_index(index)

        st.success(f"Done! {len(accepted_rows)} uploaded, {len(duplicate_rows)} duplicates skipped.")
        st.write(f"**Batch estimated tokens:** {total_tokens:,}")

        if accepted_rows:
            st.write("**Accepted files**"); st.dataframe(accepted_rows, use_container_width=True)
        if duplicate_rows:
            st.write("**Duplicates (skipped)**"); st.dataframe(duplicate_rows, use_container_width=True)

        # Batch manifest (JSON)
        batch_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        manifest = {
            "batch_id": f"batch_{batch_id}",
            "contract": contract_key, "contract_label": contract_label,
            "created_at": datetime.utcnow().isoformat()+"Z",
            "accepted": accepted_rows, "duplicates": duplicate_rows,
            "batch_est_tokens": total_tokens
        }
        buf_json = io.BytesIO(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        st.download_button("Download Manifest (JSON)", data=buf_json,
                           file_name=f"manifest_{batch_id}.json", mime="application/json")

    st.markdown("---")
    missing = []
    if not HAVE_PDF: missing.append("PDF (install `pypdf`)")
    if not HAVE_DOCX: missing.append("DOCX (install `python-docx`)")
    if not HAVE_BS4: missing.append("HTML (install `beautifulsoup4`)")
    if not HAVE_LANG: missing.append("Language auto-detect (install `langdetect`)")
    if missing:
        st.info("Optional features unavailable: " + " • ".join(missing))


# =========================
# Library
# =========================
elif page == "Library":
    st.markdown("## Library — Accepted Files")
    st.caption("Filter, preview (original formatting or cleaned text), and build custom manifests.")

    index = load_index()
    rows = index_to_rows(index, include_non_ok=False)

    all_languages = sorted({r["language"] for r in rows if r["language"]})
    all_genres = sorted({r["genre"] for r in rows if r["genre"]})
    years_auto = sorted({r["year_auto"] for r in rows if r["year_auto"]}) if rows else []
    min_year = min(years_auto) if years_auto else 1900
    max_year = max(years_auto) if years_auto else 2100

    c1, c2, c3, c4 = st.columns([2, 1.2, 1.2, 1.2])
    with c1:
        q = st.text_input("Filename contains:", "")
    with c2:
        contract_filter = st.multiselect("Contracts", CONTRACT_LABELS, default=CONTRACT_LABELS)
    with c3:
        lang_filter = st.multiselect("Language", all_languages, default=None)
    with c4:
        genre_filter = st.multiselect("Genre", all_genres, default=None)

    yr_enabled = st.checkbox("Filter by auto-detected year", value=False)
    if yr_enabled:
        yr1, yr2 = st.slider("Year range (auto)", min_year, max_year, (min_year, max_year))

    filtered = rows[:]
    if q: filtered = [r for r in filtered if q.lower() in r["filename"].lower()]
    if contract_filter:
        filtered = [r for r in filtered if (r["contract_label"] or "Training Only") in contract_filter]
    if lang_filter:
        filtered = [r for r in filtered if r["language"] in lang_filter]
    if genre_filter:
        filtered = [r for r in filtered if r["genre"] in genre_filter]
    if yr_enabled:
        filtered = [r for r in filtered if (r["year_auto"] is not None and yr1 <= r["year_auto"] <= yr2)]

    with st.expander("🔎 Tags filter (optional)"):
        tf1, tf2 = st.columns([3,1])
        with tf1:
            tags_input = st.text_input("Tags (comma-separated)", placeholder="e.g., law, appeals")
        with tf2:
            match_all = st.checkbox("Match ALL", value=False)
        tag_list = parse_tags(tags_input)
        if tag_list:
            if match_all:
                filtered = [r for r in filtered if all(t.lower() in [x.lower() for x in r["tags"]] for t in tag_list)]
            else:
                filtered = [r for r in filtered if any(t.lower() in [x.lower() for x in r["tags"]] for t in tag_list)]

    if not filtered:
        st.info("No files in this view. Adjust filters or go to **Contribute** to upload.")
    else:
        st.write(
            f"**Files shown:** {len(filtered)}  |  "
            f"**Total est. tokens:** {sum(r['est_tokens'] for r in filtered):,}  |  "
            f"**Total size:** {human_size(sum(r['size_bytes'] for r in filtered))}"
        )
        table_rows = [{
            "filename": r["filename"],
            "contract": r["contract_label"],
            "language": r["language"],
            "genre": r["genre"],
            "auto_lang": r["language_auto"],
            "auto_year": r["year_auto"],
            "title": r["title_auto"],
            "cleaned": "✅" if r["cleaned"] else "—",
            "size": r["size_pretty"],
            "est_tokens": r["est_tokens"],
            "uploaded_at": r["uploaded_at"],
            "sha256": r["sha256"],
            "path_original": r["path"],
            "path_clean": r["clean_path"],
            "uploader": r["uploader_id"],
        } for r in filtered]
        st.dataframe(table_rows, use_container_width=True)

        # ---- Manifest from current filters (KeyError-safe) ----
        st.markdown("### Build Custom Manifest from Current Filters")
        query_name = st.text_input("Optional manifest name", value="")
        filtered_sorted = sorted(filtered, key=lambda r: (r["filename"].lower(), r["sha256"]))
        lib_id = datetime.utcnow().strftime("manifest_%Y%m%dT%H%M%SZ")
        query_spec = {
            "filename_contains": q or "",
            "contracts": contract_filter,
            "languages": lang_filter or [],
            "genres": genre_filter or [],
            "year_enabled": yr_enabled,
            "year_range": [yr1, yr2] if yr_enabled else None,
            "tags": tag_list,
            "tags_match": "all" if (tag_list and match_all) else ("any" if tag_list else None),
        }
        manifest_files = []
        for r in filtered_sorted:
            manifest_files.append({
                "filename": r["filename"],
                "paths": {
                    "original": r.get("path_original", r.get("path", "")),
                    "standard": r.get("path_clean", "")
                },
                "size_bytes": r["size_bytes"],
                "est_tokens": r["est_tokens"],
                "uploaded_at": r["uploaded_at"],
                "contract": r["contract_label"],
                "contract_label": r["contract_label"],
                "language": r["language"],
                "genre": r["genre"],
                "tags": r["tags"],
                "auto": {"language": r["language_auto"], "year": r["year_auto"], "title": r["title_auto"]},
                "uploader_id": r["uploader_id"],
                "sha256_raw": r["sha256"]
            })
        manifest = {
            "manifest_id": lib_id if not query_name else f"{lib_id}__{re.sub(r'[^A-Za-z0-9_-]+','-',query_name)[:40]}",
            "created_at": datetime.utcnow().isoformat()+"Z",
            "query": query_spec,
            "stats": {
                "files": len(manifest_files),
                "total_est_tokens": sum(r["est_tokens"] for r in filtered_sorted),
                "total_size_bytes": sum(r["size_bytes"] for r in filtered_sorted),
            },
            "files": manifest_files,
            "version": "v0.6"
        }
        buf = io.BytesIO(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        st.download_button("Download Custom Manifest (JSON)", data=buf,
                           file_name=f"{manifest['manifest_id']}.json", mime="application/json")

        # ---- Viewer with original formatting OR cleaned text ----
        st.write("### View file")
        sel = st.selectbox("Choose a file", ["-- select --"] + [r["filename"] for r in filtered_sorted])
        if sel and sel != "-- select --":
            r = next((x for x in filtered_sorted if x["filename"] == sel), None)
            if r:
                can_view = (r["contract_label"] == "Full Access") or st.session_state.get("admin_override_view", False)
                if not can_view:
                    st.warning("This file is not marked 'Full Access' and cannot be viewed inline.")
                else:
                    # Load bytes of ORIGINAL file
                    src_path = r.get("path_original", r.get("path", ""))
                    if not src_path or not os.path.exists(src_path):
                        st.error("Original file missing on disk.")
                    else:
                        with open(src_path, "rb") as f:
                            raw = f.read()
                        ext = os.path.splitext(r["filename"].lower())[1]

                        view_mode = st.radio("View mode", ["Original formatting", "Cleaned text"], horizontal=True, index=0)

                        if view_mode == "Original formatting":
                            if ext == ".pdf":
                                if HAVE_PDF_VIEWER:
                                    try:
                                        # render all pages dynamically
                                        reader = PdfReader(io.BytesIO(raw)) if HAVE_PDF else None
                                        if reader:
                                            total_pages = len(reader.pages)
                                            pages = list(range(1, total_pages + 1))
                                        else:
                                            pages = [1]
                                        pdf_viewer(
                                            raw,
                                            width=900,
                                            height=900,
                                            pages_to_render=pages
                                        )
                                    except Exception:
                                        # fallback: render first page
                                        pdf_viewer(raw, width=900, height=900, pages_to_render=[1])
                                else:
                                    st.info("PDF viewer not installed. Add `streamlit-pdf-viewer` to requirements.txt")
                            elif ext == ".docx":
                                if HAVE_MAMMOTH:
                                    html = mammoth.convert_to_html(io.BytesIO(raw)).value
                                    components.html(
                                        f"""
                                        <div style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; line-height:1.5; padding:16px;">
                                            {html}
                                        </div>
                                        """,
                                        height=900, scrolling=True
                                    )
                                else:
                                    st.info("DOCX viewer not installed. Add `mammoth` to requirements.txt")
                            elif ext in (".html", ".htm"):
                                html = raw.decode("utf-8", errors="ignore")
                                # NOTE: For untrusted HTML, consider sanitizing or stripping <script> tags.
                                components.html(html, height=900, scrolling=True)
                            elif ext in (".md",):
                                st.markdown(raw.decode("utf-8", errors="ignore"))
                            elif ext in (".txt", ".json", ".csv"):
                                st.code(raw.decode("utf-8", errors="ignore")[:200000] or "(empty)", language="text")
                            else:
                                st.info(f"No specialized renderer for {ext}. Showing cleaned text instead.")
                                view_mode = "Cleaned text"

                        if view_mode == "Cleaned text":
                            text_bytes = None
                            clean_path = r.get("path_clean")
                            if clean_path and os.path.exists(clean_path):
                                with open(clean_path, "rb") as f:
                                    text_bytes = f.read()
                            else:
                                # fall back to on-the-fly extraction for readable types
                                try:
                                    text, _ = extract_text_from_bytes(r["filename"], raw)
                                    text_bytes = text.encode("utf-8", errors="ignore")
                                except Exception:
                                    text_bytes = None

                            if not text_bytes:
                                st.warning("No cleaned/readable text available for this file.")
                            else:
                                text = text_bytes.decode("utf-8", errors="ignore")
                                CHUNK = 60000
                                total_pages = max(1, (len(text) + CHUNK - 1)//CHUNK)
                                if total_pages > 1:
                                    col1, col2 = st.columns([1,4])
                                    with col1:
                                        page_idx = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
                                    with col2:
                                        st.caption(f"{len(text):,} chars • {total_pages} page(s) @ {CHUNK} chars/page")
                                    start, end = (page_idx-1)*CHUNK, (page_idx-1)*CHUNK + CHUNK
                                    chunk = text[start:end]
                                else:
                                    chunk = text
                                st.code(chunk if chunk else "(empty)", language="text")


# =========================
# Admin (password-protected)
# =========================
else:
    st.markdown("## Admin")
    # ---- password gate ----
    if not st.session_state.admin_authed:
        with st.form("admin_login"):
            st.write("Admin login required")
            pw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Enter")
        if submitted:
            if pw == ADMIN_PASSWORD:
                st.session_state.admin_authed = True
                st.success("Admin unlocked.")
            else:
                st.error("Incorrect password.")
        st.stop()

    st.caption("Export/import index, delete files, re-clean/rebuild metadata, export JSONL, diagnostics, overrides.")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🧹 Clear All Saved Data (originals + dedupe index + standard)", use_container_width=True):
            if os.path.isdir(ORIG): shutil.rmtree(ORIG, ignore_errors=True)
            os.makedirs(ORIG, exist_ok=True)
            if os.path.isdir(STD): shutil.rmtree(STD, ignore_errors=True)
            os.makedirs(STD, exist_ok=True)
            if os.path.exists(INDEX_PATH): os.remove(INDEX_PATH)
            st.success("Cleared storage and index.")

        ix = load_index()
        buf = io.BytesIO(json.dumps(ix, ensure_ascii=False, indent=2).encode("utf-8"))
        st.download_button("⬇️ Export dedupe_index.json", data=buf, file_name="dedupe_index.json", mime="application/json")

    with c2:
        up = st.file_uploader("Replace dedupe_index.json", type=["json"], accept_multiple_files=False, key="admin_index_upload")
        if up and st.button("Replace Index"):
            try:
                new_ix = json.loads(up.getvalue().decode("utf-8"))
                save_index(new_ix); st.success("Index replaced.")
            except Exception as e:
                st.error(f"Import error: {e}")

    st.markdown("---")

    ix = load_index()
    rows = index_to_rows(ix, include_non_ok=True)

    if rows:
        d1, d2 = st.columns(2)
        with d1:
            st.subheader("Delete a File")
            labels = [f"{r['filename']} ({r['sha256'][:10]}…) — {r['uploader_id']}" for r in rows]
            sel = st.selectbox("Select file", ["-- select --"] + labels, key="admin_delete_select")
            if sel and sel != "-- select --":
                chosen = rows[labels.index(sel)]
                if st.button("❌ Delete Selected", type="primary"):
                    try:
                        if os.path.exists(chosen["path"]): os.remove(chosen["path"])
                        if chosen.get("clean_path") and os.path.exists(chosen["clean_path"]):
                            os.remove(chosen["clean_path"])
                    except Exception:
                        pass
                    rec = ix.get(chosen["sha256"])
                    if rec: del ix[chosen["sha256"]]
                    save_index(ix)
                    st.success("Deleted and updated index.")

        with d2:
            st.subheader("Recompute token/word estimates")
            if st.button("🔁 Recompute for all"):
                changed = 0
                ix = load_index()
                for sha, meta in ix.items():
                    p = meta.get("clean_path") or meta.get("path")
                    if not p or not os.path.exists(p): continue
                    try:
                        if p.endswith(".txt"):
                            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                                text = f.read()
                            counts = estimate_tokens_from_text(text)
                        else:
                            with open(p, "rb") as f: raw = f.read()
                            counts = estimate_tokens_from_bytes(len(raw))
                        meta["est_tokens"] = counts["tokens"]; meta["est_words"] = counts["words"]
                        changed += 1
                    except Exception:
                        continue
                save_index(ix); st.success(f"Recomputed for {changed} file(s).")

    st.markdown("---")

    r1, r2 = st.columns(2)
    with r1:
        st.subheader("Re-clean all")
        if st.button("🧼 Re-clean (parse → clean → write)"):
            ix = load_index(); ok = 0; fail = 0
            for sha, meta in ix.items():
                path = meta.get("path"); fn = meta.get("original_name","")
                if not path or not os.path.exists(path): fail += 1; continue
                try:
                    with open(path, "rb") as f: raw = f.read()
                    text, _ = extract_text_from_bytes(fn, raw)
                    if not text: fail += 1; continue
                    clean_txt = clean_text(text, pii=st.session_state.pii_scrub)
                    ts = meta.get("uploaded_at") or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                    clean_base = f"{ts}__{os.path.splitext(fn.replace(' ','_'))[0]}.clean.txt"
                    clean_path = os.path.join(STD, clean_base)
                    with open(clean_path, "w", encoding="utf-8") as c: c.write(clean_txt)
                    meta["clean_path"] = clean_path
                    counts = estimate_tokens_from_text(clean_txt)
                    meta["est_tokens"] = counts["tokens"]; meta["est_words"] = counts["words"]
                    ok += 1
                except Exception:
                    fail += 1
            save_index(ix)
            st.success(f"Re-cleaned OK: {ok}, Failed: {fail}")

    with r2:
        st.subheader("Rebuild auto-metadata")
        if st.button("🧠 Rebuild auto-metadata for all"):
            ix = load_index(); ok = 0
            for sha, meta in ix.items():
                p = meta.get("clean_path")
                text = ""
                if p and os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as f: text = f.read()
                    except Exception: text = ""
                if not text:
                    try:
                        with open(meta.get("path",""), "rb") as f: raw = f.read()
                        text, _ = extract_text_from_bytes(meta.get("original_name",""), raw)
                    except Exception: text = ""
                meta["metadata"] = auto_metadata(meta.get("original_name",""), text or "")
                ok += 1
            save_index(ix); st.success(f"Rebuilt auto-metadata for {ok} file(s).")

    st.markdown("---")

    st.subheader("Export cleaned dataset (JSONL)")
    if st.button("⬇️ Export JSONL (cleaned text + metadata)"):
        ix = load_index()
        out = io.StringIO()
        count = 0
        for sha, meta in ix.items():
            p = meta.get("clean_path")
            if not p or not os.path.exists(p): continue
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                record = {
                    "id": sha,
                    "text": text,
                    "license": meta.get("contract_key","training_only"),
                    "manual_meta": {
                        "language": meta.get("language",""),
                        "genre": meta.get("genre",""),
                        "tags": meta.get("tags",[])
                    },
                    "auto_meta": meta.get("metadata", {}),
                    "uploader_id": meta.get("uploader_id","unknown"),
                    "paths": {"original": meta.get("path"), "standard": p}
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
            except Exception:
                continue
        st.download_button("Download dataset.jsonl", data=out.getvalue().encode("utf-8"),
                           file_name="dataset.jsonl", mime="application/jsonl")
        st.caption(f"Included {count} cleaned file(s).")

    st.markdown("---")

    o1, o2 = st.columns(2)
    with o1:
        st.subheader("Preview Override")
        st.checkbox("Allow inline preview for non–Full Access (dev mode)", key="admin_override_view")
        if st.session_state.admin_override_view:
            st.warning("Dev override ENABLED — inline previews ignore contract gate.")

    with o2:
        st.subheader("Force-Change Contract")
        if rows:
            labels2 = [f"{r['filename']} ({r['sha256'][:10]}…) — {r['uploader_id']}" for r in rows]
            sel2 = st.selectbox("Select file", ["-- select --"] + labels2, key="admin_contract_select")
            new_contract_label = st.selectbox("New contract", CONTRACT_LABELS, index=0, key="admin_contract_newlabel")
            if sel2 and sel2 != "-- select --" and st.button("Update Contract"):
                chosen = rows[labels2.index(sel2)]
                key = CONTRACT_KEY[new_contract_label]
                rec = ix.get(chosen["sha256"])
                if rec:
                    rec["contract_label"] = new_contract_label
                    rec["contract_key"] = key
                    save_index(ix)
                    st.success(f"Updated contract to '{new_contract_label}' for {chosen['filename']}.")
        else:
            st.caption("No files available to change contract.")
