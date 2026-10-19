"""Tác vụ hạ hạng thành viên chạy độc lập trên VPS."""
from app import app, process_inactive_member_ranks

if __name__ == "__main__":
    with app.app_context():
        changed = process_inactive_member_ranks()
        print(f"[MEMBER RANK DAILY OK] downgraded={changed}", flush=True)
