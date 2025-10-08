import streamlit as st
import os
import io
import re
import json
import shutil
import hashlib
from datetime import datetime

# =========================
# Config / constants
# =========================
st.set_page_config(page_title="Codexa Prototype", layout="centered")

BASE = "storage"
ORIG = os.path.join(BASE, "original")
STD = os.path.join(BASE, "standard")
INDEX_PATH = os.path.join(BASE, "dedupe_index.json")
CONTRACTS_DIR = "contracts"  # optional: put markdown files like training_only.md here
os.makedirs(ORIG, exist_ok=True)
os.makedirs(STD, exist_ok=True)

WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)

# Contract labels ↔ keys
CONTRACT_LABELS = [
    "Training Only",
    "Training + AI-Reading",
    "Full Access",
]
CONTRACT_KEY = {
    "Training Only": "training_only",
    "Training + AI-Reading": "training_plus_ai",
    "Full Access": "full_access",
}

# Default contract text (used if no file in /contracts)
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
# Helpers: index I/O
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
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def estimate_tokens(filename: str, raw: bytes) -> dict:
    """
    Returns dict: {"words": int, "tokens": int}
    - For .txt/.md: decode and count words; tokens ≈ words / 0.75
    - Others: tokens ≈ bytes / 4; words ≈ tokens * 0.75
    """
    name = filename.lower()
    if name.endswith(".txt") or name.endswith(".md"):
        try:
            text = raw.decode("utf-8", errors="ignore")
            words = len(WORD_RE.findall(text))
            tokens = int(round(words / 0.75))
            return {"words": words, "tokens": tokens}
        except Exception:
            pass
    tokens = int(round(len(raw) / 4))
    words = int(round(tokens * 0.75))
    return {"words": words, "tokens": tokens}

def human_size(n_bytes: int) -> str:
    units = ["bytes", "KB", "MB", "GB", "TB"]
    size = float(n_bytes)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{int(size)} {u}" if u == "bytes" else f"{size:.1f} {u}"
        size /= 1024.0

def parse_tags(tag_input: str):
    return [t.strip() for t in (tag_input or "").split(",") if t.strip()]

# =========================
# Helpers: contracts & rows
# =========================
def load_contract_text(label: str) -> str:
    key = CONTRACT_KEY.get(label, "training_only")
    p = os.path.join(CONTRACTS_DIR, f"{key}.md")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    return DEFAULT_CONTRACT_TEXT[label]

def index_to_rows(ix: dict, include_non_ok: bool = False):
    rows = []
    for sha, meta in ix.items():
        if not include_non_ok and meta.get("status", "ok") != "ok":
            continue
        rows.append({
            "filename": meta.get("original_name", ""),
            "contract_label": meta.get("contract_label", meta.get("contract", "Training Only")),
            "contract_key": meta.get("contract_key", CONTRACT_KEY.get(meta.get("contract", "Training Only"), "training_only")),
            "language": meta.get("language", ""),
            "genre": meta.get("genre", ""),
            "tags": meta.get("tags", []),
            "tags_display": ", ".join(meta.get("tags", [])),
            "title": meta.get("auto_title", ""),
            "year": meta.get("auto_year", None),
            "has_cleaned": bool(meta.get("paths", {}).get("standard")),
            "size_bytes": meta.get("size_bytes", 0),
            "size_pretty": human_size(meta.get("size_bytes", 0)),
            "est_tokens": meta.get("est_tokens", 0),
            "uploaded_at": meta.get("uploaded_at", ""),
            "path": meta.get("path", ""),
            "std_path": meta.get("paths", {}).get("standard"),
            "sha256": sha,
            "status": meta.get("status", "ok"),
        })
    return rows

def aggregate_stats(rows):
    total_files = len([r for r in rows if r["status"] == "ok"])
    total_bytes = sum(r["size_bytes"] for r in rows if r["status"] == "ok")
    total_tokens = sum(r["est_tokens"] for r in rows if r["status"] == "ok")
    duplicates = len([r for r in rows if r["status"] != "ok"])
    by_contract = {}
    for r in rows:
        if r["status"] != "ok":
            continue
        label = r["contract_label"] or "Unknown"
        by_contract.setdefault(label, {"files": 0, "est_tokens": 0, "size_bytes": 0})
        by_contract[label]["files"] += 1
        by_contract[label]["est_tokens"] += r["est_tokens"]
        by_contract[label]["size_bytes"] += r["size_bytes"]
    return {
        "total_files": total_files,
        "total_tokens": total_tokens,
        "total_bytes": total_bytes,
        "duplicates": duplicates,
        "by_contract": by_contract,
    }

# =========================
# Text extraction (universal) & cleaning & metadata
# =========================
def try_imports():
    """Best-effort optional imports."""
    PyPDF2 = None
    Docx = None
    try:
        import pypdf  # type: ignore
        PyPDF2 = pypdf
    except Exception:
        pass
    try:
        import docx  # type: ignore
        Docx = docx
    except Exception:
        pass
    return PyPDF2, Docx

PDF_LIB, DOCX_LIB = try_imports()

def extract_text_from_file(path: str, filename: str) -> str | None:
    ext = os.path.splitext(filename.lower())[-1]
    try:
        if ext in (".txt", ".md"):
            with open(path, "rb") as f:
                raw = f.read()
            return raw.decode("utf-8", errors="ignore")
        if ext == ".pdf":
            if not PDF_LIB:
                return None  # library not available
            try:
                reader = PDF_LIB.PdfReader(path)
                pages = []
                for p in reader.pages:
                    t = p.extract_text() or ""
                    pages.append(t)
                return "\n".join(pages).strip() or None
            except Exception:
                return None
        if ext == ".docx":
            if not DOCX_LIB:
                return None
            try:
                doc = DOCX_LIB.Document(path)
                return "\n".join([p.text for p in doc.paragraphs]).strip() or None
            except Exception:
                return None
    except Exception:
        return None
    return None

BOILERPLATE_HEAD = re.compile(r"^(page\s+\d+(/\d+)?|confidential|table of contents)\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
URL_RE = re.compile(r"https?://\S+")

def clean_text(text: str, pii: bool = False) -> str:
    if not text:
        return ""
    # Normalize linebreaks and whitespace
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n")]
    cleaned = []
    for i, ln in enumerate(lines):
        # collapse internal spaces
        ln = re.sub(r"[ \t]+", " ", ln)
        # drop obvious boilerplate early lines
        if i < 6 and len(ln) < 50 and BOILERPLATE_HEAD.search(ln or ""):
            continue
        cleaned.append(ln)
    s = "\n".join(cleaned)
    # collapse multiple blank lines
    s = re.sub(r"\n{3,}", "\n\n", s)
    if pii:
        s = EMAIL_RE.sub("[EMAIL]", s)
        s = PHONE_RE.sub("[PHONE]", s)
        s = URL_RE.sub("[URL]", s)
    return s.strip()

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

LANG_STOPWORDS = {
    "English": {"the","and","of","to","in","for","is","that","with","as","on","by"},
    "Spanish": {"el","la","de","que","y","en","a","los","se","del","las"},
    "French": {"le","la","et","les","des","de","en","un","une","dans"},
    "German": {"der","die","und","das","ist","mit","zu","den","im","von"},
}

def guess_language_simple(text: str) -> str:
    t = (text or "")[:5000].lower()
    best_lang, best_hits = "English", 0
    for lang, stops in LANG_STOPWORDS.items():
        hits = sum(1 for w in stops if f" {w} " in f" {t} ")
        if hits > best_hits:
            best_lang, best_hits = lang, hits
    return best_lang

def extract_year(filename: str, text: str) -> int | None:
    for source in (filename, text[:4000] if text else ""):
        if not source:
            continue
        m = YEAR_RE.search(source)
        if m:
            try:
                return int(m.group(0))
            except Exception:
                continue
    return None

def derive_title(filename: str, text: str) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0]
    if text:
        for ln in text.splitlines():
            ln = ln.strip()
            if 3 <= len(ln) <= 120:
                return ln
    return stem

def write_clean_file(std_dir: str, timestamp: str, filename: str, content: str) -> str:
    safe_name = filename.replace(" ", "_")
    target = os.path.join(std_dir, f"{timestamp}__{safe_name}.clean.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write(content or "")
    return target

# =========================
# Sidebar nav + logo
# =========================
with st.sidebar:
    st.markdown("### CODEXA")
    page = st.radio("Navigation", ["Profile", "Contribute", "Library", "Admin"], index=1)

# Session state for admin override/toggles
if "admin_override_view" not in st.session_state:
    st.session_state.admin_override_view = False
if "auto_clean_on_upload" not in st.session_state:
    st.session_state.auto_clean_on_upload = True
if "pii_scrub" not in st.session_state:
    st.session_state.pii_scrub = False

# =========================
# Page: Profile
# =========================
if page == "Profile":
    st.markdown("## Profile")
    st.caption("Overview of your Codexa prototype data (local instance).")

    ix = load_index()
    rows_all = index_to_rows(ix, include_non_ok=True)
    stats = aggregate_stats(rows_all)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files (accepted)", f"{stats['total_files']}")
    c2.metric("Estimated Tokens", f"{stats['total_tokens']:,}")
    c3.metric("Storage Used", human_size(stats["total_bytes"]))
    c4.metric("Duplicates Skipped", f"{stats['duplicates']}")

    st.markdown("### By Contract")
    if stats["by_contract"]:
        table = []
        for label, v in stats["by_contract"].items():
            table.append({
                "Contract": label,
                "Files": v["files"],
                "Est. Tokens": f"{v['est_tokens']:,}",
                "Total Size": human_size(v["size_bytes"]),
            })
        st.dataframe(table, use_container_width=True)
    else:
        st.info("No accepted files yet. Go to **Contribute** to upload.")

    st.markdown("### Recent Uploads")
    recent = sorted(rows_all, key=lambda r: r["uploaded_at"], reverse=True)[:20]
    if recent:
        st.dataframe(recent, use_container_width=True)
    else:
        st.caption("No uploads recorded yet.")

# =========================
# Page: Contribute (Uploader)
# =========================
elif page == "Contribute":
    st.markdown("## CODEXA — Contributor")
    st.caption("Select contract → read & accept → set metadata → upload multiple files → dedupe → clean & auto-metadata → manifest")

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
        language_manual = lang_other.strip() if lang_choice == "Other" and lang_other.strip() else lang_choice
    with c2:
        genre_choice = st.selectbox("Genre", GENRE_CHOICES, index=0)
        genre_other = st.text_input("If 'Other', specify genre", "")
        genre = genre_other.strip() if genre_choice == "Other" and genre_other.strip() else genre_choice

    tags_input = st.text_input("Tags (comma-separated)", placeholder="e.g., wwii, history, primary-source")
    tags = parse_tags(tags_input)

    st.write("### Upload files")
    files = st.file_uploader(
        "Drag & drop here or click to browse",
        type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    process = st.button("Upload", disabled=not (accepted_terms and files))

    if process:
        index = load_index()
        accepted_rows, duplicate_rows = [], []
        total_tokens = 0

        prog = st.progress(0)
        total = len(files)
        for i, f in enumerate(files, start=1):
            raw = f.getvalue()
            sha_raw = sha256_bytes(raw)

            if sha_raw in index:
                prev = index[sha_raw]
                est = estimate_tokens(f.name, raw)
                duplicate_rows.append({
                    "filename": f.name,
                    "size_bytes": len(raw),
                    "size_pretty": human_size(len(raw)),
                    "est_tokens": est["tokens"],
                    "duplicate_of": prev.get("path"),
                    "uploaded_at_original": prev.get("uploaded_at"),
                    "contract": prev.get("contract_label", "Training Only"),
                    "language": prev.get("language", ""),
                    "genre": prev.get("genre", ""),
                    "tags": prev.get("tags", []),
                })
            else:
                ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                clean_name = f.name.replace(" ", "_")
                save_path = os.path.join(ORIG, f"{ts}__{clean_name}")
                with open(save_path, "wb") as out:
                    out.write(raw)

                est = estimate_tokens(f.name, raw)
                total_tokens += est["tokens"]

                # optional universal extraction + cleaning + auto-metadata
                std_path = None
                auto_title = None
                auto_year = None
                auto_lang = None

                extracted_txt = extract_text_from_file(save_path, f.name)
                if extracted_txt and st.session_state.auto_clean_on_upload:
                    cleaned = clean_text(extracted_txt, pii=st.session_state.pii_scrub)
                    std_path = write_clean_file(STD, ts, f.name, cleaned)
                    auto_title = derive_title(f.name, cleaned)
                    auto_year = extract_year(f.name, cleaned)
                    auto_lang = guess_language_simple(cleaned)
                elif extracted_txt:
                    # no cleaning, but still save standardized text
                    std_path = write_clean_file(STD, ts, f.name, extracted_txt)
                    auto_title = derive_title(f.name, extracted_txt)
                    auto_year = extract_year(f.name, extracted_txt)
                    auto_lang = guess_language_simple(extracted_txt)

                index[sha_raw] = {
                    "path": save_path,
                    "original_name": f.name,
                    "contract_label": contract_label,
                    "contract_key": contract_key,
                    "uploaded_at": ts,
                    "size_bytes": len(raw),
                    "est_tokens": est["tokens"],
                    "est_words": est["words"],
                    "status": "ok",
                    "language": language_manual,
                    "genre": genre,
                    "tags": tags,
                    "paths": {"original": save_path, "standard": std_path} if std_path else {"original": save_path},
                    "auto_title": auto_title,
                    "auto_year": auto_year,
                    "auto_language_guess": auto_lang,
                }
                accepted_rows.append({
                    "filename": f.name,
                    "size_bytes": len(raw),
                    "size_pretty": human_size(len(raw)),
                    "est_words": est["words"],
                    "est_tokens": est["tokens"],
                    "saved_as": save_path,
                    "uploaded_at": ts,
                    "contract": contract_label,
                    "language": language_manual,
                    "genre": genre,
                    "tags": ", ".join(tags),
                    "auto_title": auto_title or "",
                    "auto_year": auto_year or "",
                    "cleaned_saved": bool(std_path),
                })
            prog.progress(i / total)

        save_index(index)

        st.success(f"Done! {len(accepted_rows)} uploaded, {len(duplicate_rows)} duplicates skipped.")
        st.write(f"**Batch estimated tokens:** {total_tokens:,}")

        if accepted_rows:
            st.write("**Accepted files**")
            st.dataframe(accepted_rows, use_container_width=True)

        if duplicate_rows:
            st.write("**Duplicates (skipped)**")
            st.dataframe(duplicate_rows, use_container_width=True)

        # Batch manifest (JSON)
        batch_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        manifest = {
            "batch_id": f"batch_{batch_id}",
            "contract": contract_key,
            "contract_label": contract_label,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "accepted": accepted_rows,
            "duplicates": duplicate_rows,
            "batch_est_tokens": total_tokens
        }
        buf_json = io.BytesIO(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        st.download_button(
            "Download Manifest (JSON)",
            data=buf_json,
            file_name=f"manifest_{batch_id}.json",
            mime="application/json"
        )

    st.markdown("---")
    st.caption("Exact dedupe via SHA-256 • Token estimate: txt/md by words; others by bytes ≈ bytes/4. "
               "PDF/DOCX extraction uses optional pypdf/python-docx if installed.")

# =========================
# Page: Library (filters + custom query manifest + Full Access universal viewer)
# =========================
elif page == "Library":
    st.markdown("## Library — Accepted Files")
    st.caption("Browse everything that passed dedupe. Build and download manifests of filtered views. Full-text reading via cleaned output where available.")

    ix = load_index()
    rows = index_to_rows(ix, include_non_ok=False)

    # Unique values for filters
    all_languages = sorted({r["language"] for r in rows if r["language"]})
    all_genres = sorted({r["genre"] for r in rows if r["genre"]})
    all_years = sorted({r["year"] for r in rows if r["year"]})

    # Filters (Filename, Contract, Language, Genre, Year)
    c1, c2, c3, c4, c5 = st.columns([2, 1.2, 1.2, 1.2, 1.0])
    with c1:
        q = st.text_input("Filter by filename (contains):", "")
    with c2:
        contract_filter = st.multiselect("Contracts", CONTRACT_LABELS, default=CONTRACT_LABELS)
    with c3:
        lang_filter = st.multiselect("Language", all_languages, default=None)
    with c4:
        genre_filter = st.multiselect("Genre", all_genres, default=None)
    with c5:
        year_filter = st.multiselect("Year", all_years, default=None)

    # Apply primary filters
    filtered = rows[:]
    if q:
        filtered = [r for r in filtered if q.lower() in r["filename"].lower()]
    if contract_filter:
        filtered = [r for r in filtered if (r["contract_label"] or "Training Only") in contract_filter]
    if lang_filter:
        filtered = [r for r in filtered if r["language"] in lang_filter]
    if genre_filter:
        filtered = [r for r in filtered if r["genre"] in genre_filter]
    if year_filter:
        filtered = [r for r in filtered if r["year"] in year_filter]

    # Tags filter (ANY/ALL)
    with st.expander("🔎 Tags filter (optional)"):
        tf1, tf2 = st.columns([3, 1])
        with tf1:
            tags_input = st.text_input("Tags (comma-separated)", placeholder="e.g., wwii, primary-source")
        with tf2:
            match_all = st.checkbox("Match ALL tags", value=False, help="Unchecked = match ANY")

        def parse_tags_local(s: str): return [t.strip() for t in (s or "").split(",") if t.strip()]
        tag_list = parse_tags_local(tags_input)
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
        table_rows = [
            {
                "filename": r["filename"],
                "contract": r["contract_label"],
                "language": r["language"],
                "genre": r["genre"],
                "year": r["year"] if r["year"] else "",
                "tags": r["tags_display"],
                "title": r["title"],
                "cleaned": "✅" if r["has_cleaned"] else "—",
                "size": r["size_pretty"],
                "est_tokens": r["est_tokens"],
                "uploaded_at": r["uploaded_at"],
                "sha256": r["sha256"],
                "path": r["path"],
            }
            for r in filtered
        ]
        st.dataframe(table_rows, use_container_width=True)

        # ---- Custom Query Manifest builder ----
        st.markdown("### Build Custom Manifest from Current Filters")
        query_name = st.text_input("Optional manifest name", value="")
        # Capture the current query spec
        query_spec = {
            "filename_contains": q or "",
            "contracts": contract_filter,
            "languages": lang_filter or [],
            "genres": genre_filter or [],
            "years": year_filter or [],
            "tags": tag_list if 'tag_list' in locals() else [],
            "tags_match": "all" if ('match_all' in locals() and match_all) else "any",
        }

        # Deterministic order (by filename then sha)
        filtered_sorted = sorted(filtered, key=lambda r: (r["filename"].lower(), r["sha256"]))

        # Manifest payload (prefer standard path if available)
        lib_id = datetime.utcnow().strftime("manifest_%Y%m%dT%H%M%SZ")
        manifest_files = []
        for r in filtered_sorted:
            manifest_files.append({
                "filename": r["filename"],
                "path_original": r["path"],
                "path_standard": r["std_path"],
                "size_bytes": r["size_bytes"],
                "est_tokens": r["est_tokens"],
                "uploaded_at": r["uploaded_at"],
                "contract": r["contract_key"],
                "contract_label": r["contract_label"],
                "language": r["language"],
                "genre": r["genre"],
                "year": r["year"],
                "title": r["title"],
                "tags": r["tags"],
                "sha256_raw": r["sha256"]
            })
        manifest = {
            "manifest_id": lib_id if not query_name else f"{lib_id}__{re.sub(r'[^A-Za-z0-9_-]+','-',query_name)[:40]}",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "query": query_spec,
            "stats": {
                "files": len(manifest_files),
                "total_est_tokens": sum(r["est_tokens"] for r in filtered_sorted),
                "total_size_bytes": sum(r["size_bytes"] for r in filtered_sorted),
            },
            "files": manifest_files,
            "version": "v0.4"
        }
        buf = io.BytesIO(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        st.download_button(
            "Download Custom Manifest (JSON)",
            data=buf,
            file_name=f"{manifest['manifest_id']}.json",
            mime="application/json"
        )

        # ---- Inline viewing (prefer cleaned text; Full Access or admin override) ----
        st.write("### View file (Full Access only)")
        sel = st.selectbox("Choose a file", ["-- select --"] + [r["filename"] for r in filtered_sorted])
        if sel and sel != "-- select --":
            r = next((x for x in filtered_sorted if x["filename"] == sel), None)
            if r:
                can_view = (r["contract_key"] == "full_access") or st.session_state.admin_override_view
                if not can_view:
                    st.warning("This file is not marked 'Full Access' and cannot be viewed inline.")
                else:
                    # Prefer cleaned text if available
                    text_to_show = None
                    if r["std_path"] and os.path.exists(r["std_path"]):
                        try:
                            with open(r["std_path"], "r", encoding="utf-8") as f:
                                text_to_show = f.read()
                        except Exception:
                            text_to_show = None
                    if text_to_show is None:
                        # fallback: show raw for txt/md only
                        ext = os.path.splitext(r["filename"])[1].lower()
                        if ext in (".txt", ".md"):
                            try:
                                with open(r["path"], "rb") as f:
                                    text_to_show = f.read().decode("utf-8", errors="ignore")
                            except Exception:
                                text_to_show = None

                    # Download originals (always)
                    try:
                        with open(r["path"], "rb") as f:
                            file_bytes = f.read()
                        st.download_button("⬇️ Download original file", data=file_bytes, file_name=r["filename"])
                    except Exception as e:
                        st.error(f"Download error: {e}")

                    if text_to_show:
                        # paginate
                        CHUNK = 50000
                        total_pages = max(1, (len(text_to_show) + CHUNK - 1) // CHUNK)
                        if total_pages > 1:
                            col1, col2 = st.columns([1, 4])
                            with col1:
                                page_idx = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
                            with col2:
                                st.caption(f"{len(text_to_show):,} characters • {total_pages} page(s) at {CHUNK} chars/page")
                            start = (page_idx - 1) * CHUNK
                            end = start + CHUNK
                            chunk = text_to_show[start:end]
                        else:
                            chunk = text_to_show
                        st.code(chunk if chunk else "(empty)", language="text")
                    else:
                        st.info("No cleaned text available yet for this file type. Install optional extractors (pypdf, python-docx) and re-clean from Admin.")

# =========================
# Page: Admin (export/import, delete, recompute, overrides, cleaning)
# =========================
else:
    st.markdown("## Admin")
    st.caption("Prototype controls — export/import index, delete files, recompute estimates, preview overrides, cleaning controls.")

    # Row 0: toggles
    t1, t2, t3 = st.columns(3)
    with t1:
        st.checkbox("Auto-clean on upload", key="auto_clean_on_upload")
    with t2:
        st.checkbox("PII scrub (emails/phones/URLs)", key="pii_scrub")
    with t3:
        st.checkbox("Allow inline preview for non–Full Access (dev mode)", key="admin_override_view")
        if st.session_state.admin_override_view:
            st.warning("Dev override ENABLED — inline previews ignore contract gate.")

    st.markdown("---")

    # Row 1: clear & export
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🧹 Clear All Saved Data (originals + standard + dedupe index)", use_container_width=True):
            if os.path.isdir(ORIG):
                shutil.rmtree(ORIG, ignore_errors=True)
            if os.path.isdir(STD):
                shutil.rmtree(STD, ignore_errors=True)
            os.makedirs(ORIG, exist_ok=True)
            os.makedirs(STD, exist_ok=True)
            if os.path.exists(INDEX_PATH):
                os.remove(INDEX_PATH)
            st.success("Cleared storage/original, storage/standard, and dedupe index.")

        # Export index
        ix = load_index()
        buf = io.BytesIO(json.dumps(ix, ensure_ascii=False, indent=2).encode("utf-8"))
        st.download_button("⬇️ Export dedupe_index.json", data=buf, file_name="dedupe_index.json", mime="application/json")

    with c2:
        # Import index (replace)
        up = st.file_uploader("Replace dedupe_index.json", type=["json"], accept_multiple_files=False, key="admin_index_upload")
        if up and st.button("Replace Index with Uploaded JSON"):
            try:
                new_ix = json.loads(up.getvalue().decode("utf-8"))
                save_index(new_ix)
                st.success("Index replaced from uploaded JSON.")
            except Exception as e:
                st.error(f"Failed to import index: {e}")

    st.markdown("---")

    # Row 2: delete + recompute + (re)clean + rebuild metadata
    ix = load_index()
    rows = index_to_rows(ix, include_non_ok=True)

    if rows:
        d1, d2 = st.columns(2)

        with d1:
            st.subheader("Delete a File")
            label_map = [f"{r['filename']}  ({r['sha256'][:10]}…)" for r in rows]
            sel = st.selectbox("Select file", ["-- select --"] + label_map, key="admin_delete_select")
            if sel and sel != "-- select --":
                chosen = rows[label_map.index(sel)]
                if st.button("❌ Delete Selected File", type="primary"):
                    try:
                        if os.path.exists(chosen["path"]):
                            os.remove(chosen["path"])
                        if chosen.get("std_path") and os.path.exists(chosen["std_path"]):
                            os.remove(chosen["std_path"])
                    except Exception:
                        pass
                    if chosen["sha256"] in ix:
                        del ix[chosen["sha256"]]
                        save_index(ix)
                    st.success(f"Deleted {chosen['filename']} and updated index.")

        with d2:
            st.subheader("Recompute token/word estimates")
            if st.button("🔁 Recompute for all files"):
                changed = 0
                ix2 = load_index()
                for sha, meta in ix2.items():
                    p = meta.get("path")
                    fn = meta.get("original_name", "")
                    if not p or not os.path.exists(p):
                        continue
                    try:
                        with open(p, "rb") as f:
                            raw = f.read()
                        est = estimate_tokens(fn, raw)
                        meta["est_tokens"] = est["tokens"]
                        meta["est_words"] = est["words"]
                        changed += 1
                    except Exception:
                        continue
                save_index(ix2)
                st.success(f"Recomputed estimates for {changed} file(s).")

        st.markdown("### Cleaning & Metadata Utilities")
        u1, u2 = st.columns(2)
        with u1:
            if st.button("🧽 Re-clean all (extract → clean → save standard)"):
                ix3 = load_index()
                cleaned = 0
                for sha, meta in ix3.items():
                    p = meta.get("path")
                    fn = meta.get("original_name", "")
                    ts = meta.get("uploaded_at", datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"))
                    if not p or not os.path.exists(p):
                        continue
                    txt = extract_text_from_file(p, fn)
                    if not txt:
                        continue
                    ctxt = clean_text(txt, pii=st.session_state.pii_scrub) if st.session_state.auto_clean_on_upload else txt
                    std_path = write_clean_file(STD, ts, fn, ctxt)
                    meta.setdefault("paths", {})["standard"] = std_path
                    meta["auto_title"] = derive_title(fn, ctxt)
                    meta["auto_year"] = extract_year(fn, ctxt)
                    meta["auto_language_guess"] = guess_language_simple(ctxt)
                    cleaned += 1
                save_index(ix3)
                st.success(f"Re-clean complete for {cleaned} file(s).")

        with u2:
            if st.button("🧭 Rebuild auto-metadata (title/year/language guess)"):
                ix4 = load_index()
                updated = 0
                for sha, meta in ix4.items():
                    pstd = meta.get("paths", {}).get("standard")
                    fn = meta.get("original_name", "")
                    source_text = None
                    if pstd and os.path.exists(pstd):
                        try:
                            with open(pstd, "r", encoding="utf-8") as f:
                                source_text = f.read()
                        except Exception:
                            source_text = None
                    if not source_text:
                        # try original for txt/md only
                        p = meta.get("path")
                        if p and os.path.splitext(fn)[1].lower() in (".txt", ".md"):
                            try:
                                with open(p, "rb") as f:
                                    source_text = f.read().decode("utf-8", errors="ignore")
                            except Exception:
                                source_text = None
                    if not source_text:
                        continue
                    meta["auto_title"] = derive_title(fn, source_text)
                    meta["auto_year"] = extract_year(fn, source_text)
                    meta["auto_language_guess"] = guess_language_simple(source_text)
                    updated += 1
                save_index(ix4)
                st.success(f"Rebuilt metadata for {updated} file(s).")
    else:
        st.caption("No entries to manage yet.")
