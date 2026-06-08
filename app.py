import os
import streamlit as st
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from groq import Groq

# ============================================================
# Konfigurasi Perusahaan
# ============================================================
COMPANY_NAME    = "Katering Yeyeti"
COLLECTION_NAME = "katering_yeyeti"

# ============================================================
# Setup halaman
# ============================================================
st.set_page_config(
    page_title=f"Chatbot Onboarding — {COMPANY_NAME}",
    page_icon="🤖",
    layout="centered"
)

# ============================================================
# Load model & koneksi (cache biar ga reload terus)
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

embedder, qdrant, groq_client = load_resources()

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
                "content": f"""Kamu adalah asisten onboarding karyawan baru di {COMPANY_NAME}.
Jawab pertanyaan HANYA berdasarkan konteks dokumen internal perusahaan yang diberikan.
Gunakan bahasa Indonesia yang ramah dan mudah dipahami.
Jika informasi tidak ada di konteks, katakan dengan jujur bahwa kamu tidak tahu."""
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
st.title("🤖 Chatbot Onboarding")
st.subheader(f"{COMPANY_NAME}")
st.caption("Tanyakan apa saja tentang perusahaan, benefit, prosedur, dan kebijakan kerja.")
st.divider()

# Chat history
if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.messages.append({
        "role": "assistant",
        "content": f"Halo! Saya asisten onboarding {COMPANY_NAME}. Ada yang bisa saya bantu?"
    })

# Tampilkan chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input pertanyaan
if pertanyaan := st.chat_input("Ketik pertanyaan kamu di sini..."):
    # Tampilkan pertanyaan user
    st.session_state.messages.append({"role": "user", "content": pertanyaan})
    with st.chat_message("user"):
        st.markdown(pertanyaan)

    # Generate jawaban
    with st.chat_message("assistant"):
        with st.spinner("Mencari jawaban..."):
            jawaban = rag_chat(pertanyaan)
        st.markdown(jawaban)
        st.session_state.messages.append({"role": "assistant", "content": jawaban})
