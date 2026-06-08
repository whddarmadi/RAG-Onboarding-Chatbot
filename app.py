import os
import time
import streamlit as st
from datetime import datetime
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from groq import Groq
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# Konfigurasi Perusahaan
# ============================================================
COMPANY_NAME    = "Katering Yeyeti"
COLLECTION_NAME = "katering_yeyeti"

# ============================================================
# Setup halaman
# ============================================================
st.set_page_config(
    page_title=f"Asisten Informasi Karyawan — {COMPANY_NAME}",
    page_icon="🤖",
    layout="centered"
)

# ============================================================
# Load model & koneksi
# ============================================================
@st.cache_resource
def load_resources():
    embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    qdrant = QdrantClient(
        url=st.secrets["QDRANT_URL"],
        api_key=st.secrets["QDRANT_API_KEY"],
    )
    groq_client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    return embedder, qdrant, groq_client

@st.cache_resource
def load_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(st.secrets["SPREADSHEET_ID"]).sheet1
    return sheet

embedder, qdrant, groq_client = load_resources()
sheet = load_sheets()

# ============================================================
# Fungsi Log ke Google Sheets
# ============================================================
def log_to_sheets(pertanyaan, jawaban, response_time):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([
            timestamp,
            pertanyaan,
            jawaban,
            COMPANY_NAME,
            f"{response_time:.2f}"
        ])
    except Exception as e:
         st.warning(f"Log gagal: {e}")

# ============================================================
# Fungsi RAG Chat
# ============================================================
def rag_chat(pertanyaan: str, top_k: int = 5):
    query_vector = embedder.encode(pertanyaan).tolist()

    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
    ).points

    context = "\n\n".join([r.payload["text"] for r in results])

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {
                "role": "system",
                "content": f"""Kamu adalah Asisten Informasi Karyawan di {COMPANY_NAME}.
Tugasmu adalah membantu karyawan baru memahami informasi seputar perusahaan.

Kamu HANYA menjawab pertanyaan berdasarkan dokumen internal perusahaan, meliputi:
- Profil, visi, misi, dan nilai-nilai perusahaan
- Hak dan kewajiban karyawan
- Peraturan dan kebijakan kerja
- Jam kerja, shift, dan sistem absensi
- Benefit, santunan, dan kesejahteraan karyawan
- Prosedur dan SOP operasional dapur
- Standar kebersihan dan keselamatan dapur
- Kebijakan halal dan standar bahan baku
- Produk dan layanan perusahaan
- Penanganan keluhan pelanggan
- Pelaporan insiden dan form harian

Gunakan bahasa Indonesia yang ramah, hangat, dan mudah dipahami oleh semua kalangan.
Jika informasi tidak ada di dokumen, katakan dengan jujur bahwa kamu tidak tahu."""
            },
            {
                "role": "user",
                "content": f"KONTEKS:\n{context}\n\nPERTANYAAN:\n{pertanyaan}"
            }
        ]
    )
    return response.choices[0].message.content

# ============================================================
# UI Streamlit
# ============================================================
st.title("🤖 Asisten Informasi Karyawan")
st.subheader(f"{COMPANY_NAME}")
st.caption("Tanyakan apa saja tentang perusahaan, peraturan kerja, hak karyawan, dan prosedur operasional.")
st.divider()

# Chat history
if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.messages.append({
        "role": "assistant",
        "content": f"Halo! Saya Asisten Informasi Karyawan {COMPANY_NAME}. Saya siap membantu Anda memahami informasi seputar kebijakan, prosedur, dan budaya kerja di sini. Ada yang ingin Anda tanyakan? 😊"
    })

# Tampilkan chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input pertanyaan
if pertanyaan := st.chat_input("Ketik pertanyaan kamu di sini..."):
    st.session_state.messages.append({"role": "user", "content": pertanyaan})
    with st.chat_message("user"):
        st.markdown(pertanyaan)

    with st.chat_message("assistant"):
        with st.spinner("Mencari jawaban..."):
            start_time = time.time()
            jawaban = rag_chat(pertanyaan)
            response_time = time.time() - start_time

        st.markdown(jawaban)
        st.caption(f"⏱️ {response_time:.2f} detik")
        st.session_state.messages.append({"role": "assistant", "content": jawaban})

    log_to_sheets(pertanyaan, jawaban, response_time)
