from pathlib import Path
from supabase import create_client
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().strip('"').strip("'")
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip().strip('"').strip("'")

if not SUPABASE_URL:
    raise RuntimeError("Thiếu SUPABASE_URL trong file .env")

if not SUPABASE_KEY:
    raise RuntimeError("Thiếu SUPABASE_KEY trong file .env")

if not SUPABASE_URL.startswith("https://") or ".supabase.co" not in SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL không đúng. Ví dụ đúng: https://xxxxxxxxxxxxxxxxxxxx.supabase.co"
    )

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)