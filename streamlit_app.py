import streamlit as st
import os
from datetime import datetime

st.set_page_config(page_title="CodEXA Prototype", layout="centered")

st.markdown("## CODexa — Prototype Uploader")
st.caption("Select contract → accept → upload → see confirmation")

contract = st.selectbox("Contract", ["Pretrain v1 (prototype only)"])
accepted = st.checkbox("I accept the contract terms")

file = st.file_uploader("Drag & drop here or click to browse",
                        type=["pdf", "docx", "txt", "md"],
                        label_visibility="collapsed")

upload_btn = st.button("Upload", disabled=not (accepted and file))

SAVE_DIR = "storage/original"
os.makedirs(SAVE_DIR, exist_ok=True)

if upload_btn:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    clean_name = file.name.replace(" ", "_")
    save_path = os.path.join(SAVE_DIR, f"{ts}__{clean_name}")
    with open(save_path, "wb") as f:
        f.write(file.getbuffer())

    st.success("✅ File uploaded successfully!")
    st.write(f"**Contract:** {contract}")
    st.write(f"**Saved as:** `{save_path}`")
    st.write(f"**Original filename:** `{file.name}`")
    st.write(f"**Size:** {len(file.getvalue()):,} bytes")
    st.balloons()

st.markdown("---")
st.caption("Prototype build — next steps: dedupe, clean, and manifest generation.")
