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
LLM_MODEL       = "llama-3.1-8b-instant"

TOP_K               = 5     # jumlah chunk default per query
TOP_K_MENU          = 12    # chunk lebih banyak untuk pertanyaan menu (agar lengkap)
MAX_HISTORY_TURNS   = 3     # jumlah pesan terakhir yang diingat (multi-turn memory)
LOW_CONF_THRESHOLD  = 0.30  # ambang skor relevansi rendah (bisa di-tune)

# Kata kunci yang memicu retrieval lebih banyak (agar menu terjelaskan lengkap)
MENU_KEYWORDS = [
    "menu", "harga", "paket", "nasi box", "tumpeng", "liwet",
    "peyek", "pax", "pesan", "porsi", "varian", "rice bowl", "katalog"
]

# ============================================================
# Setup halaman
# ============================================================
st.set_page_config(
    page_title=f"Asisten Informasi Karyawan — {COMPANY_NAME}",
    page_icon="🤖",
    layout="centered"
)

# ============================================================
# Load model & koneksi (di-cache agar hanya dijalankan sekali)
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
# System Prompt — Advanced Anti-Hallucination
# (memuat temuan dari sesi adversarial testing)
# ============================================================
SYSTEM_PROMPT = f"""Kamu adalah Asisten Informasi Karyawan di {COMPANY_NAME}.
Tugasmu adalah membantu karyawan baru memahami informasi seputar perusahaan.

═══════════ ATURAN UTAMA (WAJIB DIPATUHI) ═══════════

1. SUMBER JAWABAN
   Jawab HANYA berdasarkan KONTEKS DOKUMEN yang diberikan di tiap pertanyaan.
   DILARANG menambah informasi dari pengetahuan umum di luar dokumen.
   Jika informasi tidak ada di konteks, katakan jujur dan jelas:
   "Maaf, informasi tersebut tidak saya temukan di dokumen internal. Untuk kepastian,
   silakan tanyakan langsung ke Ibu Titi atau Supervisor."
   Jika memungkinkan, sebutkan nama dokumen sumber jawabanmu (mis. "Menurut Kebijakan
   Kesejahteraan Karyawan...").

2. SINGKATAN & ISTILAH — JANGAN MENGARANG
   JANGAN PERNAH menebak atau mengarang kepanjangan singkatan yang tidak didefinisikan
   secara eksplisit di konteks. Ini fatal, terutama pada hal-hal terkait kehalalan.
   Jika sebuah singkatan tidak dijelaskan di konteks, jawab:
   "Singkatan tersebut tidak dijelaskan rinci di dokumen. Silakan konfirmasi ke Ibu Titi
   atau Supervisor."
   Catatan: di dokumen Yeyeti, RPH = Rumah Potong Hewan. Gunakan HANYA kepanjangan resmi
   ini; jangan pernah mengarang kepanjangan lain apa pun.

3. FAKTA SPESIFIK (asal daerah, sejarah, sertifikasi, angka)
   JANGAN mengklaim asal daerah suatu menu, fakta sejarah, atau detail sertifikasi
   jika TIDAK disebutkan eksplisit di konteks.
   Contoh: jangan klaim sebuah menu "khas Betawi/Jawa" hanya karena ada di kategori
   tertentu. Jika asal daerah tidak disebut di dokumen, katakan tidak ada informasinya.
   Jangan menyimpulkan asal daerah hanya dari nama bahan (mis. "teri Medan" bukan berarti
   hidangannya berasal dari Medan).

4. HAK & BENEFIT KARYAWAN — JELASKAN SELENGKAP MUNGKIN
   Pertanyaan tentang hak, benefit, cuti, izin, santunan, atau "kelonggaran syar'i"
   adalah PENTING bagi karyawan baru — ini hak mereka.
   Gabungkan SEMUA informasi relevan dari konteks dan jelaskan lengkap. Jangan menjawab
   "tidak tahu" jika ada petunjuk yang relevan.
   Definisi: "kelonggaran syar'i" = izin kemanusiaan TANPA potong absensi, berlaku untuk
   menjenguk/mengantar keluarga atau rekan kerja yang sakit, mengurus pemakaman, menikah,
   hamil, melahirkan, dan menyusui.

5. MENU & PRODUK — JELASKAN DETAIL, LENGKAP, TERSTRUKTUR
   Saat ditanya tentang menu, sebutkan SELURUH menu yang ada di konteks — JANGAN hanya
   sebagian. Susun rapi per kategori, contoh:
   - Nasi Box Reguler (beserta isi & harga jika ada)
   - Menu Istimewa Khas Betawi & Jawa
   - Nasi Tumpeng & Nasi Liwet Tampah (beserta pilihan pax)
   - Rice Bowl Nusantara
   - Peyek Yeyeti
   Sertakan harga, isi, dan minimum pax HANYA jika tertera di konteks.
   JANGAN mengarang nama menu, harga, atau item yang tidak ada di konteks.

6. KONSISTENSI ANTAR GILIRAN
   Perhatikan riwayat percakapan. Jangan menyebut suatu istilah di satu giliran lalu
   mengaku tidak mengenalnya di giliran berikutnya. Jika kamu sudah menyebut sesuatu,
   jelaskan saat ditanya lanjutan.

7. GAYA BAHASA
   Gunakan bahasa Indonesia yang ramah, hangat, dan mudah dipahami oleh semua kalangan.
   Untuk daftar (menu, hak, prosedur), gunakan poin/penomoran agar rapi dan jelas.

═══════════ CAKUPAN TOPIK ═══════════
Profil/visi/misi/nilai · Hak & kewajiban karyawan · Peraturan & kebijakan kerja ·
Jam kerja/shift/absensi & kode kehadiran · Benefit/santunan/kesejahteraan & kelonggaran
syar'i · SOP operasional dapur · Standar kebersihan & keselamatan · Kebijakan halal &
standar bahan baku · Produk/menu/layanan · Penanganan keluhan pelanggan · Pelaporan
insiden & form laporan harian."""

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
# Query Contextualization
# Mengubah pertanyaan lanjutan (mis. "itu apa?") menjadi pertanyaan
# mandiri berdasarkan riwayat, agar retrieval ke Qdrant lebih akurat.
# ============================================================
def contextualize_query(pertanyaan, history):
    if not history:
        return pertanyaan

    recent = history[-4:]
    history_text = "\n".join(
        [f"{m['role']}: {m['content'][:250]}" for m in recent]
    )

    try:
        resp = groq_client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Tugasmu: tulis ulang PERTANYAAN TERAKHIR user menjadi satu "
                        "pertanyaan mandiri yang lengkap, berdasarkan riwayat percakapan, "
                        "agar bisa dicari di basis dokumen. Jika pertanyaan sudah mandiri, "
                        "kembalikan apa adanya. Jawab HANYA dengan pertanyaan hasil tulis "
                        "ulang, tanpa penjelasan atau tanda kutip."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Riwayat percakapan:\n{history_text}\n\n"
                        f"Pertanyaan terakhir: {pertanyaan}\n\n"
                        f"Pertanyaan mandiri:"
                    )
                }
            ]
        )
        rewritten = resp.choices[0].message.content.strip()
        return rewritten if rewritten else pertanyaan
    except Exception:
        return pertanyaan

# ============================================================
# Pilih top_k secara dinamis (menu butuh lebih banyak chunk)
# ============================================================
def pilih_top_k(query: str) -> int:
    q = query.lower()
    if any(kw in q for kw in MENU_KEYWORDS):
        return TOP_K_MENU
    return TOP_K

# ============================================================
# Fungsi RAG Chat dengan Multi-turn Memory
# ============================================================
def rag_chat(pertanyaan: str, history: list):
    # 1. Ubah pertanyaan lanjutan menjadi pertanyaan mandiri untuk retrieval
    search_query = contextualize_query(pertanyaan, history)

    # 2. Tentukan jumlah chunk & lakukan vector search
    top_k = pilih_top_k(search_query)
    query_vector = embedder.encode(search_query).tolist()

    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
    ).points

    avg_score = sum(r.score for r in results) / len(results) if results else 0
    context = "\n\n".join([r.payload["text"] for r in results])

    # 3. Susun pesan: system prompt + memori percakapan + pertanyaan + konteks
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Multi-turn memory: sertakan beberapa giliran terakhir
    for m in history[-MAX_HISTORY_TURNS:]:
        messages.append({"role": m["role"], "content": m["content"]})

    # Pertanyaan saat ini + konteks dokumen yang relevan
    messages.append({
        "role": "user",
        "content": f"KONTEKS:\n{context}\n\nPERTANYAAN:\n{pertanyaan}"
    })

    # 4. Generate jawaban (temperature rendah agar lebih faktual)
    response = groq_client.chat.completions.create(
        model=LLM_MODEL,
        temperature=0.3,
        messages=messages
    )
    return response.choices[0].message.content, avg_score

# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.header("ℹ️ Tentang Asisten")
    st.write(
        f"Asisten Informasi Karyawan **{COMPANY_NAME}** berbasis RAG. "
        "Menjawab pertanyaan karyawan baru berdasarkan dokumen internal perusahaan."
    )
    st.divider()
    st.caption("Fitur aktif:")
    st.caption("✅ Multi-turn memory (mengingat percakapan)")
    st.caption("✅ Anti-halusinasi (guardrail dokumen)")
    st.caption("✅ Penjelasan menu lengkap & detail")
    st.divider()
    if st.button("🔄 Reset Percakapan", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ============================================================
# UI Streamlit
# ============================================================
st.title("🤖 Asisten Informasi Karyawan")
st.subheader(f"{COMPANY_NAME}")
st.caption("Tanyakan apa saja tentang perusahaan, peraturan kerja, hak karyawan, dan prosedur operasional.")
st.divider()

# Inisialisasi chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

if not st.session_state.messages:
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
    # Riwayat percakapan SEBELUM pertanyaan ini
    history = st.session_state.messages.copy()

    st.session_state.messages.append({"role": "user", "content": pertanyaan})
    with st.chat_message("user"):
        st.markdown(pertanyaan)

    with st.chat_message("assistant"):
        with st.spinner("Mencari jawaban..."):
            start_time = time.time()
            jawaban, score = rag_chat(pertanyaan, history)
            response_time = time.time() - start_time

        st.markdown(jawaban)

        # Caption waktu + peringatan keyakinan rendah (jika skor relevansi rendah)
        if score < LOW_CONF_THRESHOLD:
            st.caption(
                f"⏱️ {response_time:.2f} detik · ⚠️ Keyakinan rendah — "
                "sebaiknya konfirmasi ke Ibu Titi atau Supervisor."
            )
        else:
            st.caption(f"⏱️ {response_time:.2f} detik")

        st.session_state.messages.append({"role": "assistant", "content": jawaban})

    log_to_sheets(pertanyaan, jawaban, response_time)
