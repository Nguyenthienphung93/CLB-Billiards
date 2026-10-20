import os
import math
import uuid
import re
import unicodedata
import hmac
import json
import copy
import time
import io
import tarfile
import hashlib
import zipfile
import threading
from urllib.parse import quote_plus
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    g,
    flash,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
    session,
)
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-key")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(os.getenv("SESSION_COOKIE_SECURE", "1").strip() != "0"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or ""
).strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Thiếu SUPABASE_URL hoặc SUPABASE_KEY/SUPABASE_SERVICE_ROLE_KEY trong file .env"
    )


VIETNAM_TZ = timezone(timedelta(hours=7))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE_NAME = "Thanhvien"
INVOICE_TABLE = "Hoadon"
ACTIVITY_TABLE = "Ban_hoat_dong"
PROMOTION_TABLE = "Khuyenmai"
PROMOTION_MILESTONE_TABLE = "Khuyenmai_moc"
PROMOTION_REDEEM_TABLE = "Khuyenmai_doiqua"
PROMOTION_VOUCHER_TABLE = "Khuyenmai_voucher"
ADMIN_TABLE = "admin_info"

# GitHub giữ club_setup.json làm SƯỜN. Dữ liệu từng CLB đặt NGOÀI thư mục git.
SETUP_TEMPLATE_FILE = BASE_DIR / "club_setup.json"
SETUP_DATA_DIR = Path(os.getenv("CLUB_SETUP_DATA_DIR") or (BASE_DIR.parent / (BASE_DIR.name + "_instance_data"))).resolve()
SETUP_POINTER_FILE = SETUP_DATA_DIR / "active_setup.json"
SETUP_CONFIG_VERSION = 1
_SETUP_LOCK = threading.RLock()


# ==========================================================
# BACKUP SYSTEM
# - Lưu logical backup vào Supabase Storage (private bucket)
# - Auto: 00:00 / 12:00 / 18:00, giữ 9 bản gần nhất
# - Manual/Safety giữ riêng, không làm mất nhịp 9 bản tự động
# ==========================================================
BACKUP_BUCKET = (os.getenv("BACKUP_BUCKET") or "system-backups").strip()
BACKUP_AUTO_PREFIX = (os.getenv("BACKUP_AUTO_PREFIX") or "automatic").strip("/")
BACKUP_MANUAL_PREFIX = (os.getenv("BACKUP_MANUAL_PREFIX") or "manual").strip("/")
BACKUP_SAFETY_PREFIX = (os.getenv("BACKUP_SAFETY_PREFIX") or "safety").strip("/")
BACKUP_AUTO_KEEP = max(1, int(os.getenv("BACKUP_AUTO_KEEP") or 9))
BACKUP_MANUAL_KEEP = max(1, int(os.getenv("BACKUP_MANUAL_KEEP") or 3))
BACKUP_SAFETY_KEEP = max(1, int(os.getenv("BACKUP_SAFETY_KEEP") or 3))
BACKUP_EXPORT_PAGE_SIZE = 1000

BACKUP_TABLES = (
    TABLE_NAME,
    INVOICE_TABLE,
    ACTIVITY_TABLE,
    PROMOTION_TABLE,
    PROMOTION_MILESTONE_TABLE,
    PROMOTION_REDEEM_TABLE,
    PROMOTION_VOUCHER_TABLE,
    "Thanhvien_hang_lichsu",
    ADMIN_TABLE,
)

# Xóa theo thứ tự con -> cha; khôi phục theo thứ tự cha -> con.
BACKUP_DELETE_ORDER = (
    "Thanhvien_hang_lichsu",
    PROMOTION_REDEEM_TABLE,
    PROMOTION_VOUCHER_TABLE,
    PROMOTION_MILESTONE_TABLE,
    INVOICE_TABLE,
    ACTIVITY_TABLE,
    PROMOTION_TABLE,
    TABLE_NAME,
    ADMIN_TABLE,
)

BACKUP_RESTORE_ORDER = (
    ADMIN_TABLE,
    TABLE_NAME,
    "Thanhvien_hang_lichsu",
    PROMOTION_TABLE,
    PROMOTION_MILESTONE_TABLE,
    INVOICE_TABLE,
    PROMOTION_REDEEM_TABLE,
    PROMOTION_VOUCHER_TABLE,
    ACTIVITY_TABLE,
)

# Cache cấu hình JSON theo mtime để tránh đọc file lặp lại ở mọi request.
_SETUP_CACHE = {"path": None, "mtime_ns": None, "data": None}

DEFAULT_MEMBER_RANKS = [
    {
        "code": "NEW",
        "name": "Khách hàng mới",
        "icon": "🆕",
        "min_plays": 1,
        "min_spend": 1000,
        "discount_percent": 0,
        "inactive_months": 0,
        "gift_text": "Thành viên nhận được 1 chai nước suối.",
        "birthday_text": "Nhận được voucher giảm giá trong tháng sinh nhật.",
    },
    {
        "code": "LOYAL",
        "name": "Khách hàng thân thiết",
        "icon": "💚",
        "min_plays": 6,
        "min_spend": 1000000,
        "discount_percent": 5,
        "inactive_months": 3,
        "gift_text": "Thành viên nhận được 1 chai nước suối.",
        "birthday_text": "Nhận được voucher giảm giá trong tháng sinh nhật.",
    },
    {
        "code": "BRONZE",
        "name": "Khách hàng đồng",
        "icon": "🥉",
        "min_plays": 11,
        "min_spend": 3000000,
        "discount_percent": 10,
        "inactive_months": 2,
        "gift_text": "Thành viên nhận được 1 chai nước suối.",
        "birthday_text": "Nhận được voucher giảm giá trong tháng sinh nhật.",
    },
    {
        "code": "SILVER",
        "name": "Khách hàng bạc",
        "icon": "🥈",
        "min_plays": 16,
        "min_spend": 5500000,
        "discount_percent": 15,
        "inactive_months": 2,
        "gift_text": "Thành viên nhận được 1 chai nước suối.",
        "birthday_text": "Nhận được voucher giảm giá trong tháng sinh nhật.",
    },
    {
        "code": "GOLD",
        "name": "Khách hàng vàng",
        "icon": "🥇",
        "min_plays": 21,
        "min_spend": 9000000,
        "discount_percent": 20,
        "inactive_months": 1,
        "gift_text": "Thành viên nhận được 1 chai nước suối.",
        "birthday_text": "Nhận được voucher giảm giá trong tháng sinh nhật.",
    },
    {
        "code": "DIAMOND",
        "name": "Khách hàng kim cương",
        "icon": "💎",
        "min_plays": 26,
        "min_spend": 15000000,
        "discount_percent": 25,
        "inactive_months": 1,
        "gift_text": "Thành viên nhận được 1 chai nước suối.",
        "birthday_text": "Nhận được voucher giảm giá trong tháng sinh nhật.",
    },
]

DEFAULT_SETUP = {
    "club": {
        "title": "Câu Lạc Bộ Billiards",
        "name": "Thanh Trung",
        "table_count": 12,
    },
    "pricing": [
        {
            "label": "Buổi sáng",
            "start": "00:00",
            "end": "12:00",
            "price_per_hour": 40000,
        },
        {
            "label": "Buổi trưa – chiều",
            "start": "12:00",
            "end": "18:00",
            "price_per_hour": 60000,
        },
        {
            "label": "Buổi tối",
            "start": "18:00",
            "end": "24:00",
            "price_per_hour": 90000,
        },
    ],
    "services": [
        {"code": "nuoc_loc", "name": "Nước lọc", "price": 10000},
        {"code": "tra_da", "name": "Trà đá", "price": 5000},
        {"code": "ca_phe_den", "name": "Cà phê đen", "price": 18000},
        {"code": "ca_phe_sua", "name": "Cà phê sữa", "price": 22000},
        {"code": "sting", "name": "Sting", "price": 15000},
        {"code": "coca", "name": "Coca Cola", "price": 15000},
        {"code": "pepsi", "name": "Pepsi", "price": 15000},
        {"code": "7up", "name": "7Up", "price": 15000},
        {"code": "mi_goi", "name": "Mì gói", "price": 18000},
        {"code": "my_trung", "name": "Mỳ trứng", "price": 25000},
        {"code": "xuc_xich", "name": "Xúc xích", "price": 15000},
        {"code": "banh_mi", "name": "Bánh mì", "price": 20000},
        {"code": "khoai_tay", "name": "Khoai tây chiên", "price": 30000},
    ],
    "discounts": {"percent_enabled": True, "max_percent": 100},
    "payment": {
        "account_name": "",
        "bank_code": "",
        "bank_name": "",
        "account_number": "",
        "note_template": "Khách lẻ hoặc mã Thành Viên",
    },
    "member_ranks": json.loads(json.dumps(DEFAULT_MEMBER_RANKS, ensure_ascii=False)),
    "rank_accumulation_months": 24,
}

BANK_OPTIONS = [
    {"code": "VCB", "name": "Vietcombank"},
    {"code": "BIDV", "name": "BIDV"},
    {"code": "ICB", "name": "VietinBank"},
    {"code": "MB", "name": "MB Bank"},
    {"code": "TCB", "name": "Techcombank"},
    {"code": "ACB", "name": "ACB"},
    {"code": "VPB", "name": "VPBank"},
    {"code": "TPB", "name": "TPBank"},
    {"code": "VIB", "name": "VIB"},
    {"code": "STB", "name": "Sacombank"},
    {"code": "HDB", "name": "HDBank"},
    {"code": "OCB", "name": "OCB"},
    {"code": "MSB", "name": "MSB"},
    {"code": "SHB", "name": "SHB"},
    {"code": "SEAB", "name": "SeABank"},
    {"code": "LPB", "name": "LPBank"},
    {"code": "VAB", "name": "VietABank"},
    {"code": "NAB", "name": "Nam A Bank"},
]


def _deep_copy_json(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False
        )
    )


def normalize_setup_config(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    result = _deep_copy_json(DEFAULT_SETUP)

    club = raw.get("club")
    if isinstance(club, dict):
        result["club"].update({
            key: value
            for key, value in club.items()
            if key in result["club"]
        })

    try:
        result["club"]["table_count"] = max(
            1,
            min(
                100,
                int(
                    result["club"].get("table_count")
                    or 12
                )
            )
        )
    except (TypeError, ValueError):
        result["club"]["table_count"] = 12

    pricing = raw.get("pricing")
    if isinstance(pricing, list) and pricing:
        clean_pricing = []

        for item in pricing:
            if not isinstance(item, dict):
                continue

            label = clean_text(
                item.get("label")
            )
            start = clean_text(
                item.get("start")
            )
            end = clean_text(
                item.get("end")
            )

            try:
                price = max(
                    0,
                    int(
                        item.get("price_per_hour")
                        or 0
                    )
                )
            except (TypeError, ValueError):
                price = 0

            if label and start and end:
                clean_pricing.append({
                    "label": label,
                    "start": start,
                    "end": end,
                    "price_per_hour": price,
                })

        if clean_pricing:
            result["pricing"] = clean_pricing

    services = raw.get("services")
    if isinstance(services, list):
        clean_services = []

        for index, item in enumerate(services):
            if not isinstance(item, dict):
                continue

            name = clean_text(
                item.get("name")
            )

            if not name:
                continue

            code = clean_text(
                item.get("code")
            ) or (
                "dv_"
                + uuid.uuid4().hex[:8]
            )

            try:
                price = max(
                    0,
                    int(
                        item.get("price")
                        or 0
                    )
                )
            except (TypeError, ValueError):
                price = 0

            clean_services.append({
                "code": code,
                "name": name,
                "price": price,
            })

        result["services"] = clean_services

    discounts = raw.get("discounts")
    if isinstance(discounts, dict):
        result["discounts"]["percent_enabled"] = bool(discounts.get("percent_enabled", True))
        try:
            result["discounts"]["max_percent"] = max(1, min(100, int(discounts.get("max_percent", 100))))
        except (TypeError, ValueError):
            pass

    payment = raw.get("payment")
    if isinstance(payment, dict):
        for key in result["payment"]:
            if key in payment:
                result["payment"][key] = clean_text(
                    payment.get(key)
                )

    # Thời hạn chung cho mọi hạng; 0 = không giới hạn.
    try:
        result["rank_accumulation_months"] = max(0, min(240, int(raw.get("rank_accumulation_months", 24) or 0)))
    except (ValueError, TypeError):
        result["rank_accumulation_months"] = 24

    raw_ranks = raw.get("member_ranks")
    clean_ranks = []

    for index, default_rank in enumerate(DEFAULT_MEMBER_RANKS):
        source = {}

        if isinstance(raw_ranks, list):
            for candidate in raw_ranks:
                if (
                    isinstance(candidate, dict)
                    and clean_text(candidate.get("code")).upper()
                    == default_rank["code"]
                ):
                    source = candidate
                    break

            if not source and index < len(raw_ranks) and isinstance(raw_ranks[index], dict):
                source = raw_ranks[index]

        def _rank_int(key, default_value=0, minimum=0, maximum=None):
            try:
                value = int(float(source.get(key, default_value)))
            except (TypeError, ValueError):
                value = int(default_value)
            value = max(minimum, value)
            if maximum is not None:
                value = min(maximum, value)
            return value

        clean_ranks.append({
            "code": default_rank["code"],
            "name": clean_text(source.get("name")) or default_rank["name"],
            "icon": clean_text(source.get("icon")) or default_rank["icon"],
            "min_plays": _rank_int("min_plays", default_rank["min_plays"], 0, 1000000),
            "min_spend": _rank_int("min_spend", default_rank["min_spend"], 0, 1000000000000),
            "discount_percent": _rank_int("discount_percent", default_rank["discount_percent"], 0, 100),
            "inactive_months": _rank_int("inactive_months", default_rank["inactive_months"], 0, 120),
            "gift_text": clean_text(source.get("gift_text")) or default_rank["gift_text"],
            "birthday_text": clean_text(source.get("birthday_text")) or default_rank["birthday_text"],
        })

    result["member_ranks"] = clean_ranks

    # Giữ lại các trường chưa được phiên bản này biết đến, phục vụ rollback và nâng cấp.
    def preserve_unknown(source, destination):
        if isinstance(source, dict) and isinstance(destination, dict):
            for key, value in source.items():
                if key not in destination:
                    destination[key] = _deep_copy_json(value)
                elif isinstance(value, dict) and isinstance(destination[key], dict):
                    preserve_unknown(value, destination[key])
        return destination

    preserve_unknown(raw, result)
    # Cấu hình chỉ nâng version khi migration của ứng dụng hỗ trợ version đó.
    result["config_version"] = max(SETUP_CONFIG_VERSION, int(raw.get("config_version") or 0)) if str(raw.get("config_version") or "0").isdigit() else SETUP_CONFIG_VERSION
    return result


def _setup_read_json(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"JSON không phải đối tượng: {path.name}")
    return result


def _setup_write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _setup_slug(name):
    name = remove_vietnamese_accents(str(name or ""))
    slug = re.sub(r"[^A-Za-z0-9]+", "", name)
    return slug[:65] or "Billiards"


def _setup_active_path(create=True):
    # active_setup.json chỉ chứa basename; không cho phép traversal đến file tùy ý.
    if SETUP_POINTER_FILE.exists():
        pointer = _setup_read_json(SETUP_POINTER_FILE)
        filename = pointer.get("active_setup_file")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_-]+_club_setup\.json", filename):
            raise RuntimeError("active_setup.json không hợp lệ. Không dùng Setup mặc định để thay thế.")
        active = SETUP_DATA_DIR / filename
        if not active.is_file():
            raise RuntimeError(f"Thiếu cấu hình riêng {filename}. Cần khôi phục backup.")
        return active

    if not create:
        return None

    # Nếu file chỉ dẫn mất nhưng còn cấu hình riêng: không tạo lại từ GitHub.
    if SETUP_DATA_DIR.exists() and any(SETUP_DATA_DIR.glob("*_club_setup.json")):
        raise RuntimeError("Thiếu active_setup.json trong khi có Setup riêng. Cần khôi phục file chỉ dẫn.")

    # Chỉ chạy một lần: copy cấu hình đang tồn tại (kể cả Setup đã thay đổi).
    # Không mặc định hóa khi file mẫu đang lỗi.
    if SETUP_TEMPLATE_FILE.exists():
        initial = _setup_read_json(SETUP_TEMPLATE_FILE)
    else:
        initial = _deep_copy_json(DEFAULT_SETUP)

    normalized = normalize_setup_config(initial)
    SETUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    slug = _setup_slug(normalized.get("club", {}).get("name"))
    filename = f"{slug}_club_setup.json"
    i = 1
    while (SETUP_DATA_DIR / filename).exists():
        filename = f"{slug}_{i}_club_setup.json"
        i += 1
    path = SETUP_DATA_DIR / filename
    _setup_write_json_atomic(path, normalized)
    _setup_write_json_atomic(SETUP_POINTER_FILE, {"active_setup_file": filename, "first_save_done": False})
    return path


def _setup_upgrade_config(path, raw):
    """Bổ sung cấu hình mới, không thay thế giá trị mà CLB đã chọn."""
    version = raw.get("config_version", 0)
    try:
        version = int(version or 0)
    except (ValueError, TypeError):
        raise RuntimeError("config_version không hợp lệ; cần kiểm tra Setup.")
    if version > SETUP_CONFIG_VERSION:
        raise RuntimeError("Cấu hình được tạo bởi phiên bản ứng dụng mới hơn. Không được hạ phiên bản tự động.")
    upgraded = normalize_setup_config(raw)
    if upgraded != raw:
        # Bảo lưu bản gốc trước khi migration. Không ghi đè backup cũ.
        backup = path.with_name(path.stem + ".pre_migration_" + datetime.now(VIETNAM_TZ).strftime("%Y%m%d_%H%M%S_%f") + ".json")
        _setup_write_json_atomic(backup, raw)
        _setup_write_json_atomic(path, upgraded)
    return upgraded


def load_setup_config():
    with _SETUP_LOCK:
        path = _setup_active_path()
        stat = path.stat()
        if (_SETUP_CACHE.get("path") == str(path) and
                _SETUP_CACHE.get("mtime_ns") == stat.st_mtime_ns and
                _SETUP_CACHE.get("data") is not None):
            return copy.deepcopy(_SETUP_CACHE["data"])
        # Nếu JSON lỗi hoặc biến mất: báo lỗi; tuyệt đối không fallback về GitHub.
        raw = _setup_read_json(path)
        config = _setup_upgrade_config(path, raw)
        _SETUP_CACHE.update({"path": str(path), "mtime_ns": path.stat().st_mtime_ns,
                             "data": copy.deepcopy(config)})
        return copy.deepcopy(config)


def save_setup_config(config):
    with _SETUP_LOCK:
        path = _setup_active_path()
        # Nếu config này do route Setup cập nhật, giữ các khóa tương lai chưa hỗ trợ.
        old = _setup_read_json(path)
        if int(old.get("config_version") or 0) > SETUP_CONFIG_VERSION:
            raise RuntimeError("Không thể lưu Setup phiên bản mới hơn ứng dụng.")
        normalized = normalize_setup_config(config)
        # Phải backup trước mọi lần ghi: bảo vệ cả lỗi thao tác từ Admin.
        backup = path.with_name(path.stem + ".previous.json")
        _setup_write_json_atomic(backup, old)
        pointer = _setup_read_json(SETUP_POINTER_FILE)
        if not pointer.get("first_save_done", True):
            # Lần Lưu đầu tiên: đổi tên file theo tên CLB được chọn.
            slug = _setup_slug(normalized.get("club", {}).get("name"))
            new_path = SETUP_DATA_DIR / f"{slug}_club_setup.json"
            suffix = 1
            while new_path != path and new_path.exists():
                new_path = SETUP_DATA_DIR / f"{slug}_{suffix}_club_setup.json"
                suffix += 1
            if new_path != path:
                _setup_write_json_atomic(new_path, normalized)
                _setup_write_json_atomic(SETUP_POINTER_FILE, {"active_setup_file": new_path.name, "first_save_done": True})
                path.unlink()
                path = new_path
            else:
                _setup_write_json_atomic(path, normalized)
                _setup_write_json_atomic(SETUP_POINTER_FILE, {"active_setup_file": path.name, "first_save_done": True})
        else:
            _setup_write_json_atomic(path, normalized)
        _SETUP_CACHE.update({"path": str(path), "mtime_ns": path.stat().st_mtime_ns,
                             "data": copy.deepcopy(normalized)})
        return copy.deepcopy(normalized)


def setup_storage_info():
    path = _setup_active_path()
    return {"filename": path.name, "directory": str(SETUP_DATA_DIR),
            "version": load_setup_config().get("config_version", SETUP_CONFIG_VERSION)}


MEMBER_RANK_ALIASES = [
    {"khách hàng mới", "thành viên mới", "chưa xếp hạng"},
    {"khách hàng thân thiết", "thành viên thân thiết"},
    {"khách hàng đồng", "thành viên đồng"},
    {"khách hàng bạc", "thành viên bạc"},
    {"khách hàng vàng", "thành viên vàng"},
    {"khách hàng kim cương", "thành viên kim cương"},
]


def normalize_rank_name(value):
    return re.sub(r"\\s+", " ", clean_text(value).lower()).strip()


def get_member_rank_index(value, ranks=None):
    ranks = ranks if isinstance(ranks, list) and ranks else DEFAULT_MEMBER_RANKS
    normalized = normalize_rank_name(value)

    if not normalized:
        return 0

    for index, rank in enumerate(ranks):
        if normalize_rank_name(rank.get("name")) == normalized:
            return index

    for index, aliases in enumerate(MEMBER_RANK_ALIASES):
        if normalized in aliases:
            return min(index, len(ranks) - 1)

    return 0


def member_rank_display_name(value, ranks=None):
    ranks = ranks if isinstance(ranks, list) and ranks else DEFAULT_MEMBER_RANKS
    index = get_member_rank_index(value, ranks)
    return clean_text(ranks[index].get("name")) or DEFAULT_MEMBER_RANKS[index]["name"]


def parse_clock_minutes(value):
    text = clean_text(value)

    if text == "24:00":
        return 24 * 60

    match = re.fullmatch(
        r"([01]?\d|2[0-3]):([0-5]\d)",
        text
    )

    if not match:
        raise ValueError(
            "Giờ phải đúng định dạng HH:MM; cuối ngày có thể dùng 24:00."
        )

    return (
        int(match.group(1)) * 60
        + int(match.group(2))
    )


def round_table_money_1000(value):
    """
    Làm tròn tiền bàn đến 1.000đ theo quy tắc CLB:
    - phần dư <= 500đ: làm xuống
    - phần dư > 500đ: làm lên

    Ví dụ:
    24.500 -> 24.000
    24.500,0001 -> 25.000
    24.666,67 -> 25.000
    """
    try:
        amount = max(
            0.0,
            float(value or 0)
        )
    except (TypeError, ValueError):
        amount = 0.0

    thousands = math.floor(
        amount / 1000
    )

    remainder = (
        amount
        - thousands * 1000
    )

    if remainder > 500:
        thousands += 1

    return int(
        thousands * 1000
    )


def format_minutes_detail(minutes):
    minutes = max(
        0,
        int(minutes or 0)
    )

    hours = minutes // 60
    remain = minutes % 60

    if hours and remain:
        return (
            f"{hours} giờ "
            f"{remain} phút"
        )

    if hours:
        return f"{hours} giờ"

    return f"{remain} phút"


def calculate_table_fee_detail(
    start_dt,
    end_dt,
    pricing=None
):
    """
    Chia thời gian chơi theo từng khung giá trong Setup.

    Tiền từng dòng:
        đơn giá/giờ * số phút / 60

    Sau đó từng dòng được làm tròn về 1.000đ theo quy tắc:
        <= 500đ làm xuống, > 500đ làm lên.

    Số phút tính tiền là số phút nguyên của toàn phiên (ceil).
    Nếu phiên cắt qua nhiều khung giá, số phút được phân bổ theo
    tỷ lệ thời gian thực tế nhưng tổng số phút luôn bằng đúng
    thoi_luong_choi_phut của hóa đơn.
    """
    if (
        not start_dt
        or not end_dt
        or end_dt <= start_dt
    ):
        return {
            "tong_tien": 0,
            "tong_phut": 0,
            "chi_tiet": [],
        }

    pricing = (
        pricing
        if isinstance(pricing, list)
        else load_setup_config().get(
            "pricing",
            []
        )
    )

    bands = []

    for item in pricing:
        try:
            start_min = parse_clock_minutes(
                item.get("start")
            )

            end_min = parse_clock_minutes(
                item.get("end")
            )

            price = max(
                0,
                int(
                    item.get(
                        "price_per_hour"
                    )
                    or 0
                )
            )

        except Exception:
            continue

        if end_min <= start_min:
            continue

        bands.append({
            "label":
                clean_text(
                    item.get("label")
                )
                or (
                    f"{item.get('start')} - "
                    f"{item.get('end')}"
                ),

            "start":
                clean_text(
                    item.get("start")
                ),

            "end":
                clean_text(
                    item.get("end")
                ),

            "start_min":
                start_min,

            "end_min":
                end_min,

            "price":
                price,
        })

    if not bands:
        return {
            "tong_tien": 0,
            "tong_phut": 0,
            "chi_tiet": [],
        }

    current_start = (
        start_dt
        .astimezone(
            VIETNAM_TZ
        )
    )

    current_end = (
        end_dt
        .astimezone(
            VIETNAM_TZ
        )
    )

    exact_segments = []

    day = current_start.date()

    while day <= current_end.date():

        day_start = datetime(
            day.year,
            day.month,
            day.day,
            tzinfo=VIETNAM_TZ
        )

        for band in bands:

            band_start = (
                day_start
                + timedelta(
                    minutes=
                        band["start_min"]
                )
            )

            band_end = (
                day_start
                + timedelta(
                    minutes=
                        band["end_min"]
                )
            )

            overlap_start = max(
                current_start,
                band_start
            )

            overlap_end = min(
                current_end,
                band_end
            )

            if overlap_end <= overlap_start:
                continue

            exact_seconds = (
                overlap_end
                - overlap_start
            ).total_seconds()

            exact_segments.append({
                "label":
                    band["label"],

                "khung_gio":
                    (
                        f"{band['start']} → "
                        f"{band['end']}"
                    ),

                "tu_gio":
                    band["start"],

                "den_gio":
                    band["end"],

                "don_gia_gio":
                    band["price"],

                "exact_seconds":
                    exact_seconds,

                "exact_minutes":
                    (
                        exact_seconds
                        / 60
                    ),
            })

        day += timedelta(
            days=1
        )

    if not exact_segments:
        return {
            "tong_tien": 0,
            "tong_phut": 0,
            "chi_tiet": [],
        }

    total_seconds = (
        current_end
        - current_start
    ).total_seconds()

    total_minutes = max(
        1,
        math.ceil(
            total_seconds / 60
        )
    )

    # Phân bổ phút nguyên nhưng vẫn bảo đảm tổng phút không bị lệch.
    floor_sum = 0

    for segment in exact_segments:

        exact_minutes = (
            segment[
                "exact_minutes"
            ]
        )

        billed_minutes = (
            math.floor(
                exact_minutes
            )
        )

        segment[
            "so_phut"
        ] = billed_minutes

        segment[
            "_remainder"
        ] = (
            exact_minutes
            - billed_minutes
        )

        floor_sum += (
            billed_minutes
        )

    minutes_to_add = max(
        0,
        total_minutes
        - floor_sum
    )

    # Ưu tiên cộng phút vào đoạn có phần lẻ lớn nhất.
    ordered_indexes = sorted(
        range(
            len(
                exact_segments
            )
        ),
        key=lambda index:
            exact_segments[
                index
            ][
                "_remainder"
            ],
        reverse=True
    )

    for index in (
        ordered_indexes[
            :minutes_to_add
        ]
    ):
        exact_segments[
            index
        ][
            "so_phut"
        ] += 1

    detail = []
    total_amount = 0

    for segment in exact_segments:

        billed_minutes = int(
            segment.get(
                "so_phut"
            )
            or 0
        )

        if billed_minutes <= 0:
            continue

        raw_amount = (
            segment[
                "don_gia_gio"
            ]
            * billed_minutes
            / 60
        )

        rounded_amount = (
            round_table_money_1000(
                raw_amount
            )
        )

        total_amount += (
            rounded_amount
        )

        detail.append({
            "ten_khung":
                segment["label"],

            "khung_gio":
                segment[
                    "khung_gio"
                ],

            "tu_gio":
                segment["tu_gio"],

            "den_gio":
                segment["den_gio"],

            "so_phut":
                billed_minutes,

            "thoi_luong_hien_thi":
                format_minutes_detail(
                    billed_minutes
                ),

            "don_gia_gio":
                int(
                    segment[
                        "don_gia_gio"
                    ]
                ),

            "tien_chua_lam_tron":
                round(
                    raw_amount,
                    6
                ),

            "thanh_tien":
                rounded_amount,
        })

    return {
        "tong_tien":
            int(
                total_amount
            ),

        "tong_phut":
            int(
                total_minutes
            ),

        "chi_tiet":
            detail,
    }


def calculate_table_fee(
    start_dt,
    end_dt,
    pricing=None
):
    return (
        calculate_table_fee_detail(
            start_dt,
            end_dt,
            pricing
        )
        .get(
            "tong_tien",
            0
        )
    )


def normalize_table_fee_detail(value):
    """
    Chuẩn hóa chi tiết tiền bàn đọc từ JSONB/text.
    Hỗ trợ cả hóa đơn cũ chưa có chi tiết.
    """
    if isinstance(value, list):
        rows = value

    elif isinstance(value, str):
        try:
            rows = json.loads(
                value
            )
        except Exception:
            rows = []

    else:
        rows = []

    return [
        item
        for item in rows
        if isinstance(
            item,
            dict
        )
    ]


def build_payment_qr_url(config=None):
    config = config or load_setup_config()
    payment = config.get(
        "payment",
        {}
    )

    bank_code = clean_text(
        payment.get("bank_code")
    )
    account_number = clean_text(
        payment.get("account_number")
    )

    if not bank_code or not account_number:
        return ""

    account_name = clean_text(
        payment.get("account_name")
    )
    note = clean_text(
        payment.get("note_template")
    )

    return (
        "https://img.vietqr.io/image/"
        + quote_plus(bank_code)
        + "-"
        + quote_plus(account_number)
        + "-compact2.png"
        + "?accountName="
        + quote_plus(account_name)
        + "&addInfo="
        + quote_plus(note)
    )


ADMIN_USERNAME = (
    os.getenv("ADMIN_USERNAME") or ""
).strip()

ADMIN_PASSWORD = (
    os.getenv("ADMIN_PASSWORD") or ""
).strip()


# Tài khoản bảo trì hệ thống tồn tại RIÊNG TRÊN VPS, không tạo hàng trong admin_info.
SYSTEM_ADMIN_USERNAME = (os.getenv("SYSTEM_ADMIN_USERNAME") or "").strip()
SYSTEM_ADMIN_PASSWORD_HASH = (os.getenv("SYSTEM_ADMIN_PASSWORD_HASH") or "").strip()


def system_admin_enabled():
    return bool(SYSTEM_ADMIN_USERNAME and SYSTEM_ADMIN_PASSWORD_HASH)


def _system_admin_identity():
    """Không có id Supabase; chỉ phiên system_admin được tạo bởi đăng nhập .env."""
    return {
        "id": None,
        "username": SYSTEM_ADMIN_USERNAME,
        "email": None,
        "ho_va_ten": "Quản trị hệ thống",
        "active": True,
        "is_owner": True,
        "is_system_admin": True,
        "quyen_han": dict(FULL_ADMIN_PERMISSIONS),
        "quyen_han_chuan": dict(FULL_ADMIN_PERMISSIONS),
    }


def _system_admin_signature():
    """Thay đổi tài khoản/hash trong .env sẽ vô hiệu hóa phiên quản trị cũ."""
    message = (SYSTEM_ADMIN_USERNAME + "\0" + SYSTEM_ADMIN_PASSWORD_HASH).encode("utf-8")
    return hmac.new(app.secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def valid_system_admin_session():
    return (system_admin_enabled()
            and session.get("admin_logged_in") is True
            and session.get("admin_kind") == "system"
            and hmac.compare_digest(str(session.get("system_admin_signature") or ""), _system_admin_signature()))


def verify_system_admin_password(password):
    if not system_admin_enabled() or not password:
        return False
    try:
        return check_password_hash(SYSTEM_ADMIN_PASSWORD_HASH, password)
    except (ValueError, TypeError):
        return False


def find_logo_url():
    picture_dir = BASE_DIR / "static" / "pictures"
    for ext in ("png", "webp", "jpg", "jpeg", "svg"):
        candidate = picture_dir / f"logo_clb.{ext}"
        if candidate.exists():
            return url_for("static", filename=f"pictures/logo_clb.{ext}")
    return ""


@app.context_processor
def inject_layout_data():
    """
    Luôn cấp dữ liệu dùng chung cho toàn bộ template.
    Nếu file JSON lỗi/thiếu, vẫn dùng cấu hình mặc định để base.html không bị crash.
    """
    setup_config = load_setup_config()

    current_admin = None
    admin_permissions = {
        key: False
        for key in ADMIN_PERMISSION_KEYS
    }

    if session.get("admin_logged_in"):
        current_admin = get_current_admin()

        if current_admin:
            admin_permissions = (
                admin_row_permissions(
                    current_admin
                )
            )

    return {
        "logo_url": find_logo_url(),
        "app_setup": setup_config,
        "payment_qr_url": build_payment_qr_url(
            setup_config
        ),
        "current_admin": current_admin,
        "admin_permissions": admin_permissions,
        "admin_can": current_admin_can,
        "setup_unlocked": bool(session.get("setup_unlocked")),
        "setup_remaining": setup_remaining_attempts(),
        "setup_csrf": setup_csrf_token() if session.get("admin_logged_in") else "",
        "invoice_confirm_csrf": invoice_confirm_csrf_token() if session.get("admin_logged_in") else "",
        "owner_weekly_backup_csrf": owner_weekly_backup_csrf_token() if session.get("admin_logged_in") else "",
    }


def invoice_confirm_csrf_token():
    """Token riêng cho thao tác xác nhận thanh toán (không yêu cầu mở khóa Setup)."""
    if not session.get("invoice_confirm_csrf"):
        session["invoice_confirm_csrf"] = uuid.uuid4().hex + uuid.uuid4().hex
    return session["invoice_confirm_csrf"]


def clean_text(value):
    return str(value or "").strip()

def format_money_vn(value):
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError):
        number = 0

    return (
        f"{number:,}"
        .replace(",", ".")
        + " đ"
    )

def remove_vietnamese_accents(value):
    value = clean_text(value)

    value = value.replace("Đ", "D").replace("đ", "d")

    value = unicodedata.normalize("NFD", value)

    value = "".join(
        char
        for char in value
        if unicodedata.category(char) != "Mn"
    )

    return value

def format_vnd_display(value):
    """
    Chuẩn hóa giá trị tiền về dạng: 50,000 Vnđ
    """
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    lower = (
        text.lower()
        .replace("vnđ", "")
        .replace("vnd", "")
        .replace("đ", "")
        .replace(".", "")
        .replace(",", "")
        .strip()
    )

    digits = "".join(ch for ch in lower if ch.isdigit())

    if digits:
        return f"{int(digits):,} Vnđ"

    return text




def parse_money_value(value):
    """
    Đọc chuỗi tiền như:
    50000, 50,000, 50.000, 50,000 đ, 50.000 Vnđ
    """
    digits = re.sub(
        r"\D",
        "",
        clean_text(value)
    )

    if not digits:
        return 0

    try:
        return max(
            0,
            int(digits)
        )
    except ValueError:
        return 0



# ==========================================================
# ĐIỀU KIỆN SỬ DỤNG VOUCHER
# Lưu tại Khuyenmai_voucher.dieu_kien (JSONB)
# Ví dụ:
# [
#   {"loai":"TONG_TIEN","gia_tri":500000},
#   {"loai":"THOI_LUONG","gia_tri":120},
#   {"loai":"DICH_VU","gia_tri":100000}
# ]
# Với THOI_LUONG, gia_tri được lưu theo PHÚT.
# ==========================================================
def normalize_voucher_conditions(value):
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            rows = []
        else:
            try:
                rows = json.loads(raw)
            except Exception:
                rows = []
    elif isinstance(value, list):
        rows = value
    else:
        rows = []

    result = []

    for item in rows:
        if not isinstance(item, dict):
            continue

        condition_type = clean_text(
            item.get("loai")
        ).upper()

        if condition_type not in {
            "TONG_TIEN",
            "THOI_LUONG",
            "DICH_VU",
        }:
            continue

        try:
            value_num = int(
                round(
                    float(
                        item.get("gia_tri")
                        or 0
                    )
                )
            )
        except (TypeError, ValueError):
            value_num = 0

        if value_num <= 0:
            continue

        result.append({
            "loai": condition_type,
            "gia_tri": value_num,
        })

    return result


def parse_decimal_hours_to_minutes(value):
    """
    Hỗ trợ:
    2     -> 120 phút
    1.5   -> 90 phút
    1,5   -> 90 phút
    1.25  -> 75 phút
    """
    raw = clean_text(value).replace(",", ".")

    if not raw:
        return 0

    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return 0

    if hours <= 0:
        return 0

    return max(
        1,
        int(round(hours * 60))
    )


def voucher_conditions_from_form(form):
    condition_types = form.getlist(
        "dieu_kien_loai"
    )

    condition_values = form.getlist(
        "dieu_kien_gia_tri"
    )

    conditions = []

    for condition_type, raw_value in zip(
        condition_types,
        condition_values
    ):
        condition_type = clean_text(
            condition_type
        ).upper()

        if condition_type not in {
            "TONG_TIEN",
            "THOI_LUONG",
            "DICH_VU",
        }:
            continue

        if condition_type == "THOI_LUONG":
            value_num = (
                parse_decimal_hours_to_minutes(
                    raw_value
                )
            )
        else:
            value_num = (
                parse_money_value(
                    raw_value
                )
            )

        if value_num <= 0:
            continue

        conditions.append({
            "loai": condition_type,
            "gia_tri": value_num,
        })

    return conditions


def voucher_condition_line(item):
    item = item if isinstance(item, dict) else {}

    condition_type = clean_text(
        item.get("loai")
    ).upper()

    value_num = int(
        item.get("gia_tri")
        or 0
    )

    if condition_type == "TONG_TIEN":
        return (
            "Bạn phải có tổng tiền đạt ít nhất "
            f"{format_vnd_display(value_num)}."
        )

    if condition_type == "THOI_LUONG":
        return (
            "Bạn phải chơi trong thời lượng "
            f"{format_minutes_detail(value_num)} trở lên."
        )

    if condition_type == "DICH_VU":
        return (
            "Bạn phải sử dụng dịch vụ thêm ít nhất "
            f"{format_vnd_display(value_num)}."
        )

    return ""


def enrich_voucher_condition_fields(row):
    if not isinstance(row, dict):
        return row

    conditions = normalize_voucher_conditions(
        row.get("dieu_kien")
    )

    lines = [
        voucher_condition_line(item)
        for item in conditions
    ]

    lines = [
        line
        for line in lines
        if line
    ]

    row["dieu_kien"] = conditions
    row["dieu_kien_dong"] = lines
    row["dieu_kien_hien_thi"] = (
        " ".join(lines)
    )

    return row


def check_voucher_conditions(
    conditions,
    *,
    tong_tien=0,
    thoi_luong_phut=0,
    tien_dich_vu=0
):
    normalized = normalize_voucher_conditions(
        conditions
    )

    unmet = []

    for item in normalized:
        condition_type = item["loai"]
        required = int(
            item["gia_tri"]
            or 0
        )

        if (
            condition_type == "TONG_TIEN"
            and int(tong_tien or 0) < required
        ):
            unmet.append(
                voucher_condition_line(item)
            )

        elif (
            condition_type == "THOI_LUONG"
            and int(thoi_luong_phut or 0) < required
        ):
            unmet.append(
                voucher_condition_line(item)
            )

        elif (
            condition_type == "DICH_VU"
            and int(tien_dich_vu or 0) < required
        ):
            unmet.append(
                voucher_condition_line(item)
            )

    return (
        len(unmet) == 0,
        unmet
    )

def parse_percent_discount(value):
    """Đọc 10%, 10 %, 12.5%. Chỉ chuỗi có ký hiệu % mới hợp lệ."""
    match = re.search(r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*%", clean_text(value))
    if not match:
        return 0.0
    percent = float(match.group(1).replace(",", "."))
    return percent if 0 < percent <= 100 else 0.0


def discount_benefit_value(value, *, allow_bare_amount=True):
    """Phân loại ưu đãi: PERCENT hoặc AMOUNT; trả về (loại, giá trị)."""
    percent = parse_percent_discount(value)
    if percent:
        return "PERCENT", percent
    if '%' in clean_text(value):
        return "NONE", 0
    amount = (extract_money_reward_value(value) if allow_bare_amount else parse_money_value(value))
    return ("AMOUNT", amount) if amount > 0 else ("NONE", 0)


def discount_benefit_display(kind, value):
    if kind == "PERCENT":
        return f"{value:g}% đơn hàng"
    if kind == "AMOUNT":
        return format_vnd_display(value)
    return ""


def discount_benefit_amount(kind, value, subtotal):
    """Cùng công thức cho API hóa đơn và tiền preview; % trên tổng trước giảm."""
    subtotal = max(0, int(subtotal or 0))
    if kind == "PERCENT":
        return min(subtotal, int(subtotal * float(value) / 100 + 0.5))
    if kind == "AMOUNT":
        return min(subtotal, max(0, int(value or 0)))
    return 0


def extract_money_reward_value(value):
    """
    Nhận diện tên Quà tặng có bản chất là tiền / phiếu giảm tiền.

    Nhận:
      - 10000
      - 100.000
      - 100,000
      - 100000 Vnđ / vnđ / VND
      - 100.000đ / 100,000Đ
      - 50k / 100 k
      - 50 nghìn / 50 ngàn

    Tránh nhận nhầm:
      - Gấu bông nhỏ
      - 1 chai nước
      - 100 lượt
    """
    text = clean_text(value)
    if not text:
        return 0

    lower = (
        text.lower()
        .replace("vnđ", " vnd ")
        .replace("vnd", " vnd ")
        .replace("đồng", " dong ")
    )

    k_match = re.search(
        r"(?<!\d)(\d+(?:[.,]\d+)?)\s*k\b",
        lower,
        flags=re.IGNORECASE
    )
    if k_match:
        try:
            number = float(k_match.group(1).replace(",", "."))
            return max(0, int(round(number * 1000)))
        except (TypeError, ValueError):
            pass

    money_match = re.search(
        r"(?<!\d)(\d[\d.,\s]*)\s*(?:vnd|dong|đ)(?:\b|$)",
        lower,
        flags=re.IGNORECASE
    )
    if money_match:
        digits = re.sub(r"\D", "", money_match.group(1))
        if digits:
            return max(0, int(digits))

    thousand_match = re.search(
        r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:ngh[iì]n|ng[aà]n)\b",
        lower,
        flags=re.IGNORECASE
    )
    if thousand_match:
        try:
            number = float(thousand_match.group(1).replace(",", "."))
            return max(0, int(round(number * 1000)))
        except (TypeError, ValueError):
            pass

    number_matches = re.findall(
        r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{4,})(?!\d)",
        lower
    )
    for raw_number in number_matches:
        digits = re.sub(r"\D", "", raw_number)
        if not digits:
            continue
        try:
            amount = int(digits)
        except ValueError:
            continue
        if amount >= 1000:
            return amount

    return 0


def enrich_member_rewards_and_vouchers(
    redeemed_rewards,
    vouchers
):
    """
    Chuẩn hóa Quà/Voucher cho Member Portal.

    Quy tắc:
    - Quà:
        Chưa nhận quà = còn sử dụng được
        Đã đổi quà / Đã nhận quà = đã nhận

    - Voucher:
        Chưa sử dụng / Chưa kích hoạt = còn sử dụng được
        Đã sử dụng / Đã kích hoạt = đã dùng

    - Số lượng trên giao diện chỉ đếm món CHƯA SỬ DỤNG.
    - Món đã dùng vẫn hiển thị trong 3 ngày và có thời gian xác nhận.
    """

    reward_items = []
    voucher_items = []


    # =========================================================
    # QUÀ TẶNG
    # =========================================================
    for reward in redeemed_rewards or []:

        item = dict(
            reward
        )


        status = clean_text(
            item.get(
                "trang_thai"
            )
        ) or "Chưa nhận quà"


        activated_at = parse_iso_datetime(
            item.get(
                "kich_hoat_at"
            )
        )


        # -----------------------------------------
        # XÁC ĐỊNH ĐÃ NHẬN HAY CHƯA
        # -----------------------------------------
        reward_used = (
            bool(
                activated_at
            )
            or status in {
                "Đã đổi quà",
                "Đã nhận quà",
            }
        )


        item[
            "da_su_dung"
        ] = reward_used


        item[
            "con_su_dung"
        ] = not reward_used


        # Member chỉ cần nhìn:
        # Chưa nhận quà / Đã nhận quà
        if reward_used:

            item[
                "trang_thai_hien_thi"
            ] = "Đã nhận quà"

        else:

            item[
                "trang_thai_hien_thi"
            ] = "Chưa nhận quà"


        # -----------------------------------------
        # THỜI GIAN XÁC NHẬN
        # -----------------------------------------
        item[
            "kich_hoat_at_hien_thi"
        ] = (
            activated_at
            .astimezone(
                VIETNAM_TZ
            )
            .strftime(
                "%d/%m/%Y %H:%M:%S"
            )
            if activated_at
            else ""
        )


        item[
            "qr_code_value"
        ] = (
            item.get(
                "ma_qua"
            )
            or ""
        )


        item[
            "ma_hien_thi"
        ] = (
            item.get(
                "ma_qua"
            )
            or ""
        )

        item["is_money_related"] = (
            discount_benefit_value(item.get("ten_qua_tang"))[0] != "NONE"
        )
        item["uu_dai_hien_thi"] = discount_benefit_display(
            *discount_benefit_value(item.get("ten_qua_tang"))
        )


        reward_items.append(
            item
        )


    # =========================================================
    # VOUCHER
    # =========================================================
    for voucher in vouchers or []:

        item = dict(
            voucher
        )


        status = clean_text(
            item.get(
                "trang_thai"
            )
        ) or "Chưa sử dụng"


        activated_at = parse_iso_datetime(
            item.get(
                "kich_hoat_at"
            )
        )


        voucher_used = (
            bool(
                activated_at
            )
            or status in {
                "Đã sử dụng",
                "Đã kích hoạt",
            }
        )


        item[
            "da_su_dung"
        ] = voucher_used


        item[
            "con_su_dung"
        ] = not voucher_used


        # Member chỉ hiển thị 2 trạng thái chính
        if voucher_used:

            item[
                "trang_thai_hien_thi"
            ] = "Đã sử dụng"

        else:

            item[
                "trang_thai_hien_thi"
            ] = "Chưa sử dụng"


        item[
            "kich_hoat_at_hien_thi"
        ] = (
            activated_at
            .astimezone(
                VIETNAM_TZ
            )
            .strftime(
                "%d/%m/%Y %H:%M:%S"
            )
            if activated_at
            else ""
        )


        item[
            "qr_code_value"
        ] = (
            item.get(
                "ma_voucher"
            )
            or ""
        )


        item[
            "ma_hien_thi"
        ] = (
            item.get(
                "ma_voucher"
            )
            or ""
        )


        item[
            "gia_tri_hien_thi"
        ] = discount_benefit_display(*discount_benefit_value(item.get("gia_tri"), allow_bare_amount=False))

        item["is_money_related"] = (
            discount_benefit_value(item.get("gia_tri"), allow_bare_amount=False)[0] != "NONE"
        )

        enrich_voucher_condition_fields(
            item
        )


        voucher_items.append(
            item
        )


    return (
        reward_items,
        voucher_items
    )

def build_member_code(ho_va_ten, ngay_sinh, so_dien_thoai):
    ho_va_ten = clean_text(ho_va_ten)
    so_dien_thoai = re.sub(r"\D", "", clean_text(so_dien_thoai))

    birth = normalize_birthdate(ngay_sinh)

    if not ho_va_ten or not birth or len(so_dien_thoai) != 10:
        return ""

    # Tách họ tên
    words = [
        item
        for item in ho_va_ten.split()
        if item.strip()
    ]

    if not words:
        return ""

    # Các chữ cái đầu của Họ + tên lót
    initials = ""

    if len(words) > 1:
        for word in words[:-1]:
            clean_word = remove_vietnamese_accents(word)

            if clean_word:
                initials += clean_word[0].upper()

    # Toàn bộ tên chính
    last_name = remove_vietnamese_accents(
        words[-1]
    )

    last_name = re.sub(
        r"[^A-Za-z0-9]",
        "",
        last_name
    ).upper()

    # 2 số cuối năm sinh
    year_2 = birth[2:4]

    # 3 số cuối điện thoại
    phone_3 = so_dien_thoai[-3:]

    return (
        initials
        + last_name
        + year_2
        + phone_3
    )

def generate_unique_member_code(
    ho_va_ten,
    ngay_sinh,
    so_dien_thoai,
    exclude_id=None
):
    base_code = build_member_code(
        ho_va_ten,
        ngay_sinh,
        so_dien_thoai
    )

    if not base_code:
        return ""

    candidate = base_code
    suffix = 0

    while True:
        query = (
            supabase.table(TABLE_NAME)
            .select("id,ma_thanh_vien")
            .eq("ma_thanh_vien", candidate)
        )

        result = query.execute().data or []

        # Không có ai dùng mã này
        if not result:
            return candidate

        # Khi chỉnh sửa chính thành viên đó
        if exclude_id is not None:
            other_rows = [
                row
                for row in result
                if int(row.get("id") or 0)
                != int(exclude_id)
            ]

            if not other_rows:
                return candidate

        suffix += 1

        candidate = (
            f"{base_code}_{suffix}"
        )

def normalize_birthdate(value):
    value = clean_text(value)
    if not value:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def format_birthdate_display(value):
    value = clean_text(value)
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return value


def member_payload(form):
    return {
        "ma_thanh_vien": clean_text(form.get("ma_thanh_vien")),
        "ho_va_ten": clean_text(form.get("ho_va_ten")),
        "gioi_tinh": clean_text(form.get("gioi_tinh")) or None,
        "ngay_sinh": normalize_birthdate(form.get("ngay_sinh")),
        "dia_chi": clean_text(form.get("dia_chi")) or None,
        "so_dien_thoai": clean_text(form.get("so_dien_thoai")) or None,
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def validate_member(payload):
    if not payload["ho_va_ten"]:
        return "Họ và tên không được để trống."
    if payload["gioi_tinh"] not in (None, "Nam", "Nữ", "Khác"):
        return "Giới tính không hợp lệ."
    phone = re.sub(
        r"\D",
        "",
        clean_text(payload.get("so_dien_thoai"))
    )

    if not phone:
        return "Số điện thoại không được để trống."

    if len(phone) != 10:
        return "Số điện thoại phải gồm đúng 10 số."

    if not payload.get("ngay_sinh"):
        return "Ngày sinh không được để trống."
    return ""


# ==========================================================
# QUẢN TRỊ ADMIN + PHÂN QUYỀN
# ==========================================================
ADMIN_PERMISSION_KEYS = (
    "thanh_vien",
    "hoat_dong",
    "hoa_don",
    "khuyen_mai",
    "kich_hoat_qua",
    "setup",
    "quan_tri_admin",
)

FULL_ADMIN_PERMISSIONS = {
    key: True
    for key in ADMIN_PERMISSION_KEYS
}

ADMIN_PERMISSION_LABELS = (
    ("thanh_vien", "Thành Viên"),
    ("hoat_dong", "Hoạt Động"),
    ("hoa_don", "Hóa Đơn"),
    ("khuyen_mai", "Khuyến Mãi"),
    ("kich_hoat_qua", "Kích hoạt Quà/Voucher"),
    ("setup", "Setup"),
    ("quan_tri_admin", "Phân quyền Admin"),
)

ENDPOINT_PERMISSION_MAP = {
    # Thành viên
    "thanh_vien": "thanh_vien",
    "api_tim_kiem_thanh_vien": "thanh_vien",
    "api_thong_ke_thanh_vien": "thanh_vien",
    "them_thanh_vien": "thanh_vien",
    "cap_nhat_thanh_vien": "thanh_vien",
    "xoa_thanh_vien": "thanh_vien",

    # Hoạt động
    "hoat_dong": "hoat_dong",
    "api_bat_dau_ban": "hoat_dong",
    "api_luu_gio_hang": "hoat_dong",
    "api_tao_hoa_don": "hoat_dong",
    "api_uu_dai_tien_thanh_vien": "hoat_dong",

    # Hóa đơn
    "hoa_don": "hoa_don",
    "xoa_hoa_don": "hoa_don",
    "xac_nhan_thanh_toan_hoa_don": "hoa_don",

    # Khuyến mãi
    "khuyen_mai_admin": "khuyen_mai",
    "toggle_khuyen_mai": "khuyen_mai",
    "cap_nhat_khuyen_mai": "khuyen_mai",
    "tang_voucher": "khuyen_mai",
    "cap_nhat_voucher": "khuyen_mai",
    "xoa_voucher": "khuyen_mai",

    # Kích hoạt quà / voucher
    "kich_hoat_qua": "kich_hoat_qua",
    "api_tra_cuu_qua_voucher": "kich_hoat_qua",
    "xac_nhan_kich_hoat_qua": "kich_hoat_qua",

    # Setup
    "setup": "setup",
    "setup_verify": "setup",
    "setup_unlock_page": "setup",
    "setup_verify": "setup",
    "api_backup_list": "setup",
    "api_backup_create": "setup",
    "api_backup_download": "setup",
    "api_backup_restore": "setup",

    # Quản trị Admin
    "them_admin": "quan_tri_admin",
    "cap_nhat_admin": "quan_tri_admin",
    "xoa_admin": "quan_tri_admin",
}


def normalize_admin_permissions(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}

    if not isinstance(value, dict):
        value = {}

    return {
        key: bool(value.get(key, False))
        for key in ADMIN_PERMISSION_KEYS
    }


def admin_row_permissions(admin_row):
    if not isinstance(admin_row, dict):
        return {
            key: False
            for key in ADMIN_PERMISSION_KEYS
        }

    if bool(admin_row.get("is_owner")):
        return dict(FULL_ADMIN_PERMISSIONS)

    return normalize_admin_permissions(
        admin_row.get("quyen_han")
    )


# Cache Admin ngắn hạn để mỗi lần chuyển menu không phải gọi Supabase lại.
# g vẫn là cache theo request; cache dưới đây dùng giữa các request.
_ADMIN_CACHE = {}
_ADMIN_CACHE_TTL_SECONDS = 12


def invalidate_admin_cache(admin_id=None):
    if admin_id is None:
        _ADMIN_CACHE.clear()
        return
    _ADMIN_CACHE.pop(str(admin_id), None)


def get_current_admin():
    if session.get("admin_kind") == "system":
        if valid_system_admin_session():
            return _system_admin_identity()
        return None

    cached_request = getattr(g, "_current_admin", None)
    if cached_request is not None:
        return cached_request or None

    admin_id = session.get("admin_id")
    if not admin_id:
        g._current_admin = {}
        return None

    cache_key = str(admin_id)
    now_mono = time.monotonic()
    cached_global = _ADMIN_CACHE.get(cache_key)

    if cached_global:
        cached_at, cached_admin = cached_global
        if now_mono - cached_at < _ADMIN_CACHE_TTL_SECONDS:
            admin = copy.deepcopy(cached_admin) if cached_admin else None
            g._current_admin = admin or {}
            return admin

    try:
        rows = (
            supabase
            .table(ADMIN_TABLE)
            .select(
                "id,"
                "username,"
                "email,"
                "ho_va_ten,"
                "active,"
                "is_owner,"
                "quyen_han"
            )
            .eq("id", admin_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        admin = rows[0] if rows else None
        if not admin or not bool(admin.get("active")):
            _ADMIN_CACHE[cache_key] = (now_mono, None)
            g._current_admin = {}
            return None

        admin["quyen_han_chuan"] = admin_row_permissions(admin)
        _ADMIN_CACHE[cache_key] = (now_mono, copy.deepcopy(admin))
        g._current_admin = admin
        return admin

    except Exception as exc:
        print("[LOAD CURRENT ADMIN ERROR]", repr(exc))
        g._current_admin = {}
        return None


def current_admin_can(permission_key):
    permission_key = clean_text(
        permission_key
    )

    admin = get_current_admin()

    if not admin:
        return False

    if bool(admin.get("is_owner")):
        return True

    return bool(
        admin
        .get(
            "quyen_han_chuan",
            {}
        )
        .get(
            permission_key,
            False
        )
    )


def admin_permissions_from_form(form):
    permissions = {}

    for key in ADMIN_PERMISSION_KEYS:
        permissions[key] = (
            form.get(
                f"perm_{key}"
            )
            == "1"
        )

    return permissions


def first_allowed_admin_endpoint():
    preferred = (
        ("thanh_vien", "thanh_vien"),
        ("hoat_dong", "hoat_dong"),
        ("hoa_don", "hoa_don"),
        ("khuyen_mai", "khuyen_mai_admin"),
        ("kich_hoat_qua", "kich_hoat_qua"),
        ("setup", "setup"),
    )

    for permission_key, endpoint in preferred:
        if current_admin_can(permission_key):
            return endpoint

    return "admin_logout"


# Legacy ADMIN_USERNAME/ADMIN_PASSWORD migration deliberately disabled.
# CLB admins are created explicitly in Setup, never implicitly from .env.


# ==========================================================
# SETUP: xác thực lại mật khẩu Admin (tối đa 3 lần sai / phiên)
# ==========================================================
SETUP_MAX_PASSWORD_FAILURES = 3
SETUP_GUARDED_ENDPOINTS = {
    "setup", "api_backup_list", "api_backup_create",
    "api_backup_download", "api_backup_restore",
    "them_admin", "cap_nhat_admin", "xoa_admin",
}

def setup_csrf_token():
    token = session.get("setup_csrf")
    if not token:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        session["setup_csrf"] = token
    return token


def setup_remaining_attempts():
    return max(0, SETUP_MAX_PASSWORD_FAILURES - int(session.get("setup_failures", 0)))


def _setup_password_result(password, csrf):
    # Xác thực ngay trên server; không phụ thuộc popup hay localStorage.
    expected = session.get("setup_csrf", "")
    if not expected or not hmac.compare_digest(str(csrf or ""), expected):
        return {"ok": False, "message": "Phiên xác thực không hợp lệ. Vui lòng tải lại trang.",
                "remaining": setup_remaining_attempts()}, 403
    if session.get("admin_kind") == "system":
        if not valid_system_admin_session():
            return {"ok": False, "logout": True, "message": "Phiên hệ thống hết hạn."}, 401
        valid = verify_system_admin_password(password)
    else:
        admin_id = session.get("admin_id")
        if not session.get("admin_logged_in") or not admin_id:
            return {"ok": False, "logout": True, "message": "Phiên đăng nhập đã hết hạn."}, 401
        try:
            rows = (supabase.table(ADMIN_TABLE).select("password_hash,active")
                    .eq("id", admin_id).limit(1).execute().data or [])
        except Exception as exc:
            print("[SETUP VERIFY ERROR]", type(exc).__name__)
            return {"ok": False, "message": "Không thể kiểm tra mật khẩu. Vui lòng thử lại.",
                    "remaining": setup_remaining_attempts()}, 503
        admin = rows[0] if rows else None
        valid = bool(admin and admin.get("active") and admin.get("password_hash")
                     and password and check_password_hash(admin["password_hash"], password))
    if valid:
        session["setup_unlocked"] = True
        session["setup_failures"] = 0
        return {"ok": True, "message": "Xác thực thành công.", "redirect": url_for("setup")}, 200
    attempts = int(session.get("setup_failures", 0)) + 1
    session["setup_failures"] = attempts
    remaining = max(0, SETUP_MAX_PASSWORD_FAILURES - attempts)
    if remaining == 0:
        session.clear()
        return {"ok": False, "logout": True, "remaining": 0,
                "message": "Bạn đã nhập sai mật khẩu 3 lần. Hệ thống đã tự đăng xuất để bảo vệ tài khoản.",
                "redirect": url_for("admin_login")}, 401
    return {"ok": False, "logout": False, "remaining": remaining,
            "message": f"Mật khẩu không chính xác. Bạn còn {remaining} lần thử."}, 401


@app.post("/admin/setup-verify")
def setup_verify():
    data = request.get_json(silent=True) if request.is_json else request.form
    data = data or {}
    result, status = _setup_password_result(
        str(data.get("password") or ""),
        str(data.get("csrf") or request.headers.get("X-Setup-CSRF") or ""),
    )
    return jsonify(result), status


@app.route("/admin/setup-unlock", methods=["GET", "POST"])
def setup_unlock_page():
    if session.get("setup_unlocked"):
        return redirect(url_for("setup"))
    error = ""
    if request.method == "POST":
        result, status = _setup_password_result(request.form.get("password", ""), request.form.get("csrf", ""))
        if result.get("ok"):
            return redirect(url_for("setup"))
        if result.get("logout"):
            flash(result["message"], "danger")
            return redirect(url_for("admin_login"))
        error = result.get("message", "Không thể xác thực.")
    return render_template_string('''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Xác thực Setup</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Arial,sans-serif;background:#eef5fc;color:#153557}.panel{width:min(430px,92vw);background:#fff;border:1px solid #cbdff4;border-radius:20px;padding:30px;box-shadow:0 20px 60px #173b6722}h2{margin:0 0 12px}p{line-height:1.5}input{box-sizing:border-box;width:100%;padding:13px;margin:12px 0;border:1px solid #b5cbe5;border-radius:10px}button{background:#2563eb;border:0;border-radius:10px;padding:12px 20px;color:white;font-weight:700;cursor:pointer}.error{color:#b91c1c;font-weight:700}.hint{color:#b45309;font-weight:700}</style></head><body><div class="panel"><h2>🔐 Xác thực quyền truy cập Setup</h2><p>Nhập lại mật khẩu của tài khoản Admin đang đăng nhập.</p>
{% if error %}<p class="error">{{ error }}</p>{% endif %}<p class="hint">Còn {{ remaining }} lần nhập mật khẩu.</p><form method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="password" name="password" required autocomplete="current-password" placeholder="Mật khẩu Admin" autofocus><button type="submit">Xác nhận</button></form><p><a href="{{ back }}">Quay lại</a></p></div></body></html>''', error=error, remaining=setup_remaining_attempts(), csrf=setup_csrf_token(), back=url_for(first_allowed_admin_endpoint()))


@app.after_request
def setup_no_cache(response):
    if request.endpoint in SETUP_GUARDED_ENDPOINTS or request.endpoint == "setup_unlock_page":
        response.headers["Cache-Control"] = "no-store, private"
    return response


@app.route(
    "/admin-login",
    methods=["GET", "POST"]
)
def admin_login():

    if session.get("admin_logged_in"):
        admin = get_current_admin()

        if admin:
            return redirect(
                url_for(
                    first_allowed_admin_endpoint()
                )
            )

        session.clear()

    if request.method == "POST":

        login = clean_text(
            request.form.get("login")
        )

        password = str(
            request.form.get("password")
            or ""
        )

        if not login or not password:
            flash(
                "Vui lòng nhập tài khoản và mật khẩu.",
                "danger"
            )
            return render_template(
                "admin_login.html"
            )

        # Tài khoản hệ thống luôn được nhận diện trước Supabase.
        # Username trùng nhưng sai password KHÔNG được thử tài khoản CLB.
        if system_admin_enabled() and hmac.compare_digest(login.casefold(), SYSTEM_ADMIN_USERNAME.casefold()):
            if verify_system_admin_password(password):
                session.clear()
                session.permanent = True
                session["admin_logged_in"] = True
                session["admin_kind"] = "system"
                session["admin_username"] = SYSTEM_ADMIN_USERNAME
                session["admin_name"] = "Quản trị hệ thống"
                session["system_admin_signature"] = _system_admin_signature()
                flash("Đăng nhập quản trị hệ thống thành công.", "success")
                return redirect(url_for(first_allowed_admin_endpoint()))
            flash("User/Email hoặc mật khẩu không chính xác.", "danger")
            return render_template("admin_login.html")

        try:
            rows = (
                supabase
                .table(ADMIN_TABLE)
                .select(
                    "id,"
                    "username,"
                    "email,"
                    "ho_va_ten,"
                    "password_hash,"
                    "active,"
                    "is_owner,"
                    "quyen_han"
                )
                .ilike(
                    "username",
                    login
                )
                .limit(1)
                .execute()
                .data
                or []
            )

            if not rows:
                rows = (
                    supabase
                    .table(ADMIN_TABLE)
                    .select(
                        "id,"
                        "username,"
                        "email,"
                        "ho_va_ten,"
                        "password_hash,"
                        "active,"
                        "is_owner,"
                        "quyen_han"
                    )
                    .ilike(
                        "email",
                        login
                    )
                    .limit(1)
                    .execute()
                    .data
                    or []
                )

            admin = (
                rows[0]
                if rows
                else None
            )

            password_ok = (
                bool(admin)
                and bool(
                    admin.get(
                        "password_hash"
                    )
                )
                and check_password_hash(
                    admin.get(
                        "password_hash"
                    ),
                    password
                )
            )

            if (
                admin
                and bool(
                    admin.get("active")
                )
                and password_ok
            ):

                session.clear()

                session[
                    "admin_logged_in"
                ] = True

                session["admin_kind"] = "club"

                session[
                    "admin_id"
                ] = admin.get("id")

                session[
                    "admin_username"
                ] = admin.get(
                    "username"
                )

                session[
                    "admin_name"
                ] = (
                    admin.get("ho_va_ten")
                    or admin.get("username")
                )

                try:
                    supabase.table(
                        ADMIN_TABLE
                    ).update({
                        "last_login_at":
                            datetime.now(
                                VIETNAM_TZ
                            ).isoformat(),

                        "updated_at":
                            datetime.now(
                                VIETNAM_TZ
                            ).isoformat(),
                    }).eq(
                        "id",
                        admin.get("id")
                    ).execute()
                except Exception as update_exc:
                    print(
                        "[ADMIN LAST LOGIN ERROR]",
                        repr(update_exc)
                    )

                flash(
                    "Đăng nhập thành công.",
                    "success"
                )

                # Cache admin cho request hiện tại
                admin[
                    "quyen_han_chuan"
                ] = admin_row_permissions(
                    admin
                )
                g._current_admin = admin

                return redirect(
                    url_for(
                        first_allowed_admin_endpoint()
                    )
                )

            if (
                admin
                and not bool(
                    admin.get("active")
                )
            ):
                flash(
                    "Tài khoản Admin đã bị khóa.",
                    "danger"
                )
            else:
                flash(
                    "User/Email hoặc mật khẩu không chính xác.",
                    "danger"
                )

        except Exception as exc:
            print(
                "[ADMIN LOGIN ERROR]",
                repr(exc)
            )

            flash(
                "Không thể đăng nhập Admin. "
                "Vui lòng kiểm tra bảng admin_info.",
                "danger"
            )

    return render_template(
        "admin_login.html"
    )


@app.get("/admin-logout")
def admin_logout():
    session.clear()

    flash(
        "Đã đăng xuất khỏi hệ thống.",
        "success"
    )

    return redirect(
        url_for("admin_login")
    )


@app.before_request
def require_admin_login():

    allowed_endpoints = {
        "index",
        "admin_login",
        "admin_logout",

        "member_login",
        "member_information",
        "member_thongtin",
        "member_khuyenmai",
        "member_redeem_reward",
        "member_logout",

        "static",
    }

    if request.endpoint in allowed_endpoints:
        return None

    if not session.get(
        "admin_logged_in"
    ):
        return redirect(
            url_for("admin_login")
        )

    admin = get_current_admin()

    if not admin:
        session.clear()

        flash(
            "Tài khoản Admin không còn hiệu lực "
            "hoặc đã bị khóa.",
            "danger"
        )

        return redirect(
            url_for("admin_login")
        )

    permission_key = (
        ENDPOINT_PERMISSION_MAP.get(
            request.endpoint
        )
    )

    # Endpoint Admin nào chưa khai báo quyền:
    # Chủ được vào, Admin thường bị chặn để tránh lọt quyền.
    if not permission_key:
        if bool(admin.get("is_owner")):
            return None

        return (
            jsonify({
                "ok": False,
                "message":
                    "Tài khoản không có quyền "
                    "thực hiện chức năng này."
            }),
            403
        ) if request.path.startswith("/api/") else (
            render_template(
                "403.html"
            ),
            403
        )

    if not current_admin_can(
        permission_key
    ):
        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "message":
                    "Bạn không có quyền sử dụng "
                    "chức năng này."
            }), 403

        flash(
            "Tài khoản của bạn không có quyền "
            "truy cập chức năng này.",
            "danger"
        )

        return redirect(
            url_for(
                first_allowed_admin_endpoint()
            )
        )

    # Bảo vệ toàn bộ API, POST và trang Setup ngay cả khi gõ URL trực tiếp.
    if request.endpoint in SETUP_GUARDED_ENDPOINTS:
        if not session.get("setup_unlocked"):
            if request.method == "GET" and request.endpoint == "setup":
                return redirect(url_for("setup_unlock_page"))
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "message": "Cần nhập lại mật khẩu Admin để truy cập Setup.",
                                "reauth_required": True}), 403
            return redirect(url_for("setup_unlock_page")) if request.method == "GET" else ("Yêu cầu xác thực Setup trước khi thực hiện thao tác.", 403)
    elif request.endpoint not in {"setup_verify", "setup_unlock_page"}:
        # Rời khu vực Setup -> lần vào kế tiếp phải nhập lại mật khẩu.
        session.pop("setup_unlocked", None)

    return None


@app.route(
    "/member-login",
    methods=["GET", "POST"]
)
def member_login():

    # Nếu đã đăng nhập thành viên
    if session.get("member_logged_in"):
        return redirect(
            url_for("member_information")
        )

    if request.method == "POST":

        ma_thanh_vien = clean_text(
            request.form.get("ma_thanh_vien")
        ).upper()

        so_dien_thoai = re.sub(
            r"\D",
            "",
            clean_text(
                request.form.get("so_dien_thoai")
            )
        )


        if not ma_thanh_vien or not so_dien_thoai:

            flash(
                "Vui lòng nhập đầy đủ mã thành viên và số điện thoại.",
                "danger"
            )

            return render_template(
                "member-login.html"
            )


        try:

            rows = (
                supabase.table(TABLE_NAME)
                .select(
                    "id,"
                    "ma_thanh_vien,"
                    "ho_va_ten,"
                    "so_dien_thoai"
                )
                .eq(
                    "ma_thanh_vien",
                    ma_thanh_vien
                )
                .limit(1)
                .execute()
                .data
                or []
            )


            if not rows:

                flash(
                    "Mã thành viên hoặc số điện thoại không chính xác.",
                    "danger"
                )

                return render_template(
                    "member-login.html"
                )


            member = rows[0]

            saved_phone = re.sub(
                r"\D",
                "",
                clean_text(
                    member.get(
                        "so_dien_thoai"
                    )
                )
            )


            if not hmac.compare_digest(
                so_dien_thoai,
                saved_phone
            ):

                flash(
                    "Mã thành viên hoặc số điện thoại không chính xác.",
                    "danger"
                )

                return render_template(
                    "member-login.html"
                )


            # ==========================
            # ĐĂNG NHẬP THÀNH CÔNG
            # ==========================

            session[
                "member_logged_in"
            ] = True

            session[
                "member_code"
            ] = member.get(
                "ma_thanh_vien"
            )


            return redirect(
                url_for(
                    "member_information"
                )
            )


        except Exception as exc:

            print(
                "[MEMBER LOGIN ERROR]",
                repr(exc)
            )

            flash(
                "Không thể đăng nhập. Vui lòng thử lại.",
                "danger"
            )


    return render_template(
        "member-login.html"
    )

@app.get("/member-information")
def member_information():

    if not session.get(
        "member_logged_in"
    ):
        return redirect(
            url_for(
                "member_login"
            )
        )


    member_code = clean_text(
        session.get(
            "member_code"
        )
    ).upper()


    try:

        rows = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .limit(1)
            .execute()
            .data
            or []
        )


        if not rows:

            session.pop(
                "member_logged_in",
                None
            )

            session.pop(
                "member_code",
                None
            )

            flash(
                "Không tìm thấy thông tin thành viên.",
                "danger"
            )

            return redirect(
                url_for(
                    "member_login"
                )
            )


        member = rows[0]

        setup_config = load_setup_config()
        member_ranks = setup_config.get("member_ranks") or DEFAULT_MEMBER_RANKS
        member_rank_index = sync_rank_display(member, member_ranks)
        stats = rank_summary_for_member(member_code, include_expiry=True)
        member["rank_window"] = stats
        member["rank_progress_plays"] = stats["plays"]
        member["rank_progress_spend"] = stats["spend"]
        member["rank_progress_source"] = member.get("hang_nang_nguon") or ""


    except Exception as exc:

        print(
            "[MEMBER INFORMATION ERROR]",
            repr(exc)
        )

        member = None


    return render_template(
        "member_information.html",
        member=member
    )

@app.get("/member-thongtin")
def member_thongtin():

    if not session.get("member_logged_in"):
        return redirect(
            url_for("member_login")
        )

    member_code = clean_text(
        session.get("member_code")
    ).upper()

    try:
        # ==========================
        # THÔNG TIN THÀNH VIÊN
        # ==========================
        member_rows = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("ma_thanh_vien", member_code)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not member_rows:
            session.pop("member_logged_in", None)
            session.pop("member_code", None)

            flash(
                "Không tìm thấy thông tin thành viên.",
                "danger"
            )

            return redirect(
                url_for("member_login")
            )

        member = member_rows[0]

        member["ngay_sinh_hien_thi"] = (
            format_birthdate_display(
                member.get("ngay_sinh")
            )
        )

        # ==========================
        # NGÀY ĐĂNG KÝ LẦN ĐẦU
        # ==========================
        registered_at = parse_iso_datetime(
            member.get("created_at")
        )

        member["ngay_dang_ky_hien_thi"] = (
            registered_at
            .astimezone()
            .strftime("%d/%m/%Y")
            if registered_at
            else ""
        )


        # ==========================
        # CHƠI GẦN NHẤT
        # ==========================
        last_play = parse_iso_datetime(
            member.get(
                "thoi_gian_choi_gan_nhat"
            )
        )

        if last_play:
            member[
                "thoi_gian_choi_gan_nhat_hien_thi"
            ] = (
                last_play
                .astimezone()
                .strftime(
                    "%d/%m/%Y %H:%M:%S"
                )
            )

        else:
            member[
                "thoi_gian_choi_gan_nhat_hien_thi"
            ] = ""


        # ==========================
        # HÓA ĐƠN CỦA THÀNH VIÊN
        # ==========================
        member_code = clean_text(
            member.get(
                "ma_thanh_vien"
            )
        ).upper()

        invoices = (
            supabase.table(INVOICE_TABLE)
            .select("*")
            .eq(
                "ma_thanh_vien",
                member_code
            )
                        .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
            or []
        )


        # ==========================
        # FORMAT HÓA ĐƠN
        # ==========================
        for row in invoices:

            row[
                "chi_tiet_tien_ban"
            ] = normalize_table_fee_detail(
                row.get(
                    "chi_tiet_tien_ban"
                )
            )

            created_dt = parse_iso_datetime(
                row.get("created_at")
            )

            if created_dt:
                row[
                    "created_at_hien_thi"
                ] = (
                    created_dt
                    .astimezone()
                    .strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )
                )

            else:
                row[
                    "created_at_hien_thi"
                ] = ""


            # Thời lượng
            duration = int(
                row.get(
                    "thoi_luong_choi_phut"
                )
                or 0
            )

            hours = duration // 60
            minutes = duration % 60

            if hours and minutes:

                row[
                    "thoi_luong_hien_thi"
                ] = (
                    f"{hours} giờ "
                    f"{minutes} phút"
                )

            elif hours:

                row[
                    "thoi_luong_hien_thi"
                ] = f"{hours} giờ"

            else:

                row[
                    "thoi_luong_hien_thi"
                ] = f"{minutes} phút"


            # Tiền
            row[
                "so_tien_giam_hien_thi"
            ] = format_money_vn(
                row.get("so_tien_giam")
            )

            row[
                "thanh_tien_hien_thi"
            ] = format_money_vn(
                row.get("thanh_tien")
            )


        # ==========================
        # HỒ SƠ HOẠT ĐỘNG
        # ==========================
        total_spent = sum(
            int(row.get("thanh_tien") or 0)
            for row in invoices
        )

        member["so_tien_da_chi_hien_thi"] = (
            format_money_vn(total_spent)
        )

        valid_play_rows = [
            row
            for row in invoices
            if int(row.get("thoi_luong_choi_phut") or 0) > 0
        ]

        def _play_summary(row):
            if not row:
                return ""

            duration = int(
                row.get("thoi_luong_choi_phut")
                or 0
            )

            hours = duration // 60
            minutes = duration % 60

            if hours and minutes:
                duration_text = f"{hours} giờ {minutes} phút"
            elif hours:
                duration_text = f"{hours} giờ"
            else:
                duration_text = f"{minutes} phút"

            play_dt = (
                parse_iso_datetime(
                    row.get("thoi_gian_ket_thuc")
                )
                or parse_iso_datetime(
                    row.get("created_at")
                )
            )

            date_text = (
                play_dt.astimezone().strftime("%d/%m/%Y")
                if play_dt
                else "—"
            )

            return f"{date_text} ({duration_text})"

        longest_row = (
            max(
                valid_play_rows,
                key=lambda row: int(
                    row.get("thoi_luong_choi_phut")
                    or 0
                )
            )
            if valid_play_rows
            else None
        )

        shortest_row = (
            min(
                valid_play_rows,
                key=lambda row: int(
                    row.get("thoi_luong_choi_phut")
                    or 0
                )
            )
            if valid_play_rows
            else None
        )

        member["thoi_gian_choi_lau_nhat_hien_thi"] = (
            _play_summary(longest_row)
        )

        member["thoi_gian_choi_ngan_nhat_hien_thi"] = (
            _play_summary(shortest_row)
        )


    except Exception as exc:

        print(
            "[MEMBER THONGTIN ERROR]",
            repr(exc)
        )

        flash(
            "Không đọc được thông tin thành viên.",
            "danger"
        )

        return redirect(
            url_for("member_information")
        )


    return render_template(
        "member-thongtin.html",
        member=member,
        invoices=invoices
    )

def get_member_promotion_progress(
    member_code,
    promotion
):

    member_code = clean_text(
        member_code
    ).upper()

    promotion_code = clean_text(
        promotion.get(
            "ma_khuyen_mai"
        )
    )

    promo_type = clean_text(
        promotion.get(
            "loai_khuyen_mai"
        )
    )

    start_dt = parse_iso_datetime(
        promotion.get(
            "bat_dau"
        )
    )

    end_dt = parse_iso_datetime(
        promotion.get(
            "ket_thuc"
        )
    )

    query = (
        supabase
        .table(
            INVOICE_TABLE
        )
        .select(
            "id,"
            "ma_thanh_vien,"
            "created_at,"
            "thoi_luong_choi_phut,"
            "thanh_tien"
        )
        .eq(
            "ma_thanh_vien",
            member_code
        )
        .eq(
            "trang_thai",
            "Đã thanh toán"
        )
    )

    if start_dt:
        query = query.gte(
            "created_at",
            start_dt.isoformat()
        )

    if end_dt:
        query = query.lte(
            "created_at",
            end_dt.isoformat()
        )

    invoices = (
        query
        .execute()
        .data
        or []
    )

    if promo_type == "THOI_LUONG":
        total_value = sum(
            int(
                item.get(
                    "thoi_luong_choi_phut"
                )
                or 0
            )
            for item in invoices
        )

    elif promo_type == "SO_LAN":
        total_value = len(
            invoices
        )

    elif promo_type == "SO_TIEN":
        # Tích theo số tiền THỰC TRẢ của hóa đơn đã thanh toán.
        total_value = sum(
            int(
                item.get("thanh_tien")
                or 0
            )
            for item in invoices
        )

    else:
        total_value = 0

    redeem_query = (
        supabase
        .table(
            PROMOTION_REDEEM_TABLE
        )
        .select(
            "ma_qua,"
            "ma_thanh_vien,"
            "ma_khuyen_mai,"
            "gia_tri_da_dung,"
            "created_at"
        )
        .eq(
            "ma_thanh_vien",
            member_code
        )
        .eq(
            "ma_khuyen_mai",
            promotion_code
        )
    )

    if start_dt:
        redeem_query = redeem_query.gte(
            "created_at",
            start_dt.isoformat()
        )

    if end_dt:
        redeem_query = redeem_query.lte(
            "created_at",
            end_dt.isoformat()
        )

    redeemed_rows = (
        redeem_query
        .execute()
        .data
        or []
    )

    used_value = sum(
        int(
            item.get(
                "gia_tri_da_dung"
            )
            or 0
        )
        for item in redeemed_rows
    )

    current_value = max(
        0,
        total_value - used_value
    )

    return {
        "tong": total_value,
        "da_dung": used_value,
        "con_lai": current_value,
        "so_hoa_don_hop_le": len(invoices),
        "so_qua_da_quy_doi": len(redeemed_rows),
    }

def promotion_minutes_display(minutes):

    minutes = int(minutes or 0)

    hours = minutes // 60

    remain_minutes = minutes % 60

    if hours and remain_minutes:
        return (
            f"{hours} giờ "
            f"{remain_minutes} phút"
        )

    if hours:
        return f"{hours} giờ"

    return f"{remain_minutes} phút"



@app.get("/member-khuyenmai")
def member_khuyenmai():

    if not session.get(
        "member_logged_in"
    ):
        return redirect(
            url_for(
                "member_login"
            )
        )

    member_code = clean_text(
        session.get(
            "member_code"
        )
    ).upper()

    try:

        member_rows = (
            supabase
            .table(
                TABLE_NAME
            )
            .select(
                "id,"
                "ma_thanh_vien,"
                "ho_va_ten,"
                "so_dien_thoai"
            )
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not member_rows:
            session.pop(
                "member_logged_in",
                None
            )
            session.pop(
                "member_id",
                None
            )
            session.pop(
                "member_code",
                None
            )
            flash(
                "Không tìm thấy thông tin thành viên.",
                "danger"
            )
            return redirect(
                url_for(
                    "member_login"
                )
            )

        member = member_rows[0]

        member_code = clean_text(
            member.get(
                "ma_thanh_vien"
            )
        ).upper()

        session[
            "member_code"
        ] = member_code

        promotions = (
            supabase
            .table(
                PROMOTION_TABLE
            )
            .select("*")
            .order(
                "created_at"
            )
            .execute()
            .data
            or []
        )

        promo_hour = None
        promo_play = None
        promo_money = None
        promo_voucher = None

        for promo in promotions:
            promo_type = clean_text(
                promo.get(
                    "loai_khuyen_mai"
                )
            )

            if promo_type == "THOI_LUONG":
                promo_hour = promo
            elif promo_type == "SO_LAN":
                promo_play = promo
            elif promo_type == "SO_TIEN":
                promo_money = promo
            elif promo_type == "VOUCHER":
                promo_voucher = promo

        def prepare_promo(
            promo
        ):

            if not promo:
                return None

            now = (
                datetime.now()
                .astimezone()
            )

            start_dt = parse_iso_datetime(
                promo.get(
                    "bat_dau"
                )
            )

            end_dt = parse_iso_datetime(
                promo.get(
                    "ket_thuc"
                )
            )

            is_enabled = bool(
                promo.get(
                    "mo_chuong_trinh"
                )
            )

            promo_active = False
            promo[
                "trang_thai_ma"
            ] = "CHUA_MO"
            promo[
                "trang_thai_thoi_gian"
            ] = ""

            if not is_enabled:
                promo[
                    "trang_thai_ma"
                ] = "CHUA_MO"
                promo[
                    "trang_thai_thoi_gian"
                ] = "Chương trình chưa mở."

            elif start_dt and now < start_dt:
                promo[
                    "trang_thai_ma"
                ] = "SAP_DIEN_RA"
                promo[
                    "trang_thai_thoi_gian"
                ] = "Chương trình sắp diễn ra."

            elif end_dt and now > end_dt:
                promo[
                    "trang_thai_ma"
                ] = "KET_THUC"
                promo[
                    "trang_thai_thoi_gian"
                ] = "Chương trình đã kết thúc."

            else:
                promo_active = True
                promo[
                    "trang_thai_ma"
                ] = "DANG_DIEN_RA"
                promo[
                    "trang_thai_thoi_gian"
                ] = "Chương trình đang diễn ra."

            promo[
                "dang_hoat_dong"
            ] = promo_active

            promo[
                "bat_dau_hien_thi"
            ] = promotion_date_display(
                promo.get(
                    "bat_dau"
                )
            )

            promo[
                "ket_thuc_hien_thi"
            ] = promotion_date_display(
                promo.get(
                    "ket_thuc"
                )
            )

            promotion_code = clean_text(
                promo.get(
                    "ma_khuyen_mai"
                )
            )

            milestones = (
                supabase
                .table(
                    PROMOTION_MILESTONE_TABLE
                )
                .select("*")
                .eq(
                    "ma_khuyen_mai",
                    promotion_code
                )
                .eq(
                    "active",
                    True
                )
                .order(
                    "thu_tu"
                )
                .execute()
                .data
                or []
            )

            progress = (
                get_member_promotion_progress(
                    member_code,
                    promo
                )
            )

            current_value = int(
                progress.get(
                    "con_lai"
                )
                or 0
            )

            promo_type = clean_text(
                promo.get(
                    "loai_khuyen_mai"
                )
            )

            max_value = 0

            if milestones:
                max_value = max(
                    int(
                        item.get(
                            "gia_tri_moc"
                        )
                        or 0
                    )
                    for item in milestones
                )

            if max_value > 0:
                progress_percent = (
                    current_value
                    / max_value
                    * 100
                )
                progress_percent = min(
                    100,
                    max(
                        0,
                        progress_percent
                    )
                )
            else:
                progress_percent = 0

            promo[
                "progress_percent"
            ] = round(
                progress_percent,
                2
            )

            for milestone in milestones:
                target = int(
                    milestone.get(
                        "gia_tri_moc"
                    )
                    or 0
                )

                milestone[
                    "co_the_doi"
                ] = (
                    promo_active
                    and current_value >= target
                )

                if promo_type == "THOI_LUONG":
                    milestone[
                        "moc_hien_thi"
                    ] = promotion_minutes_display(
                        target
                    )
                elif promo_type == "SO_TIEN":
                    milestone[
                        "moc_hien_thi"
                    ] = format_vnd_display(
                        target
                    )
                else:
                    milestone[
                        "moc_hien_thi"
                    ] = f"{target} lần"

            promo[
                "moc"
            ] = milestones

            promo[
                "tien_do"
            ] = progress

            if promo_type == "THOI_LUONG":
                promo[
                    "tien_do_hien_thi"
                ] = promotion_minutes_display(
                    current_value
                )
            elif promo_type == "SO_TIEN":
                promo[
                    "tien_do_hien_thi"
                ] = format_vnd_display(
                    current_value
                )
            else:
                promo[
                    "tien_do_hien_thi"
                ] = f"{current_value} lần"

            return promo

        promo_hour = prepare_promo(
            promo_hour
        )

        promo_play = prepare_promo(
            promo_play
        )

        promo_money = prepare_promo(
            promo_money
        )

        vouchers = (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .select("*")
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
            or []
        )

        for voucher in vouchers:
            voucher[
                "ngay_bat_dau_hien_thi"
            ] = voucher_date_display(
                voucher.get(
                    "ngay_bat_dau"
                )
            )

            voucher[
                "ngay_het_han_hien_thi"
            ] = voucher_date_display(
                voucher.get(
                    "ngay_het_han"
                )
            )

            voucher[
                "gia_tri_hien_thi"
            ] = (
                discount_benefit_display(*discount_benefit_value(voucher.get("gia_tri"), allow_bare_amount=False))
                or clean_text(
                    voucher.get(
                        "gia_tri"
                    )
                )
            )

            enrich_voucher_condition_fields(
                voucher
            )

        redeemed_rewards = (
            supabase
            .table(
                PROMOTION_REDEEM_TABLE
            )
            .select(
                "id,"
                "ma_qua,"
                "ma_thanh_vien,"
                "ma_khuyen_mai,"
                "ma_moc,"
                "loai_khuyen_mai,"
                "gia_tri_da_dung,"
                "ten_qua_tang,"
                "trang_thai,"
                "kich_hoat_at,"
                "created_at,"
                "updated_at"
            )
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
            or []
        )

        for reward in redeemed_rewards:
            created_dt = parse_iso_datetime(
                reward.get(
                    "created_at"
                )
            )

            reward[
                "created_at_hien_thi"
            ] = (
                created_dt
                .astimezone()
                .strftime(
                    "%d/%m/%Y %H:%M"
                )
                if created_dt
                else ""
            )

            reward_type = clean_text(
                reward.get(
                    "loai_khuyen_mai"
                )
            )

            if reward_type == "THOI_LUONG":
                reward[
                    "loai_hien_thi"
                ] = "Quà tích giờ"
            elif reward_type == "SO_LAN":
                reward[
                    "loai_hien_thi"
                ] = "Quà tích lượt"
            elif reward_type == "SO_TIEN":
                reward[
                    "loai_hien_thi"
                ] = "Quà tích phí"
            else:
                reward[
                    "loai_hien_thi"
                ] = "Quà khuyến mãi"

    except Exception as exc:

        print(
            "[MEMBER PROMOTION ERROR]",
            repr(exc)
        )

        member = None
        promo_hour = None
        promo_play = None
        promo_money = None
        promo_voucher = None
        vouchers = []
        redeemed_rewards = []

        flash(
            "Không tải được chương trình khuyến mãi.",
            "danger"
        )

    reward_items, voucher_items = enrich_member_rewards_and_vouchers(
        redeemed_rewards,
        vouchers
    )

    # ==========================================================
    # SỐ LƯỢNG QUÀ / VOUCHER CÒN SỬ DỤNG ĐƯỢC
    # Không tính các món đã nhận / đã sử dụng.
    # ==========================================================

    available_reward_count = sum(
        1
        for item in reward_items
        if item.get(
            "con_su_dung"
        )
    )


    available_voucher_count = sum(
        1
        for item in voucher_items
        if item.get(
            "con_su_dung"
        )
    )


    available_benefit_count = (
        available_reward_count
        + available_voucher_count
    )

    return render_template(
        "member-khuyenmai.html",

        member=member,

        promo_hour=promo_hour,

        promo_play=promo_play,

        promo_money=promo_money,

        vouchers=vouchers,

        redeemed_rewards=
            redeemed_rewards,

        reward_items=
            reward_items,

        voucher_items=
            voucher_items,

        available_reward_count=
            available_reward_count,

        available_voucher_count=
            available_voucher_count,

        available_benefit_count=
            available_benefit_count
    )

@app.post(
    "/member-khuyenmai/doi-qua/<string:milestone_code>"
)
def member_redeem_reward(
    milestone_code
):

    if not session.get(
        "member_logged_in"
    ):
        return jsonify({
            "ok": False,
            "message":
                "Phiên đăng nhập đã hết hạn."
        }), 401

    member_code = clean_text(
        session.get(
            "member_code"
        )
    ).upper()

    if not member_code:
        return jsonify({
            "ok": False,
            "message":
                "Không xác định được mã thành viên."
        }), 400

    try:

        milestone_rows = (
            supabase
            .table(
                PROMOTION_MILESTONE_TABLE
            )
            .select("*")
            .eq(
                "ma_moc",
                milestone_code
            )
            .eq(
                "active",
                True
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not milestone_rows:
            return jsonify({
                "ok": False,
                "message":
                    "Không tìm thấy mốc quà."
            }), 404

        milestone = milestone_rows[0]

        promotion_code = clean_text(
            milestone.get(
                "ma_khuyen_mai"
            )
        )

        promotion_rows = (
            supabase
            .table(
                PROMOTION_TABLE
            )
            .select("*")
            .eq(
                "ma_khuyen_mai",
                promotion_code
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not promotion_rows:
            return jsonify({
                "ok": False,
                "message":
                    "Không tìm thấy chương trình."
            }), 404

        promotion = promotion_rows[0]

        if not promotion.get(
            "mo_chuong_trinh"
        ):
            return jsonify({
                "ok": False,
                "message":
                    "Chương trình hiện chưa mở."
            }), 400

        now = (
            datetime.now()
            .astimezone()
        )

        start_dt = parse_iso_datetime(
            promotion.get(
                "bat_dau"
            )
        )

        end_dt = parse_iso_datetime(
            promotion.get(
                "ket_thuc"
            )
        )

        if start_dt and now < start_dt:
            return jsonify({
                "ok": False,
                "message":
                    "Chương trình chưa bắt đầu."
            }), 400

        if end_dt and now > end_dt:
            return jsonify({
                "ok": False,
                "message":
                    "Chương trình đã kết thúc."
            }), 400

        progress = (
            get_member_promotion_progress(
                member_code,
                promotion
            )
        )

        required_value = int(
            milestone.get(
                "gia_tri_moc"
            )
            or 0
        )

        if required_value <= 0:
            return jsonify({
                "ok": False,
                "message":
                    "Mốc quà không hợp lệ."
            }), 400

        if (
            progress.get(
                "con_lai",
                0
            )
            < required_value
        ):
            return jsonify({
                "ok": False,
                "message":
                    "Bạn chưa đủ điều kiện quy đổi phần quà này."
            }), 400

        reward_code = (
            generate_reward_code()
        )

        now_iso = (
            datetime.now()
            .astimezone()
            .isoformat()
        )

        payload = {
            "ma_qua": reward_code,
            "ma_thanh_vien": member_code,
            "ma_khuyen_mai": promotion_code,
            "ma_moc": clean_text(
                milestone.get(
                    "ma_moc"
                )
            ),
            "loai_khuyen_mai": clean_text(
                promotion.get(
                    "loai_khuyen_mai"
                )
            ),
            "gia_tri_da_dung": required_value,
            "ten_qua_tang": clean_text(
                milestone.get(
                    "ten_qua_tang"
                )
            ),
            "trang_thai": "Chưa nhận quà",
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        result = (
            supabase
            .table(
                PROMOTION_REDEEM_TABLE
            )
            .insert(
                payload
            )
            .execute()
        )

        return jsonify({
            "ok": True,
            "message":
                "Quy đổi quà thành công.",
            "ma_qua":
                reward_code,
            "qua_tang":
                milestone.get(
                    "ten_qua_tang"
                ),
            "trang_thai":
                "Chưa nhận quà",
            "du_lieu": (
                result.data[0]
                if result.data
                else payload
            )
        })

    except Exception as exc:

        print(
            "[REDEEM PROMOTION ERROR]",
            repr(exc)
        )

        return jsonify({
            "ok": False,
            "message":
                f"Không quy đổi được quà: {exc}"
        }), 500

@app.get("/member-logout")
def member_logout():

    session.pop(
        "member_logged_in",
        None
    )

    session.pop(
        "member_code",
        None
    )

    flash(
        "Đã đăng xuất.",
        "success"
    )

    return redirect(
        url_for(
            "member_login"
        )
    )

@app.get("/")
def index():
    # Domain gốc luôn đưa khách đến đăng nhập Member.
    # Admin vẫn truy cập trực tiếp /admin-login.
    return redirect(url_for("member_login"))


@app.get("/thanh-vien")
def thanh_vien():
    q = clean_text(request.args.get("q"))

    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(request.args.get("per_page") or 20)
    except (TypeError, ValueError):
        per_page = 20

    allowed_per_page = {20, 50, 100, 200}
    if per_page not in allowed_per_page:
        per_page = 20

    try:
        query = (
            supabase.table(TABLE_NAME)
            .select(
                "id,"
                "ma_thanh_vien,"
                "ho_va_ten,"
                "gioi_tinh,"
                "ngay_sinh,"
                "dia_chi,"
                "so_dien_thoai,"
                "hang_thanh_vien,"
                "so_lan_da_choi,"
                "thoi_gian_choi_gan_nhat,"
                "thoi_gian_choi_lau_nhat,"
                "thoi_gian_choi_ngan_nhat",
                count="exact"
            )
            .order("created_at", desc=True)
        )

        if q:
            safe_q = q.replace(",", " ").strip()
            query = query.or_(
                f"ma_thanh_vien.ilike.%{safe_q}%,"
                f"ho_va_ten.ilike.%{safe_q}%,"
                f"so_dien_thoai.ilike.%{safe_q}%,"
                f"ngay_sinh.eq.{safe_q}"
            )

        start = (page - 1) * per_page
        end = start + per_page - 1
        response = query.range(start, end).execute()

        rows = response.data or []
        total_rows = int(response.count or 0)
        total_pages = max(1, (total_rows + per_page - 1) // per_page)

        if page > total_pages and total_rows:
            return redirect(
                url_for(
                    "thanh_vien",
                    q=q,
                    per_page=per_page,
                    page=total_pages
                )
            )

        for row in rows:
            row["ngay_sinh_hien_thi"] = format_birthdate_display(row.get("ngay_sinh"))

    except Exception as exc:
        print("[LOAD THANHVIEN ERROR]", repr(exc))
        rows = []
        total_rows = 0
        total_pages = 1
        page = 1
        flash(f"Không đọc được dữ liệu Supabase: {exc}", "danger")

    return render_template(
        "Thanhvien.html",
        rows=rows,
        q=q,
        page=page,
        per_page=per_page,
        total_rows=total_rows,
        total_pages=total_pages
    )


@app.get("/api/thanh-vien/tim-kiem")
def api_tim_kiem_thanh_vien():
    q = clean_text(request.args.get("q"))

    if not q:
        return jsonify({"ok": True, "items": []})

    safe_q = q.replace(",", " ").strip()

    try:
        rows = (
            supabase.table(TABLE_NAME)
            .select(
                "id,ma_thanh_vien,ho_va_ten,so_dien_thoai,ngay_sinh,hang_thanh_vien"
            )
            .or_(
                f"ma_thanh_vien.ilike.%{safe_q}%,"
                f"ho_va_ten.ilike.%{safe_q}%,"
                f"so_dien_thoai.ilike.%{safe_q}%"
            )
            .order("ho_va_ten")
            .limit(20)
            .execute()
            .data
            or []
        )

        return jsonify({
            "ok": True,
            "items": rows
        })

    except Exception as exc:
        print("[MEMBER STAT SEARCH ERROR]", repr(exc))
        return jsonify({
            "ok": False,
            "message": f"Không tìm được thành viên: {exc}"
        }), 500


@app.get("/api/thanh-vien/<string:member_code>/thong-ke")
def api_thong_ke_thanh_vien(member_code):
    member_code = clean_text(member_code).upper()

    try:
        month = int(request.args.get("month") or 0)
    except (TypeError, ValueError):
        month = 0

    try:
        year = int(request.args.get("year") or 0)
    except (TypeError, ValueError):
        year = 0

    if month not in range(0, 13):
        month = 0

    if year < 0:
        year = 0

    try:
        member_rows = (
            supabase.table(TABLE_NAME)
            .select("id,ma_thanh_vien,ho_va_ten,so_dien_thoai")
            .eq("ma_thanh_vien", member_code)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not member_rows:
            return jsonify({
                "ok": False,
                "message": "Không tìm thấy thành viên."
            }), 404

        member = member_rows[0]

        all_invoices = (
            supabase.table(INVOICE_TABLE)
            .select(
                "created_at,thoi_gian_bat_dau,thoi_gian_ket_thuc,"
                "thoi_luong_choi_phut,tien_ban,tien_dich_vu,tong_tien,"
                "so_tien_giam,thanh_tien,trang_thai"
            )
            .eq("ma_thanh_vien", member_code)
            .eq("trang_thai", "Đã thanh toán")
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )

        all_invoice_datetimes = []
        for row in all_invoices:
            dt = (
                parse_iso_datetime(row.get("created_at"))
                or parse_iso_datetime(row.get("thoi_gian_ket_thuc"))
            )
            all_invoice_datetimes.append((row, dt.astimezone() if dt else None))

        available_years = sorted({
            dt.year
            for _, dt in all_invoice_datetimes
            if dt
        }, reverse=True)

        invoices = []
        for row, dt in all_invoice_datetimes:
            if year and (not dt or dt.year != year):
                continue
            if month and (not dt or dt.month != month):
                continue
            invoices.append(row)

        total_plays = len(invoices)
        total_minutes = sum(int(row.get("thoi_luong_choi_phut") or 0) for row in invoices)
        total_table = sum(int(row.get("tien_ban") or 0) for row in invoices)
        total_service = sum(int(row.get("tien_dich_vu") or 0) for row in invoices)
        total_paid = sum(int(row.get("thanh_tien") or 0) for row in invoices)

        play_datetimes = []
        for row in invoices:
            dt = (
                parse_iso_datetime(row.get("thoi_gian_ket_thuc"))
                or parse_iso_datetime(row.get("created_at"))
            )
            if dt:
                play_datetimes.append(dt.astimezone())

        latest_play = max(play_datetimes) if play_datetimes else None
        earliest_play = min(play_datetimes) if play_datetimes else None

        if latest_play and earliest_play and total_plays > 1:
            play_time_display = (
                f"{earliest_play.strftime('%d/%m/%Y')} - "
                f"{latest_play.strftime('%d/%m/%Y')}"
            )
        elif latest_play:
            play_time_display = latest_play.strftime("%d/%m/%Y %H:%M")
        else:
            play_time_display = "Chưa có"

        if year and year not in available_years:
            available_years.append(year)
            available_years.sort(reverse=True)

        return jsonify({
            "ok": True,
            "member": member,
            "filter": {
                "month": month,
                "year": year,
            },
            "available_years": available_years,
            "stats": {
                "luot_choi": total_plays,
                "thoi_luong_phut": total_minutes,
                "thoi_luong_hien_thi": format_minutes_detail(total_minutes),
                "thoi_gian_choi_hien_thi": play_time_display,
                "tien_ban": total_table,
                "tien_ban_hien_thi": format_money_vn(total_table),
                "tien_dich_vu": total_service,
                "tien_dich_vu_hien_thi": format_money_vn(total_service),
                "tong_tien": total_paid,
                "tong_tien_hien_thi": format_money_vn(total_paid),
            }
        })

    except Exception as exc:
        print("[MEMBER STAT ERROR]", repr(exc))
        return jsonify({
            "ok": False,
            "message": f"Không tải được thống kê thành viên: {exc}"
        }), 500


@app.post("/thanh-vien/them")
def them_thanh_vien():
    payload = member_payload(request.form)

    # Chuẩn hóa SĐT
    payload["so_dien_thoai"] = re.sub(
        r"\D",
        "",
        clean_text(
            request.form.get("so_dien_thoai")
        )
    )

    error = validate_member(payload)

    if error:
        flash(error, "danger")
        return redirect(url_for("thanh_vien"))


    # TỰ SINH MÃ THÀNH VIÊN
    payload["ma_thanh_vien"] = (
        generate_unique_member_code(
            payload["ho_va_ten"],
            request.form.get("ngay_sinh"),
            payload["so_dien_thoai"]
        )
    )

    if not payload["ma_thanh_vien"]:
        flash(
            "Không thể tạo mã thành viên. "
            "Vui lòng kiểm tra họ tên, ngày sinh và số điện thoại.",
            "danger"
        )

        return redirect(
            url_for("thanh_vien")
        )
    if error:
        flash(error, "danger")
        return redirect(url_for("thanh_vien"))

    try:
        supabase.table(TABLE_NAME).insert(payload).execute()
        flash("Đã thêm thành viên thành công.", "success")
    except Exception as exc:
        print("[ADD THANHVIEN ERROR]", repr(exc))
        message = str(exc)
        if "duplicate key" in message.lower() or "unique" in message.lower():
            message = "Mã thành viên đã tồn tại."
        flash(f"Không thêm được thành viên: {message}", "danger")

    return redirect(url_for("thanh_vien"))


def sync_member_references(old_code, new_code, ho_va_ten, so_dien_thoai):
    """
    Đồng bộ các bảng đang liên kết thành viên bằng ma_thanh_vien.

    Lưu ý: Supabase REST không tạo transaction đa bảng trong một request.
    Hàm này vì vậy cập nhật theo thứ tự và ném lỗi nếu bất kỳ bước nào thất bại.
    """
    old_code = clean_text(old_code).upper()
    new_code = clean_text(new_code).upper()
    ho_va_ten = clean_text(ho_va_ten)
    so_dien_thoai = re.sub(r"\D", "", clean_text(so_dien_thoai))

    if not old_code or not new_code:
        raise ValueError("Mã thành viên không hợp lệ để đồng bộ.")

    # Hóa đơn đang lưu cả mã, họ tên và SĐT -> đồng bộ đầy đủ.
    (
        supabase
        .table(INVOICE_TABLE)
        .update({
            "ma_thanh_vien": new_code,
            "ho_va_ten": ho_va_ten or None,
            "so_dien_thoai": so_dien_thoai or None,
            "updated_at": datetime.now().astimezone().isoformat(),
        })
        .eq("ma_thanh_vien", old_code)
        .execute()
    )

    # Quà đã đổi / đang chờ nhận liên kết bằng mã thành viên.
    (
        supabase
        .table(PROMOTION_REDEEM_TABLE)
        .update({
            "ma_thanh_vien": new_code,
            "updated_at": datetime.now().astimezone().isoformat(),
        })
        .eq("ma_thanh_vien", old_code)
        .execute()
    )

    # Voucher liên kết bằng mã thành viên.
    (
        supabase
        .table(PROMOTION_VOUCHER_TABLE)
        .update({
            "ma_thanh_vien": new_code,
            "updated_at": datetime.now().astimezone().isoformat(),
        })
        .eq("ma_thanh_vien", old_code)
        .execute()
    )


@app.post("/thanh-vien/<int:member_id>/cap-nhat")
def cap_nhat_thanh_vien(member_id):
    return_q = clean_text(request.form.get("return_q"))
    return_page = clean_text(request.form.get("return_page")) or 1
    return_per_page = clean_text(request.form.get("return_per_page")) or 20

    try:
        # 1. Lấy hồ sơ cũ để biết mã đang được các bảng khác sử dụng.
        old_rows = (
            supabase
            .table(TABLE_NAME)
            .select("id,ma_thanh_vien,ho_va_ten,ngay_sinh,so_dien_thoai")
            .eq("id", member_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not old_rows:
            raise ValueError("Không tìm thấy thành viên cần cập nhật.")

        old_member = old_rows[0]
        old_code = clean_text(old_member.get("ma_thanh_vien")).upper()

        # 2. Chuẩn hóa dữ liệu mới.
        payload = member_payload(request.form)
        payload["so_dien_thoai"] = re.sub(
            r"\D",
            "",
            clean_text(request.form.get("so_dien_thoai"))
        )

        # Mã thành viên không cho nhập tay. Sinh lại từ Họ tên + Ngày sinh + SĐT.
        new_code = generate_unique_member_code(
            payload.get("ho_va_ten"),
            request.form.get("ngay_sinh"),
            payload.get("so_dien_thoai"),
            exclude_id=member_id,
        )

        if not new_code:
            raise ValueError(
                "Không thể tạo mã thành viên. Vui lòng kiểm tra họ tên, ngày sinh và số điện thoại."
            )

        payload["ma_thanh_vien"] = new_code

        error = validate_member(payload)
        if error:
            raise ValueError(error)

        # 3. Nếu mã thay đổi, đồng bộ toàn bộ bảng liên quan trước.
        if new_code != old_code:
            sync_member_references(
                old_code=old_code,
                new_code=new_code,
                ho_va_ten=payload.get("ho_va_ten"),
                so_dien_thoai=payload.get("so_dien_thoai"),
            )
        else:
            # Mã không đổi nhưng tên/SĐT có thể đổi -> vẫn cập nhật hóa đơn để dữ liệu thống nhất.
            (
                supabase
                .table(INVOICE_TABLE)
                .update({
                    "ho_va_ten": payload.get("ho_va_ten") or None,
                    "so_dien_thoai": payload.get("so_dien_thoai") or None,
                    "updated_at": datetime.now().astimezone().isoformat(),
                })
                .eq("ma_thanh_vien", old_code)
                .execute()
            )

        # 4. Cập nhật hồ sơ chính sau cùng.
        (
            supabase
            .table(TABLE_NAME)
            .update(payload)
            .eq("id", member_id)
            .execute()
        )

        # Nếu chính thành viên này đang đăng nhập, đổi mã trong session ngay.
        if clean_text(session.get("member_code")).upper() == old_code:
            session["member_code"] = new_code

        flash(
            f"Đã cập nhật thành viên và đồng bộ dữ liệu liên quan. Mã hiện tại: {new_code}",
            "success"
        )

    except Exception as exc:
        print("[UPDATE THANHVIEN SYNC ERROR]", repr(exc))
        message = str(exc)
        if "duplicate key" in message.lower() or "unique" in message.lower():
            message = "Mã thành viên hoặc dữ liệu liên quan đang bị trùng."
        flash(f"Không cập nhật được thành viên: {message}", "danger")

    return redirect(url_for(
        "thanh_vien",
        q=return_q,
        page=return_page,
        per_page=return_per_page
    ))


@app.post("/thanh-vien/<int:member_id>/xoa")
def xoa_thanh_vien(member_id):
    try:
        supabase.table(TABLE_NAME).delete().eq("id", member_id).execute()
        flash("Đã xóa thành viên.", "success")
    except Exception as exc:
        print("[DELETE THANHVIEN ERROR]", repr(exc))
        flash(f"Không xóa được thành viên: {exc}", "danger")

    return redirect(url_for(
        "thanh_vien",
        q=clean_text(request.form.get("return_q")),
        page=clean_text(request.form.get("return_page")) or 1,
        per_page=clean_text(request.form.get("return_per_page")) or 20
    ))


def normalize_voucher_money(value):
    """Lưu tiền dạng '50,000 đ', hoặc phần trăm dạng '10%' trong gia_tri TEXT."""
    text = clean_text(value)
    if '%' in text:
        percent = parse_percent_discount(text)
        if not percent:
            raise ValueError("Phần trăm voucher phải lớn hơn 0 và không quá 100%.")
        return f"{percent:g}%"
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return f"{int(digits):,} đ"


def parse_voucher_date(value, end_of_day=False):
    """
    Voucher nhập theo NGÀY dd/mm/yyyy.
    Hiệu lực theo ngày tại Việt Nam, không phụ thuộc timezone của VPS.
    - Ngày bắt đầu: 00:00:00 +07
    - Ngày hết hạn: 23:59:59 +07
    """
    raw = clean_text(value)

    if not raw:
        return None

    try:
        dt = datetime.strptime(
            raw,
            "%d/%m/%Y"
        )

    except ValueError:
        raise ValueError(
            "Ngày phải đúng định dạng dd/mm/yyyy."
        )

    if end_of_day:
        dt = dt.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=999999
        )

    else:
        dt = dt.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    return dt.replace(
        tzinfo=VIETNAM_TZ
    ).isoformat()


def voucher_date_display(value):
    dt = parse_iso_datetime(value)
    if not dt:
        return ""
    return dt.astimezone().strftime("%d/%m/%Y")

def parse_promotion_date(
    value,
    end_of_day=False
):

    raw = clean_text(value)

    if not raw:
        return None


    try:

        dt = datetime.strptime(
            raw,
            "%d/%m/%Y"
        )


    except ValueError:

        raise ValueError(
            "Ngày khuyến mãi phải đúng định dạng dd/mm/yyyy."
        )


    if end_of_day:

        dt = dt.replace(
            hour=23,
            minute=59,
            second=59
        )

    else:

        dt = dt.replace(
            hour=0,
            minute=0,
            second=0
        )


    local_tz = (
        datetime.now()
        .astimezone()
        .tzinfo
    )


    return (
        dt.replace(
            tzinfo=local_tz
        )
        .isoformat()
    )

def promotion_date_display(value):

    dt = parse_iso_datetime(value)

    if not dt:
        return ""

    return (
        dt.astimezone()
        .strftime("%d/%m/%Y")
    )


@app.get("/khuyen-mai")
def khuyen_mai_admin():

    try:

        # ==========================================
        # 1. ĐỌC TOÀN BỘ CHƯƠNG TRÌNH
        # ==========================================
        promotions = (
            supabase
            .table(PROMOTION_TABLE)
            .select("*")
            .order("created_at")
            .execute()
            .data
            or []
        )


        # ==========================================
        # 2. BẢO ĐẢM MỖI LOẠI CÓ 1 CHƯƠNG TRÌNH
        #    THỜI LƯỢNG / SỐ LẦN / SỐ TIỀN / VOUCHER
        # ==========================================
        existing_types = {
            clean_text(row.get("loai_khuyen_mai"))
            for row in promotions
        }

        default_rows = [
            {
                "ten_khuyen_mai": "Tích giờ đổi quà",
                "loai_khuyen_mai": "THOI_LUONG",
                "mo_chuong_trinh": False,
                "mo_ta":
                    "Tích lũy thời gian chơi để nhận các phần quà.",
            },
            {
                "ten_khuyen_mai": "Tích lượt đổi quà",
                "loai_khuyen_mai": "SO_LAN",
                "mo_chuong_trinh": False,
                "mo_ta":
                    "Tích lũy số lần chơi để nhận các phần quà.",
            },
            {
                "ten_khuyen_mai": "Tích lũy tiền chơi để đổi quà",
                "loai_khuyen_mai": "SO_TIEN",
                "mo_chuong_trinh": False,
                "mo_ta":
                    "Tích lũy số tiền thực trả từ các hóa đơn để đổi quà.",
            },
            {
                "ten_khuyen_mai": "Voucher thành viên",
                "loai_khuyen_mai": "VOUCHER",
                "mo_chuong_trinh": True,
                "mo_ta":
                    "Voucher được CLB gửi riêng cho từng thành viên.",
            },
        ]

        for item in default_rows:

            promo_type = item["loai_khuyen_mai"]

            if promo_type in existing_types:
                continue

            payload = dict(item)

            payload["ma_khuyen_mai"] = (
                generate_promotion_code()
            )

            now_iso = (
                datetime.now()
                .astimezone()
                .isoformat()
            )

            payload["created_at"] = now_iso
            payload["updated_at"] = now_iso

            (
                supabase
                .table(PROMOTION_TABLE)
                .insert(payload)
                .execute()
            )


        # Đọc lại sau khi bổ sung thiếu
        promotions = (
            supabase
            .table(PROMOTION_TABLE)
            .select("*")
            .order("created_at")
            .execute()
            .data
            or []
        )


        # ==========================================
        # 3. DANH SÁCH THÀNH VIÊN
        # ==========================================
        members = (
            supabase
            .table(TABLE_NAME)
            .select(
                "id,"
                "ma_thanh_vien,"
                "ho_va_ten,"
                "so_dien_thoai"
            )
            .order("ho_va_ten")
            .execute()
            .data
            or []
        )

        member_map_by_code = {
            clean_text(
                row.get("ma_thanh_vien")
            ).upper(): row

            for row in members

            if clean_text(
                row.get("ma_thanh_vien")
            )
        }


        # ==========================================
        # 4. PHÂN LOẠI + LOAD MỐC THEO MÃ KM
        # ==========================================
        promo_hour = None
        promo_play = None
        promo_money = None
        promo_voucher = None

        for promo in promotions:

            promo_type = clean_text(
                promo.get("loai_khuyen_mai")
            )

            promotion_code = clean_text(
                promo.get("ma_khuyen_mai")
            )

            promo["bat_dau_input"] = (
                promotion_date_display(
                    promo.get("bat_dau")
                )
            )

            promo["ket_thuc_input"] = (
                promotion_date_display(
                    promo.get("ket_thuc")
                )
            )

            # ======================================
            # QUAN HỆ MỚI:
            # Khuyenmai_moc.ma_khuyen_mai
            # ======================================
            promo["moc"] = (
                supabase
                .table(
                    PROMOTION_MILESTONE_TABLE
                )
                .select("*")
                .eq(
                    "ma_khuyen_mai",
                    promotion_code
                )
                .order("thu_tu")
                .execute()
                .data
                or []
            )

            if promo_type == "THOI_LUONG":
                promo_hour = promo

            elif promo_type == "SO_LAN":
                promo_play = promo

            elif promo_type == "SO_TIEN":
                promo_money = promo

            elif promo_type == "VOUCHER":
                promo_voucher = promo


        # ==========================================
        # 5. DANH SÁCH VOUCHER
        #    MAP THÀNH VIÊN BẰNG ma_thanh_vien
        # ==========================================
        vouchers = (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .select("*")
            .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
            or []
        )

        for voucher in vouchers:

            member_code = clean_text(
                voucher.get("ma_thanh_vien")
            ).upper()

            member = (
                member_map_by_code.get(
                    member_code,
                    {}
                )
            )

            voucher[
                "ho_va_ten"
            ] = clean_text(
                member.get("ho_va_ten")
            )

            voucher[
                "so_dien_thoai"
            ] = clean_text(
                member.get("so_dien_thoai")
            )

            created_dt = parse_iso_datetime(
                voucher.get("created_at")
            )

            voucher[
                "created_at_hien_thi"
            ] = (
                created_dt
                .astimezone()
                .strftime("%d/%m/%Y %H:%M")
                if created_dt
                else ""
            )

            voucher[
                "ngay_bat_dau_hien_thi"
            ] = voucher_date_display(
                voucher.get(
                    "ngay_bat_dau"
                )
            )

            voucher[
                "ngay_het_han_hien_thi"
            ] = voucher_date_display(
                voucher.get(
                    "ngay_het_han"
                )
            )

            voucher[
                "gia_tri_hien_thi"
            ] = discount_benefit_display(
                *discount_benefit_value(voucher.get("gia_tri"), allow_bare_amount=False)
            )

            enrich_voucher_condition_fields(
                voucher
            )


    except Exception as exc:

        print(
            "[ADMIN PROMOTION ERROR]",
            repr(exc)
        )

        promo_hour = None
        promo_play = None
        promo_money = None
        promo_voucher = None
        members = []
        vouchers = []

        flash(
            f"Không tải được dữ liệu khuyến mãi: {exc}",
            "danger"
        )


    return render_template(
        "khuyenmai.html",

        promo_hour=promo_hour,
        promo_play=promo_play,
        promo_money=promo_money,
        promo_voucher=promo_voucher,

        members=members,
        vouchers=vouchers
    )


@app.post(
    "/khuyen-mai/<int:promotion_id>/toggle"
)
def toggle_khuyen_mai(
    promotion_id
):

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        enabled = bool(
            data.get("enabled")
        )

        promo_rows = (
            supabase
            .table(PROMOTION_TABLE)
            .select(
                "id,"
                "ma_khuyen_mai"
            )
            .eq(
                "id",
                promotion_id
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not promo_rows:
            return jsonify({
                "ok": False,
                "message":
                    "Không tìm thấy chương trình."
            }), 404

        (
            supabase
            .table(
                PROMOTION_TABLE
            )
            .update({
                "mo_chuong_trinh":
                    enabled,

                "updated_at":
                    datetime.now()
                    .astimezone()
                    .isoformat()
            })
            .eq(
                "ma_khuyen_mai",
                promo_rows[0].get(
                    "ma_khuyen_mai"
                )
            )
            .execute()
        )

        return jsonify({
            "ok": True,
            "enabled": enabled
        })


    except Exception as exc:

        print(
            "[PROMOTION TOGGLE ERROR]",
            repr(exc)
        )

        return jsonify({
            "ok": False,
            "message":
                f"Không cập nhật được trạng thái: {exc}"
        }), 500


@app.post(
    "/khuyen-mai/<int:promotion_id>/cap-nhat"
)
def cap_nhat_khuyen_mai(
    promotion_id
):

    try:

        # ==================================
        # 1. CHƯƠNG TRÌNH HIỆN TẠI
        # ==================================
        promo_rows = (
            supabase
            .table(
                PROMOTION_TABLE
            )
            .select(
                "id,"
                "ma_khuyen_mai,"
                "loai_khuyen_mai,"
                "mo_chuong_trinh"
            )
            .eq(
                "id",
                promotion_id
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not promo_rows:
            raise ValueError(
                "Không tìm thấy chương trình khuyến mãi."
            )

        promo = promo_rows[0]

        promotion_code = clean_text(
            promo.get(
                "ma_khuyen_mai"
            )
        )

        promo_type = clean_text(
            promo.get(
                "loai_khuyen_mai"
            )
        )

        if not promotion_code:
            raise ValueError(
                "Chương trình chưa có mã khuyến mãi."
            )


        # ==================================
        # 2. CẬP NHẬT THÔNG TIN CHƯƠNG TRÌNH
        # ==================================
        payload = {

            "ten_khuyen_mai":
                clean_text(
                    request.form.get(
                        "ten_khuyen_mai"
                    )
                ),

            "mo_ta":
                clean_text(
                    request.form.get(
                        "mo_ta"
                    )
                )
                or None,

            "bat_dau":
                parse_promotion_date(
                    request.form.get(
                        "bat_dau"
                    )
                ),

            "ket_thuc":
                parse_promotion_date(
                    request.form.get(
                        "ket_thuc"
                    ),
                    end_of_day=True
                ),

            "updated_at":
                datetime.now()
                .astimezone()
                .isoformat()
        }

        if not payload[
            "ten_khuyen_mai"
        ]:
            raise ValueError(
                "Tên chương trình không được để trống."
            )

        (
            supabase
            .table(
                PROMOTION_TABLE
            )
            .update(payload)
            .eq(
                "ma_khuyen_mai",
                promotion_code
            )
            .execute()
        )


        # ==================================
        # 3. MỐC QUÀ GỬI TỪ FORM
        #
        # HỖ TRỢ CẢ:
        # - moc_code   (schema mới)
        # - moc_id     (template cũ)
        # ==================================
        milestone_codes = (
            request.form.getlist(
                "moc_code"
            )
        )

        old_milestone_ids = (
            request.form.getlist(
                "moc_id"
            )
        )

        milestone_values = (
            request.form.getlist(
                "moc_value"
            )
        )

        milestone_gifts = (
            request.form.getlist(
                "moc_gift"
            )
        )

        total_rows = max(
            len(milestone_codes),
            len(old_milestone_ids),
            len(milestone_values),
            len(milestone_gifts)
        )

        submitted_codes = set()


        for index in range(
            total_rows
        ):

            milestone_code = (
                clean_text(
                    milestone_codes[index]
                )
                if index < len(
                    milestone_codes
                )
                else ""
            )

            old_id = (
                clean_text(
                    old_milestone_ids[index]
                )
                if index < len(
                    old_milestone_ids
                )
                else ""
            )

            raw_value = (
                clean_text(
                    milestone_values[index]
                )
                if index < len(
                    milestone_values
                )
                else ""
            )

            gift_name = (
                clean_text(
                    milestone_gifts[index]
                )
                if index < len(
                    milestone_gifts
                )
                else ""
            )

            if (
                not raw_value
                and not gift_name
            ):
                continue

            if not raw_value:
                raise ValueError(
                    "Mốc khuyến mãi không được để trống."
                )

            if not gift_name:
                raise ValueError(
                    "Tên phần quà không được để trống."
                )

            try:

                if promo_type == "SO_TIEN":
                    value_number = parse_money_value(
                        raw_value
                    )
                else:
                    value_number = float(
                        raw_value.replace(
                            ",",
                            "."
                        )
                    )

            except ValueError:

                raise ValueError(
                    "Giá trị mốc không hợp lệ."
                )

            if value_number <= 0:
                raise ValueError(
                    "Giá trị mốc phải lớn hơn 0."
                )


            # ==================================
            # TÍCH GIỜ:
            # UI nhập GIỜ, DB lưu PHÚT
            # ==================================
            if (
                promo_type
                == "THOI_LUONG"
            ):

                db_value = int(
                    round(
                        value_number * 60
                    )
                )

            else:

                db_value = int(
                    round(
                        value_number
                    )
                )


            # ==================================
            # Nếu template cũ chỉ gửi ID
            # thì đổi ID đó sang ma_moc
            # ==================================
            if (
                not milestone_code
                and old_id
            ):

                try:

                    old_id_int = int(
                        old_id
                    )

                except ValueError:

                    old_id_int = 0

                if old_id_int:

                    old_rows = (
                        supabase
                        .table(
                            PROMOTION_MILESTONE_TABLE
                        )
                        .select(
                            "ma_moc,"
                            "ma_khuyen_mai"
                        )
                        .eq(
                            "id",
                            old_id_int
                        )
                        .eq(
                            "ma_khuyen_mai",
                            promotion_code
                        )
                        .limit(1)
                        .execute()
                        .data
                        or []
                    )

                    if old_rows:

                        milestone_code = (
                            clean_text(
                                old_rows[0].get(
                                    "ma_moc"
                                )
                            )
                        )


            row_payload = {

                "ma_khuyen_mai":
                    promotion_code,

                "gia_tri_moc":
                    db_value,

                "ten_qua_tang":
                    gift_name,

                "thu_tu":
                    index + 1,

                "active":
                    True,

                "updated_at":
                    datetime.now()
                    .astimezone()
                    .isoformat()
            }


            # ==================================
            # UPDATE MỐC CŨ THEO ma_moc
            # ==================================
            if milestone_code:

                submitted_codes.add(
                    milestone_code
                )

                (
                    supabase
                    .table(
                        PROMOTION_MILESTONE_TABLE
                    )
                    .update(
                        row_payload
                    )
                    .eq(
                        "ma_moc",
                        milestone_code
                    )
                    .eq(
                        "ma_khuyen_mai",
                        promotion_code
                    )
                    .execute()
                )


            # ==================================
            # THÊM MỐC MỚI
            # ==================================
            else:

                milestone_code = (
                    generate_milestone_code()
                )

                submitted_codes.add(
                    milestone_code
                )

                row_payload[
                    "ma_moc"
                ] = milestone_code

                row_payload[
                    "created_at"
                ] = (
                    datetime.now()
                    .astimezone()
                    .isoformat()
                )

                (
                    supabase
                    .table(
                        PROMOTION_MILESTONE_TABLE
                    )
                    .insert(
                        row_payload
                    )
                    .execute()
                )


        # ==================================
        # 4. XÓA MỐC KHÔNG CÒN TRONG FORM
        # ==================================
        old_rows = (
            supabase
            .table(
                PROMOTION_MILESTONE_TABLE
            )
            .select(
                "ma_moc"
            )
            .eq(
                "ma_khuyen_mai",
                promotion_code
            )
            .execute()
            .data
            or []
        )

        for old in old_rows:

            old_code = clean_text(
                old.get("ma_moc")
            )

            if (
                old_code
                and old_code
                not in submitted_codes
            ):

                (
                    supabase
                    .table(
                        PROMOTION_MILESTONE_TABLE
                    )
                    .delete()
                    .eq(
                        "ma_moc",
                        old_code
                    )
                    .eq(
                        "ma_khuyen_mai",
                        promotion_code
                    )
                    .execute()
                )


        flash(
            "Đã lưu chương trình và các mốc quà.",
            "success"
        )


    except Exception as exc:

        print(
            "[UPDATE PROMOTION ERROR]",
            repr(exc)
        )

        flash(
            f"Không cập nhật được: {exc}",
            "danger"
        )


    return redirect(
        url_for(
            "khuyen_mai_admin"
        )
    )


def generate_voucher_code():

    now = datetime.now().astimezone()

    suffix = (
        uuid.uuid4()
        .hex[:5]
        .upper()
    )

    return (
        f"VC{now:%Y%m%d}"
        f"-{suffix}"
    )

def generate_reward_code():

    now = datetime.now().astimezone()

    suffix = (
        uuid.uuid4()
        .hex[:5]
        .upper()
    )

    return (
        f"QG{now:%Y%m%d}"
        f"-{suffix}"
    )

def generate_promotion_code():

    now = datetime.now().astimezone()

    suffix = (
        uuid.uuid4()
        .hex[:5]
        .upper()
    )

    return (
        f"KM{now:%Y%m%d}"
        f"-{suffix}"
    )

def generate_milestone_code():

    now = datetime.now().astimezone()

    suffix = (
        uuid.uuid4()
        .hex[:5]
        .upper()
    )

    return (
        f"MC{now:%Y%m%d}"
        f"-{suffix}"
    )


@app.post(
    "/khuyen-mai/tang-voucher"
)
def tang_voucher():

    try:

        # ==========================================
        # 1. FORM HIỆN TẠI GỬI ID THÀNH VIÊN
        #    NHƯNG DB VOUCHER CHỈ LƯU ma_thanh_vien
        # ==========================================
        member_ids_raw = (
            request.form.getlist(
                "thanh_vien_ids"
            )
        )

        member_ids = []

        for value in member_ids_raw:

            try:
                member_ids.append(
                    int(value)
                )

            except (
                TypeError,
                ValueError
            ):
                continue

        member_ids = list(
            dict.fromkeys(
                member_ids
            )
        )

        if not member_ids:

            raise ValueError(
                "Vui lòng chọn ít nhất 1 thành viên."
            )


        # ==========================================
        # 2. THÔNG TIN VOUCHER
        # ==========================================
        ten_voucher = clean_text(
            request.form.get(
                "ten_voucher"
            )
        )

        noi_dung = (
            clean_text(
                request.form.get(
                    "noi_dung"
                )
            )
            or None
        )

        gia_tri = normalize_voucher_money(
            request.form.get(
                "gia_tri"
            )
        )

        dieu_kien = (
            voucher_conditions_from_form(
                request.form
            )
        )

        ngay_bat_dau = (
            parse_voucher_date(
                request.form.get(
                    "ngay_bat_dau"
                )
            )
            or datetime.now()
            .astimezone()
            .isoformat()
        )

        ngay_het_han = (
            parse_voucher_date(
                request.form.get(
                    "ngay_het_han"
                ),
                end_of_day=True
            )
        )

        if not ten_voucher:

            raise ValueError(
                "Tên Voucher không được để trống."
            )


        # ==========================================
        # 3. ĐỔI ID FORM -> ma_thanh_vien
        # ==========================================
        selected_members = (
            supabase
            .table(TABLE_NAME)
            .select(
                "id,"
                "ma_thanh_vien,"
                "ho_va_ten,"
                "so_dien_thoai"
            )
            .in_(
                "id",
                member_ids
            )
            .execute()
            .data
            or []
        )

        if not selected_members:

            raise ValueError(
                "Không tìm thấy thành viên đã chọn."
            )

        member_map = {
            int(
                row.get("id")
            ): row

            for row in selected_members

            if row.get("id") is not None
        }


        # ==========================================
        # 4. INSERT CHỈ BẰNG MÃ THÀNH VIÊN
        # ==========================================
        rows_to_insert = []

        for member_id in member_ids:

            member = member_map.get(
                int(member_id)
            )

            if not member:
                continue

            member_code = clean_text(
                member.get(
                    "ma_thanh_vien"
                )
            ).upper()

            if not member_code:
                continue

            now_iso = (
                datetime.now()
                .astimezone()
                .isoformat()
            )

            rows_to_insert.append({

                "ma_voucher":
                    generate_voucher_code(),

                "ma_thanh_vien":
                    member_code,

                "ten_voucher":
                    ten_voucher,

                "noi_dung":
                    noi_dung,

                "gia_tri":
                    gia_tri,

                "dieu_kien":
                    dieu_kien,

                "ngay_bat_dau":
                    ngay_bat_dau,

                "ngay_het_han":
                    ngay_het_han,

                "trang_thai":
                    "Chưa sử dụng",

                "created_at":
                    now_iso,

                "updated_at":
                    now_iso,
            })


        if not rows_to_insert:

            raise ValueError(
                "Không có thành viên hợp lệ để gửi Voucher."
            )

        (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .insert(
                rows_to_insert
            )
            .execute()
        )

        flash(
            (
                f"Đã gửi Voucher cho "
                f"{len(rows_to_insert)} thành viên."
            ),
            "success"
        )


    except Exception as exc:

        print(
            "[SEND MULTI VOUCHER ERROR]",
            repr(exc)
        )

        flash(
            f"Không gửi được Voucher: {exc}",
            "danger"
        )


    return redirect(
        url_for(
            "khuyen_mai_admin"
        )
    )


@app.post(
    "/khuyen-mai/voucher/<int:voucher_id>/cap-nhat"
)
def cap_nhat_voucher(
    voucher_id
):

    try:

        voucher_rows = (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .select(
                "id,"
                "ma_voucher"
            )
            .eq(
                "id",
                voucher_id
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not voucher_rows:

            raise ValueError(
                "Không tìm thấy Voucher."
            )

        voucher_code = clean_text(
            voucher_rows[0].get(
                "ma_voucher"
            )
        )

        ten_voucher = clean_text(
            request.form.get(
                "ten_voucher"
            )
        )

        if not ten_voucher:

            raise ValueError(
                "Tên Voucher không được để trống."
            )

        trang_thai = (
            clean_text(
                request.form.get(
                    "trang_thai"
                )
            )
            or "Chưa sử dụng"
        )

        allowed_status = {
            "Chưa sử dụng",
            "Đã sử dụng",
            "Đã kích hoạt",
            "Hết hạn",
        }

        if (
            trang_thai
            not in allowed_status
        ):
            trang_thai = (
                "Chưa sử dụng"
            )

        payload = {

            "ten_voucher":
                ten_voucher,

            "noi_dung":
                clean_text(
                    request.form.get(
                        "noi_dung"
                    )
                )
                or None,

            "gia_tri":
                normalize_voucher_money(
                    request.form.get(
                        "gia_tri"
                    )
                ),

            "dieu_kien":
                voucher_conditions_from_form(
                    request.form
                ),

            "ngay_bat_dau":
                parse_voucher_date(
                    request.form.get(
                        "ngay_bat_dau"
                    )
                ),

            "ngay_het_han":
                parse_voucher_date(
                    request.form.get(
                        "ngay_het_han"
                    ),
                    end_of_day=True
                ),

            "trang_thai":
                trang_thai,

            "updated_at":
                datetime.now()
                .astimezone()
                .isoformat(),
        }

        (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .update(payload)
            .eq(
                "ma_voucher",
                voucher_code
            )
            .execute()
        )

        flash(
            "Đã cập nhật Voucher.",
            "success"
        )


    except Exception as exc:

        print(
            "[UPDATE VOUCHER ERROR]",
            repr(exc)
        )

        flash(
            f"Không cập nhật được Voucher: {exc}",
            "danger"
        )


    return redirect(
        url_for(
            "khuyen_mai_admin"
        )
    )


@app.post(
    "/khuyen-mai/voucher/<int:voucher_id>/xoa"
)
def xoa_voucher(
    voucher_id
):

    try:

        voucher_rows = (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .select(
                "id,"
                "ma_voucher"
            )
            .eq(
                "id",
                voucher_id
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not voucher_rows:

            raise ValueError(
                "Không tìm thấy Voucher."
            )

        voucher_code = clean_text(
            voucher_rows[0].get(
                "ma_voucher"
            )
        )

        (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .delete()
            .eq(
                "ma_voucher",
                voucher_code
            )
            .execute()
        )

        flash(
            "Đã xóa Voucher.",
            "success"
        )


    except Exception as exc:

        print(
            "[DELETE VOUCHER ERROR]",
            repr(exc)
        )

        flash(
            f"Không xóa được Voucher: {exc}",
            "danger"
        )


    return redirect(
        url_for(
            "khuyen_mai_admin"
        )
    )


# ==========================================================
# KÍCH HOẠT QUÀ / VOUCHER
# ==========================================================
@app.get("/kich-hoat-qua")
def kich_hoat_qua():

    q = clean_text(
        request.args.get("q")
    ).upper()

    try:
        page = max(
            1,
            int(
                request.args.get("page")
                or 1
            )
        )
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(
            request.args.get("per_page")
            or 30
        )
    except (TypeError, ValueError):
        per_page = 30

    allowed_per_page = {
        30,
        50,
        100,
        200,
        500,
    }

    if per_page not in allowed_per_page:
        per_page = 30

    try:
        reward_query = (
            supabase
            .table(PROMOTION_REDEEM_TABLE)
            .select(
                "ma_qua,"
                "ma_thanh_vien,"
                "ten_qua_tang,"
                "trang_thai,"
                "kich_hoat_at,"
                "created_at"
            )
            .order(
                "created_at",
                desc=True
            )
        )

        voucher_query = (
            supabase
            .table(PROMOTION_VOUCHER_TABLE)
            .select(
                "ma_voucher,"
                "ma_thanh_vien,"
                "ten_voucher,"
                "gia_tri,"
                "dieu_kien,"
                "trang_thai,"
                "kich_hoat_at,"
                "created_at"
            )
            .order(
                "created_at",
                desc=True
            )
        )

        if q:
            reward_query = reward_query.ilike(
                "ma_qua",
                f"%{q}%"
            )

            voucher_query = voucher_query.ilike(
                "ma_voucher",
                f"%{q}%"
            )

        rewards = (
            reward_query
            .execute()
            .data
            or []
        )

        vouchers = (
            voucher_query
            .execute()
            .data
            or []
        )

        member_codes = {
            clean_text(
                row.get("ma_thanh_vien")
            ).upper()
            for row in (rewards + vouchers)
            if clean_text(
                row.get("ma_thanh_vien")
            )
        }

        member_map = {}

        if member_codes:
            member_rows = (
                supabase
                .table(TABLE_NAME)
                .select(
                    "ma_thanh_vien,"
                    "ho_va_ten"
                )
                .in_(
                    "ma_thanh_vien",
                    list(member_codes)
                )
                .execute()
                .data
                or []
            )

            member_map = {
                clean_text(
                    row.get("ma_thanh_vien")
                ).upper(): clean_text(
                    row.get("ho_va_ten")
                )
                for row in member_rows
            }

        all_items = []

        for reward in rewards:
            code = clean_text(
                reward.get("ma_qua")
            )

            member_code = clean_text(
                reward.get("ma_thanh_vien")
            ).upper()

            activated_at = parse_iso_datetime(
                reward.get("kich_hoat_at")
            )

            all_items.append({
                "loai": "QUA",
                "loai_hien_thi": "Quà tặng",
                "ma": code,
                "ma_thanh_vien": member_code,
                "ho_va_ten": member_map.get(
                    member_code,
                    ""
                ),
                "ten": clean_text(
                    reward.get("ten_qua_tang")
                ),
                "gia_tri_hien_thi": "",
                "dieu_kien_dong": [],
                "dieu_kien_hien_thi": "",
                "trang_thai": clean_text(
                    reward.get("trang_thai")
                ) or "Chưa nhận quà",
                "da_xac_nhan": (
                    bool(activated_at)
                    or clean_text(
                        reward.get("trang_thai")
                    ) in {
                        "Đã đổi quà",
                        "Đã nhận quà",
                    }
                ),
                "kich_hoat_at_hien_thi": (
                    activated_at
                    .astimezone()
                    .strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )
                    if activated_at
                    else ""
                ),
                "created_at": clean_text(
                    reward.get("created_at")
                ),
            })

        for voucher in vouchers:
            code = clean_text(
                voucher.get("ma_voucher")
            )

            member_code = clean_text(
                voucher.get("ma_thanh_vien")
            ).upper()

            activated_at = parse_iso_datetime(
                voucher.get("kich_hoat_at")
            )

            conditions = normalize_voucher_conditions(
                voucher.get("dieu_kien")
            )

            condition_lines = [
                line
                for line in (
                    voucher_condition_line(item)
                    for item in conditions
                )
                if line
            ]

            all_items.append({
                "loai": "VOUCHER",
                "loai_hien_thi": "Voucher",
                "ma": code,
                "ma_thanh_vien": member_code,
                "ho_va_ten": member_map.get(
                    member_code,
                    ""
                ),
                "ten": clean_text(
                    voucher.get("ten_voucher")
                ),
                "gia_tri_hien_thi": discount_benefit_display(
                    *discount_benefit_value(voucher.get("gia_tri"), allow_bare_amount=False)
                ),
                "dieu_kien": conditions,
                "dieu_kien_dong": condition_lines,
                "dieu_kien_hien_thi": " ".join(
                    condition_lines
                ),
                "trang_thai": clean_text(
                    voucher.get("trang_thai")
                ) or "Chưa sử dụng",
                "da_xac_nhan": (
                    bool(activated_at)
                    or clean_text(
                        voucher.get("trang_thai")
                    ) in {
                        "Đã sử dụng",
                        "Đã kích hoạt",
                    }
                ),
                "kich_hoat_at_hien_thi": (
                    activated_at
                    .astimezone()
                    .strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )
                    if activated_at
                    else ""
                ),
                "created_at": clean_text(
                    voucher.get("created_at")
                ),
            })

        # ======================================================
        # SẮP XẾP DANH SÁCH KÍCH HOẠT QUÀ / VOUCHER
        # Ưu tiên:
        #   1. Mục có thể Kích hoạt trực tiếp
        #   2. Mục chưa sử dụng nhưng phải áp dụng qua Hoạt Động
        #   3. Mục đã sử dụng / đã xác nhận
        # Trong cùng một nhóm: mục mới nhất lên trước.
        # ======================================================
        def activation_sort_priority(row):
            is_confirmed = bool(
                row.get("da_xac_nhan")
            )

            condition_lines = (
                row.get("dieu_kien_dong")
                or []
            )

            item_type = clean_text(
                row.get("loai")
            ).upper()

            # Nhóm 3: đã sử dụng / đã nhận / đã xác nhận
            if is_confirmed:
                priority = 2

            # Nhóm 2: Voucher chưa dùng nhưng có điều kiện,
            # phải áp dụng trong trang Hoạt Động
            elif (
                item_type == "VOUCHER"
                and condition_lines
            ):
                priority = 1

            # Nhóm 1: có nút Kích hoạt trực tiếp
            else:
                priority = 0

            return (
                priority,
                row.get("created_at") or ""
            )

        # Python sort tăng dần theo priority.
        # Riêng thời gian cần mới nhất trước, nên sort từng nhóm
        # bằng timestamp đảo chiều thông qua hai bước ổn định.
        all_items.sort(
            key=lambda row: (
                row.get("created_at")
                or ""
            ),
            reverse=True
        )

        all_items.sort(
            key=lambda row: (
                activation_sort_priority(row)[0]
            )
        )

        total_items = len(all_items)
        total_pages = max(
            1,
            (
                total_items
                + per_page
                - 1
            ) // per_page
        )

        if page > total_pages:
            page = total_pages

        start_index = (
            page - 1
        ) * per_page

        end_index = (
            start_index
            + per_page
        )

        items = all_items[
            start_index:end_index
        ]

        display_from = (
            start_index + 1
            if total_items
            else 0
        )

        display_to = min(
            end_index,
            total_items
        )

    except Exception as exc:
        print(
            "[ACTIVATION LIST ERROR]",
            repr(exc)
        )

        items = []
        total_items = 0
        total_pages = 1
        page = 1
        display_from = 0
        display_to = 0

        flash(
            f"Không tải được danh sách Quà/Voucher: {exc}",
            "danger"
        )

    return render_template(
        "Kichhoatqua.html",
        items=items,
        q=q,
        page=page,
        per_page=per_page,
        total_items=total_items,
        total_pages=total_pages,
        display_from=display_from,
        display_to=display_to,
    )

@app.get("/api/kich-hoat-qua/tra-cuu/<string:code>")
def api_tra_cuu_qua_voucher(code):
    """Tra cứu chính xác Quà/Voucher sau khi quét QR."""

    code = clean_text(code).upper()

    if not code:
        return jsonify({
            "ok": False,
            "message": "Mã QR không hợp lệ."
        }), 400

    try:
        # ------------------------------------------
        # 1. Tìm Quà tặng trước
        # ------------------------------------------
        reward_rows = (
            supabase
            .table(PROMOTION_REDEEM_TABLE)
            .select(
                "ma_qua,"
                "ma_thanh_vien,"
                "ten_qua_tang,"
                "trang_thai,"
                "kich_hoat_at,"
                "created_at"
            )
            .eq("ma_qua", code)
            .limit(1)
            .execute()
            .data
            or []
        )

        row = None
        item_type = ""

        if reward_rows:
            row = reward_rows[0]
            item_type = "QUA"

        # ------------------------------------------
        # 2. Nếu không phải Quà thì tìm Voucher
        # ------------------------------------------
        if row is None:
            voucher_rows = (
                supabase
                .table(PROMOTION_VOUCHER_TABLE)
                .select(
                    "ma_voucher,"
                    "ma_thanh_vien,"
                    "ten_voucher,"
                    "gia_tri,"
                    "noi_dung,"
                    "ngay_bat_dau,"
                    "ngay_het_han,"
                    "dieu_kien,"
                    "trang_thai,"
                    "kich_hoat_at,"
                    "created_at"
                )
                .eq("ma_voucher", code)
                .limit(1)
                .execute()
                .data
                or []
            )

            if voucher_rows:
                row = voucher_rows[0]
                item_type = "VOUCHER"

        if row is None:
            return jsonify({
                "ok": False,
                "message": "Không tìm thấy Quà/Voucher tương ứng với mã QR này."
            }), 404

        member_code = clean_text(
            row.get("ma_thanh_vien")
        ).upper()

        member_name = ""
        if member_code:
            member_rows = (
                supabase
                .table(TABLE_NAME)
                .select("ho_va_ten")
                .eq("ma_thanh_vien", member_code)
                .limit(1)
                .execute()
                .data
                or []
            )
            if member_rows:
                member_name = clean_text(
                    member_rows[0].get("ho_va_ten")
                )

        activated_at = parse_iso_datetime(
            row.get("kich_hoat_at")
        )
        current_status = clean_text(
            row.get("trang_thai")
        )

        if item_type == "QUA":
            used = (
                bool(activated_at)
                or current_status in {
                    "Đã đổi quà",
                    "Đã nhận quà",
                }
            )

            return jsonify({
                "ok": True,
                "item": {
                    "loai": "QUA",
                    "loai_hien_thi": "Quà tặng",
                    "ma": code,
                    "ma_thanh_vien": member_code,
                    "ho_va_ten": member_name,
                    "ten": clean_text(
                        row.get("ten_qua_tang")
                    ) or "Quà tặng",
                    "gia_tri_hien_thi": "",
                    "trang_thai": current_status or "Chưa nhận quà",
                    "da_xac_nhan": used,
                    "dieu_kien_dong": [],
                    "co_dieu_kien": False,
                    "can_kich_hoat_truc_tiep": not used,
                }
            })

        conditions = normalize_voucher_conditions(
            row.get("dieu_kien")
        )
        condition_lines = [
            line
            for line in (
                voucher_condition_line(item)
                for item in conditions
            )
            if line
        ]

        used = (
            bool(activated_at)
            or current_status in {
                "Đã sử dụng",
                "Đã kích hoạt",
            }
        )

        return jsonify({
            "ok": True,
            "item": {
                "loai": "VOUCHER",
                "loai_hien_thi": "Voucher",
                "ma": code,
                "ma_thanh_vien": member_code,
                "ho_va_ten": member_name,
                "ten": clean_text(
                    row.get("ten_voucher")
                ) or "Voucher",
                "gia_tri_hien_thi": discount_benefit_display(*discount_benefit_value(row.get("gia_tri"), allow_bare_amount=False)),
                "noi_dung": clean_text(
                    row.get("noi_dung")
                ),
                "trang_thai": current_status or "Chưa sử dụng",
                "da_xac_nhan": used,
                "dieu_kien_dong": condition_lines,
                "co_dieu_kien": bool(condition_lines),
                # Voucher có điều kiện bắt buộc đi qua Hoạt Động
                # để kiểm tra thời lượng / tổng tiền / dịch vụ.
                "can_kich_hoat_truc_tiep": (
                    (not used)
                    and (not condition_lines)
                ),
            }
        })

    except Exception as exc:
        print(
            "[QR LOOKUP ERROR]",
            repr(exc)
        )
        return jsonify({
            "ok": False,
            "message": f"Không tra cứu được mã QR: {exc}"
        }), 500


@app.post(
    "/kich-hoat-qua/xac-nhan"
)
def xac_nhan_kich_hoat_qua():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    item_type = clean_text(
        data.get("loai")
    ).upper()

    code = clean_text(
        data.get("ma")
    ).upper()

    if item_type not in {
        "QUA",
        "VOUCHER"
    }:
        return jsonify({
            "ok": False,
            "message": "Loại kích hoạt không hợp lệ."
        }), 400

    if not code:
        return jsonify({
            "ok": False,
            "message": "Thiếu mã Quà/Voucher."
        }), 400

    try:
        now_iso = (
            datetime.now()
            .astimezone()
            .isoformat()
        )

        if item_type == "QUA":

            rows = (
                supabase
                .table(
                    PROMOTION_REDEEM_TABLE
                )
                .select(
                    "ma_qua,"
                    "trang_thai,"
                    "kich_hoat_at"
                )
                .eq(
                    "ma_qua",
                    code
                )
                .limit(1)
                .execute()
                .data
                or []
            )

            if not rows:
                return jsonify({
                    "ok": False,
                    "message": "Không tìm thấy mã quà."
                }), 404

            current_status = clean_text(
                rows[0].get(
                    "trang_thai"
                )
            )

            if (
                rows[0].get(
                    "kich_hoat_at"
                )
                or current_status in {
                    "Đã đổi quà",
                    "Đã nhận quà",
                }
            ):
                return jsonify({
                    "ok": True,
                    "already": True,
                    "message": "Quà này đã được xác nhận."
                })

            (
                supabase
                .table(
                    PROMOTION_REDEEM_TABLE
                )
                .update({
                    "trang_thai":
                        "Đã đổi quà",
                    "kich_hoat_at":
                        now_iso,
                    "updated_at":
                        now_iso,
                })
                .eq(
                    "ma_qua",
                    code
                )
                .execute()
            )

            member_status = (
                "Đã đổi quà"
            )

        else:

            rows = (
                supabase
                .table(
                    PROMOTION_VOUCHER_TABLE
                )
                .select(
                    "ma_voucher,"
                    "dieu_kien,"
                    "trang_thai,"
                    "kich_hoat_at"
                )
                .eq(
                    "ma_voucher",
                    code
                )
                .limit(1)
                .execute()
                .data
                or []
            )

            if not rows:
                return jsonify({
                    "ok": False,
                    "message": "Không tìm thấy mã Voucher."
                }), 404

            current_status = clean_text(
                rows[0].get(
                    "trang_thai"
                )
            )

            voucher_conditions = (
                normalize_voucher_conditions(
                    rows[0].get(
                        "dieu_kien"
                    )
                )
            )

            if voucher_conditions:
                return jsonify({
                    "ok": False,
                    "message":
                        "Voucher này có điều kiện sử dụng. "
                        "Vui lòng áp dụng tại phần Hoạt Động khi lập hóa đơn "
                        "để hệ thống kiểm tra đủ điều kiện."
                }), 400

            if (
                rows[0].get(
                    "kich_hoat_at"
                )
                or current_status in {
                    "Đã sử dụng",
                    "Đã kích hoạt",
                }
            ):
                return jsonify({
                    "ok": True,
                    "already": True,
                    "message": "Voucher này đã được xác nhận."
                })

            (
                supabase
                .table(
                    PROMOTION_VOUCHER_TABLE
                )
                .update({
                    "trang_thai":
                        "Đã kích hoạt",
                    "kich_hoat_at":
                        now_iso,
                    "updated_at":
                        now_iso,
                })
                .eq(
                    "ma_voucher",
                    code
                )
                .execute()
            )

            member_status = (
                "Đã kích hoạt"
            )

        return jsonify({
            "ok": True,
            "message": "Xác nhận thành công.",
            "trang_thai": member_status,
            "kich_hoat_at": now_iso,
        })

    except Exception as exc:
        print(
            "[ACTIVATION CONFIRM ERROR]",
            repr(exc)
        )

        return jsonify({
            "ok": False,
            "message":
                f"Không xác nhận được: {exc}"
        }), 500



@app.get(
    "/api/thanh-vien/<string:member_code>/uu-dai-tien"
)
def api_uu_dai_tien_thanh_vien(
    member_code
):
    """
    Trả về Voucher / Quà tặng có giá trị tiền
    chưa được kích hoạt và thuộc đúng Mã thành viên.
    """

    member_code = clean_text(
        member_code
    ).upper()

    if not member_code:
        return jsonify({
            "ok": False,
            "message":
                "Thiếu mã thành viên.",
            "items": []
        }), 400

    try:

        now = datetime.now(
            VIETNAM_TZ
        )

        today_vn = now.date()

        items = []


        # ==========================================
        # 1. VOUCHER GIẢM TIỀN
        # ==========================================
        voucher_rows = (
            supabase
            .table(
                PROMOTION_VOUCHER_TABLE
            )
            .select(
                "ma_voucher,"
                "ma_thanh_vien,"
                "ten_voucher,"
                "gia_tri,"
                "dieu_kien,"
                "ngay_bat_dau,"
                "ngay_het_han,"
                "trang_thai,"
                "kich_hoat_at,"
                "created_at"
            )
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
            or []
        )

        for row in voucher_rows:

            # Nguồn quyết định chính là trạng thái nghiệp vụ.
            # Nếu Voucher vẫn đang "Chưa sử dụng" thì phải cho phép
            # xuất hiện trong combobox, kể cả trường kich_hoat_at từng
            # bị ghi nhầm từ dữ liệu cũ.
            status = clean_text(
                row.get(
                    "trang_thai"
                )
            )

            allowed_voucher_status = {
                "",
                "Chưa sử dụng",
                "Chưa kích hoạt",
            }

            if status not in allowed_voucher_status:
                continue

            start_dt = parse_iso_datetime(
                row.get(
                    "ngay_bat_dau"
                )
            )

            end_dt = parse_iso_datetime(
                row.get(
                    "ngay_het_han"
                )
            )

            # Voucher nhập theo NGÀY nên kiểm tra theo ngày Việt Nam,
            # không so giờ-phút-giây.
            if start_dt:
                start_date_vn = (
                    start_dt
                    .astimezone(
                        VIETNAM_TZ
                    )
                    .date()
                )

                if today_vn < start_date_vn:
                    continue

            if end_dt:
                end_date_vn = (
                    end_dt
                    .astimezone(
                        VIETNAM_TZ
                    )
                    .date()
                )

                if today_vn > end_date_vn:
                    continue

            benefit_kind, amount = discount_benefit_value(
                row.get("gia_tri"), allow_bare_amount=False
            )
            if benefit_kind == "NONE":
                continue

            items.append({
                "loai": "VOUCHER",
                "ma":
                    clean_text(
                        row.get(
                            "ma_voucher"
                        )
                    ),
                "ten":
                    clean_text(
                        row.get(
                            "ten_voucher"
                        )
                    )
                    or "Voucher",
                "gia_tri": amount,
                "kieu_giam": benefit_kind,
                "gia_tri_hien_thi": discount_benefit_display(benefit_kind, amount),
                "dieu_kien":
                    normalize_voucher_conditions(
                        row.get("dieu_kien")
                    ),
                "dieu_kien_dong": [
                    voucher_condition_line(item)
                    for item in normalize_voucher_conditions(
                        row.get("dieu_kien")
                    )
                    if voucher_condition_line(item)
                ],
                "dieu_kien_hien_thi": " ".join(
                    voucher_condition_line(item)
                    for item in normalize_voucher_conditions(
                        row.get("dieu_kien")
                    )
                    if voucher_condition_line(item)
                ),
            })


        # ==========================================
        # 2. QUÀ TẶNG CÓ GIÁ TRỊ TIỀN
        # ==========================================
        reward_rows = (
            supabase
            .table(
                PROMOTION_REDEEM_TABLE
            )
            .select(
                "ma_qua,"
                "ma_thanh_vien,"
                "ten_qua_tang,"
                "trang_thai,"
                "kich_hoat_at,"
                "created_at"
            )
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
            or []
        )

        for row in reward_rows:

            # Tương tự Voucher: trạng thái nghiệp vụ là nguồn chính.
            # Quà còn "Chưa nhận quà" vẫn được phép sử dụng nếu
            # bản chất phần quà là tiền/phiếu giảm tiền.
            status = clean_text(
                row.get(
                    "trang_thai"
                )
            )

            allowed_reward_status = {
                "",
                "Chưa nhận quà",
                "Chưa sử dụng",
            }

            if status not in allowed_reward_status:
                continue

            gift_name = clean_text(
                row.get(
                    "ten_qua_tang"
                )
            )

            benefit_kind, amount = discount_benefit_value(gift_name)
            # Gấu bông, nước, quà vật phẩm không được giảm vào hóa đơn.
            if benefit_kind == "NONE":
                continue

            items.append({
                "loai": "QUA",
                "ma":
                    clean_text(
                        row.get(
                            "ma_qua"
                        )
                    ),
                "ten":
                    gift_name
                    or "Quà tặng",
                "gia_tri": amount,
                "kieu_giam": benefit_kind,
                "gia_tri_hien_thi": discount_benefit_display(benefit_kind, amount),
            })


        print(
            "[MEMBER CASH BENEFIT]",
            member_code,
            "voucher_rows=", len(voucher_rows),
            "reward_rows=", len(reward_rows),
            "eligible_items=", len(items)
        )

        return jsonify({
            "ok": True,
            "items": items
        })


    except Exception as exc:

        print(
            "[MEMBER CASH BENEFIT ERROR]",
            type(exc).__name__
        )

        return jsonify({
            "ok": False,
            "message":
                f"Không tải được Voucher/Quà giảm tiền: {exc}",
            "items": []
        }), 500




# ==========================================================
# BACKUP / RESTORE
# ==========================================================
def _backup_storage_bucket():
    return supabase.storage.from_(BACKUP_BUCKET)


def _backup_prefix_for_kind(kind):
    kind = clean_text(kind).lower()
    if kind == "manual":
        return BACKUP_MANUAL_PREFIX, BACKUP_MANUAL_KEEP
    if kind == "safety":
        return BACKUP_SAFETY_PREFIX, BACKUP_SAFETY_KEEP
    return BACKUP_AUTO_PREFIX, BACKUP_AUTO_KEEP


def _backup_export_table(table_name):
    rows = []
    offset = 0

    while True:
        result = (
            supabase
            .table(table_name)
            .select("*")
            .range(
                offset,
                offset + BACKUP_EXPORT_PAGE_SIZE - 1
            )
            .execute()
        )

        batch = result.data or []
        rows.extend(batch)

        if len(batch) < BACKUP_EXPORT_PAGE_SIZE:
            break

        offset += BACKUP_EXPORT_PAGE_SIZE

    return rows


def _backup_add_bytes(tar, arcname, data_bytes):
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data_bytes)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data_bytes))


def _backup_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")


def _backup_list_prefix(prefix):
    try:
        items = _backup_storage_bucket().list(prefix) or []
    except Exception as exc:
        raise RuntimeError(
            f"Không đọc được Supabase Storage bucket '{BACKUP_BUCKET}': {exc}"
        ) from exc

    output = []

    for item in items:
        if not isinstance(item, dict):
            continue

        name = clean_text(item.get("name"))
        if not name or not name.endswith(".tar.gz"):
            continue

        metadata = item.get("metadata") or {}
        try:
            size = int(metadata.get("size") or item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0

        dt = None
        match = re.search(r"(\d{8})-(\d{6})", name)
        if match:
            try:
                dt = datetime.strptime(
                    match.group(1) + match.group(2),
                    "%Y%m%d%H%M%S"
                ).replace(tzinfo=VIETNAM_TZ)
            except ValueError:
                dt = None

        output.append({
            "name": name,
            "path": f"{prefix}/{name}",
            "size": size,
            "created_at": (
                dt.isoformat()
                if dt else clean_text(item.get("created_at"))
            ),
            "created_at_hien_thi": (
                dt.strftime("%d/%m/%Y %H:%M")
                if dt else clean_text(item.get("created_at"))
            ),
        })

    output.sort(
        key=lambda x: x.get("name") or "",
        reverse=True
    )
    return output


def _backup_prune(prefix, keep):
    items = _backup_list_prefix(prefix)
    old_items = items[keep:]

    if old_items:
        paths = [item["path"] for item in old_items]
        _backup_storage_bucket().remove(paths)

    return len(old_items)


def create_system_backup(kind="automatic"):
    """
    Tạo logical backup gồm club_setup.json + toàn bộ các bảng chính,
    nén tar.gz và upload vào Supabase Storage.
    """
    prefix, keep = _backup_prefix_for_kind(kind)
    now = datetime.now(VIETNAM_TZ)
    filename = f"backup-{now:%Y%m%d-%H%M%S}.tar.gz"
    remote_path = f"{prefix}/{filename}"

    table_data = {}
    row_counts = {}

    for table_name in BACKUP_TABLES:
        rows = _backup_export_table(table_name)
        table_data[table_name] = rows
        row_counts[table_name] = len(rows)

    setup_config = load_setup_config()

    manifest = {
        "format": "bida-logical-backup",
        "version": 1,
        "created_at": now.isoformat(),
        "timezone": "Asia/Ho_Chi_Minh",
        "kind": kind,
        "bucket": BACKUP_BUCKET,
        "tables": row_counts,
        "setup_file": "app_config/club_setup.json",
    }

    archive_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=archive_buffer,
        mode="w:gz"
    ) as tar:
        _backup_add_bytes(
            tar,
            "manifest.json",
            _backup_json_bytes(manifest)
        )
        _backup_add_bytes(
            tar,
            "app_config/club_setup.json",
            _backup_json_bytes(setup_config)
        )

        for table_name, rows in table_data.items():
            _backup_add_bytes(
                tar,
                f"database/{table_name}.json",
                _backup_json_bytes(rows)
            )

    archive_bytes = archive_buffer.getvalue()
    sha256 = hashlib.sha256(archive_bytes).hexdigest()

    bucket = _backup_storage_bucket()
    file_options = {
        "content-type": "application/gzip",
        "upsert": "false",
    }

    try:
        bucket.upload(
            remote_path,
            archive_bytes,
            file_options=file_options,
        )
    except TypeError:
        # Tương thích một số phiên bản supabase-py cũ.
        bucket.upload(
            remote_path,
            archive_bytes,
            file_options,
        )

    deleted = _backup_prune(prefix, keep)

    return {
        "ok": True,
        "name": filename,
        "path": remote_path,
        "kind": kind,
        "size": len(archive_bytes),
        "sha256": sha256,
        "row_counts": row_counts,
        "deleted_old": deleted,
        "created_at": now.isoformat(),
    }


def list_system_backups():
    items = []

    for kind, prefix in (
        ("automatic", BACKUP_AUTO_PREFIX),
        ("manual", BACKUP_MANUAL_PREFIX),
        ("safety", BACKUP_SAFETY_PREFIX),
    ):
        for item in _backup_list_prefix(prefix):
            item["kind"] = kind
            items.append(item)

    items.sort(
        key=lambda x: x.get("name") or "",
        reverse=True
    )
    return items


def _backup_read_json_from_tar(tar, name):
    member = tar.getmember(name)
    file_obj = tar.extractfile(member)
    if file_obj is None:
        raise ValueError(f"Backup thiếu file {name}.")
    return json.loads(file_obj.read().decode("utf-8"))


def _backup_delete_all_rows(table_name):
    # Chỉ delete khi bảng thực sự có dữ liệu, tránh filter giả gây lỗi.
    first_rows = (
        supabase
        .table(table_name)
        .select("id")
        .limit(1)
        .execute()
        .data
        or []
    )

    if not first_rows:
        return

    first_id = first_rows[0].get("id")
    if first_id is None:
        raise RuntimeError(
            f"Bảng {table_name} không có cột id để restore an toàn."
        )

    # id >= 0 bao phủ các identity id thông thường trong hệ thống.
    (
        supabase
        .table(table_name)
        .delete()
        .gte("id", 0)
        .execute()
    )


def _backup_prepare_restore_rows(table_name, rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue

        item = dict(row)
        # Không restore identity ID để tránh lệch PostgreSQL sequence.
        item.pop("id", None)

        # created_by có thể là ID admin cũ; khi ID được sinh lại thì bỏ liên kết này.
        if table_name == ADMIN_TABLE and "created_by" in item:
            item["created_by"] = None

        cleaned.append(item)
    return cleaned


def restore_system_backup(remote_path):
    remote_path = clean_text(remote_path).strip("/")
    allowed_prefixes = (
        BACKUP_AUTO_PREFIX + "/",
        BACKUP_MANUAL_PREFIX + "/",
        BACKUP_SAFETY_PREFIX + "/",
    )

    if not remote_path.endswith(".tar.gz") or not remote_path.startswith(allowed_prefixes):
        raise ValueError("Đường dẫn backup không hợp lệ.")

    archive_bytes = _backup_storage_bucket().download(remote_path)
    if not archive_bytes:
        raise ValueError("Không tải được file backup.")

    # Trước restore luôn tạo một safety backup riêng.
    safety_backup = create_system_backup(kind="safety")

    with tarfile.open(
        fileobj=io.BytesIO(bytes(archive_bytes)),
        mode="r:gz"
    ) as tar:
        manifest = _backup_read_json_from_tar(
            tar,
            "manifest.json"
        )

        if manifest.get("format") != "bida-logical-backup":
            raise ValueError("File không đúng định dạng backup của hệ thống.")

        version = int(manifest.get("version") or 0)
        if version != 1:
            raise ValueError(
                f"Phiên bản backup {version} chưa được hỗ trợ."
            )

        setup_config = _backup_read_json_from_tar(
            tar,
            "app_config/club_setup.json"
        )

        restore_data = {}
        for table_name in BACKUP_TABLES:
            restore_data[table_name] = _backup_read_json_from_tar(
                tar,
                f"database/{table_name}.json"
            )

    # Xóa dữ liệu cũ từ bảng con tới bảng cha.
    for table_name in BACKUP_DELETE_ORDER:
        _backup_delete_all_rows(table_name)

    # Khôi phục từ bảng cha tới bảng con, theo batch nhỏ.
    restored_counts = {}
    for table_name in BACKUP_RESTORE_ORDER:
        rows = _backup_prepare_restore_rows(
            table_name,
            restore_data.get(table_name) or []
        )

        restored_counts[table_name] = len(rows)
        for start in range(0, len(rows), 200):
            batch = rows[start:start + 200]
            if batch:
                (
                    supabase
                    .table(table_name)
                    .insert(batch)
                    .execute()
                )

    save_setup_config(setup_config)

    # Session hiện tại có thể chứa admin_id cũ; buộc đăng nhập lại sau restore.
    session.clear()

    return {
        "ok": True,
        "restored": restored_counts,
        "safety_backup": safety_backup.get("path"),
    }


@app.get("/api/setup/backups")
def api_backup_list():
    try:
        items = list_system_backups()
        return jsonify({
            "ok": True,
            "bucket": BACKUP_BUCKET,
            "auto_keep": BACKUP_AUTO_KEEP,
            "items": items,
        })
    except Exception as exc:
        print("[BACKUP LIST ERROR]", repr(exc))
        return jsonify({
            "ok": False,
            "message": f"Không tải được danh sách backup: {exc}",
        }), 500


@app.post("/api/setup/backups/create")
def api_backup_create():
    try:
        result = create_system_backup(kind="manual")
        return jsonify(result)
    except Exception as exc:
        print("[BACKUP CREATE ERROR]", repr(exc))
        return jsonify({
            "ok": False,
            "message": f"Không tạo được backup: {exc}",
        }), 500


@app.get("/api/setup/backups/download")
def api_backup_download():
    try:
        remote_path = clean_text(
            request.args.get("path")
        ).strip("/")

        allowed_prefixes = (
            BACKUP_AUTO_PREFIX + "/",
            BACKUP_MANUAL_PREFIX + "/",
            BACKUP_SAFETY_PREFIX + "/",
        )

        if not remote_path.endswith(".tar.gz") or not remote_path.startswith(allowed_prefixes):
            raise ValueError("Đường dẫn backup không hợp lệ.")

        content = _backup_storage_bucket().download(remote_path)
        filename = remote_path.rsplit("/", 1)[-1]

        return (
            bytes(content),
            200,
            {
                "Content-Type": "application/gzip",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as exc:
        print("[BACKUP DOWNLOAD ERROR]", repr(exc))
        return jsonify({
            "ok": False,
            "message": f"Không tải được backup: {exc}",
        }), 500


# ============================================================
# BACKUP CHU NHAT CHO ADMIN CHU CLB - MAY TINH
# - Chỉ Admin CLB có is_owner=true, không phải Admin hệ thống.
# - Thứ 7/Chủ nhật: CHỈ Chủ nhật theo giờ Việt Nam.
# - Popup nhắc tải và xác thực lại password trước khi tải.
# - ZIP AES-256: chủ CLB đặt một mật khẩu riêng cho file.
# - admin_info.weekly_backup_downloaded_on lưu theo TỪNG chủ.
# ============================================================
def owner_weekly_backup_csrf_token():
    token = session.get("owner_weekly_backup_csrf")
    if not token:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        session["owner_weekly_backup_csrf"] = token
    return token


def _owner_backup_eligible():
    if not session.get("admin_logged_in") or session.get("admin_kind") == "system":
        return None
    admin = get_current_admin()
    if not admin or not bool(admin.get("is_owner")) or not admin.get("id"):
        return None
    return admin


def _owner_backup_desktop_request():
    # Browser UA chỉ hỗ trợ UX, không phải biện pháp định danh an toàn tuyệt đối.
    ua = (request.headers.get("User-Agent") or "").lower()
    mobile_markers = ("android", "iphone", "ipad", "ipod", "mobile", "tablet", "windows phone")
    return bool(ua) and not any(marker in ua for marker in mobile_markers)


def _owner_backup_is_sunday():
    return datetime.now(VIETNAM_TZ).weekday() == 6


def _owner_backup_db_row(admin_id):
    rows = (supabase.table(ADMIN_TABLE)
            .select("id,active,is_owner,password_hash,weekly_backup_downloaded_on")
            .eq("id", admin_id).limit(1).execute().data or [])
    row = rows[0] if rows else None
    if not row or not row.get("active") or not row.get("is_owner"):
        return None
    return row


def _owner_backup_latest():
    items = _backup_list_prefix(BACKUP_AUTO_PREFIX)
    if not items:
        raise RuntimeError("Chưa có bản backup tự động để tải.")
    latest = items[0]
    when = datetime.fromisoformat(latest["created_at"])
    if when.tzinfo is None:
        when = when.replace(tzinfo=VIETNAM_TZ)
    if datetime.now(VIETNAM_TZ) - when > timedelta(hours=36):
        raise RuntimeError("Bản backup tự động quá cũ (trên 36 giờ). Vui lòng kiểm tra lịch backup.")
    return latest


@app.get("/api/owner-weekly-backup/status")
def owner_weekly_backup_status():
    admin = _owner_backup_eligible()
    if not admin or not _owner_backup_is_sunday() or not _owner_backup_desktop_request():
        return jsonify({"ok": True, "show": False}), 200
    try:
        row = _owner_backup_db_row(admin["id"])
        if not row:
            return jsonify({"ok": True, "show": False}), 200
        today = datetime.now(VIETNAM_TZ).date().isoformat()
        if str(row.get("weekly_backup_downloaded_on") or "") == today:
            return jsonify({"ok": True, "show": False}), 200
        latest = _owner_backup_latest()
        return jsonify({"ok": True, "show": True,
                        "backup_date": latest["created_at_hien_thi"],
                        "csrf": owner_weekly_backup_csrf_token()}), 200
    except Exception as exc:
        print("[WEEKLY BACKUP STATUS]", type(exc).__name__, str(exc)[:180])
        return jsonify({"ok": False, "show": False,
                        "message": "Không kiểm tra được backup Chủ nhật; vui lòng kiểm tra SQL migration."}), 503


@app.post("/api/owner-weekly-backup/download")
def owner_weekly_backup_download():
    admin = _owner_backup_eligible()
    if not admin or not _owner_backup_is_sunday() or not _owner_backup_desktop_request():
        return jsonify({"ok": False, "message": "Chức năng chỉ dành cho Admin Chủ CLB trên máy tính vào Chủ nhật."}), 403
    payload = request.get_json(silent=True) or {}
    csrf = str(payload.get("csrf") or "")
    expected = str(session.get("owner_weekly_backup_csrf") or "")
    if not expected or not hmac.compare_digest(csrf, expected):
        return jsonify({"ok": False, "message": "Phiên tải không hợp lệ, hãy tải lại trang."}), 403
    password = str(payload.get("admin_password") or "")
    archive_password = str(payload.get("archive_password") or "")
    if len(archive_password) < 12 or len(archive_password) > 128:
        return jsonify({"ok": False, "message": "Mật khẩu mã hóa file cần từ 12 đến 128 ký tự."}), 400
    try:
        row = _owner_backup_db_row(admin["id"])
        if not row:
            return jsonify({"ok": False, "message": "Admin Chủ không còn hiệu lực."}), 403
        valid = bool(password and row.get("password_hash")
                     and check_password_hash(row["password_hash"], password))
        if not valid:
            failures = int(session.get("owner_weekly_backup_failures") or 0) + 1
            session["owner_weekly_backup_failures"] = failures
            if failures >= 3:
                session.clear()
                return jsonify({"ok": False, "message": "Nhập sai 3 lần, phiên đăng nhập đã kết thúc.",
                                "logout": True}), 401
            return jsonify({"ok": False, "message": f"Mật khẩu Admin không đúng. Còn {3-failures} lần thử."}), 401
        session["owner_weekly_backup_failures"] = 0
        latest = _owner_backup_latest()
        source_bytes = bytes(_backup_storage_bucket().download(latest["path"]))
        if not source_bytes:
            raise RuntimeError("Không đọc được nội dung backup.")
        # Kiểm tra định dạng tối thiểu trước khi tạo file ZIP.
        with tarfile.open(fileobj=io.BytesIO(source_bytes), mode="r:gz") as tar:
            manifest = _backup_read_json_from_tar(tar, "manifest.json")
            if manifest.get("format") != "bida-logical-backup":
                raise RuntimeError("Backup không đúng định dạng Billiards Manager.")
            missing = [f"database/{name}.json" for name in BACKUP_TABLES
                       if f"database/{name}.json" not in tar.getnames()]
            if missing or "app_config/club_setup.json" not in tar.getnames():
                raise RuntimeError("Backup thiếu bảng hoặc Setup, không cho tải.")
        try:
            import pyzipper
        except ImportError as exc:
            raise RuntimeError("Chưa cài pyzipper. Chạy: venv/bin/pip install pyzipper") from exc
        zip_buffer = io.BytesIO()
        with pyzipper.AESZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword(archive_password.encode("utf-8"))
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            archive.writestr(latest["name"], source_bytes)
        # Lưu trạng thái cho TỪNG chủ. Chỉ đánh dấu đã gửi file, không bảo đảm browser đã lưu.
        today = datetime.now(VIETNAM_TZ).date().isoformat()
        (supabase.table(ADMIN_TABLE)
         .update({"weekly_backup_downloaded_on": today})
         .eq("id", admin["id"]).eq("is_owner", True).execute())
        session.pop("owner_weekly_backup_csrf", None)
        config = load_setup_config()
        club = _setup_slug(config.get("club", {}).get("name") or "CLB")
        filename = f"{club}_Backup_{today}.zip"
        return (zip_buffer.getvalue(), 200,
                {"Content-Type": "application/zip",
                 "Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store, private",
                 "X-Content-Type-Options": "nosniff"})
    except Exception as exc:
        print("[WEEKLY BACKUP DOWNLOAD]", type(exc).__name__, str(exc)[:180])
        return jsonify({"ok": False,
                        "message": "Không tạo được file backup bảo mật. Kiểm tra cấu hình và thử lại."}), 503


@app.post("/api/setup/backups/restore")
def api_backup_restore():
    current_admin = get_current_admin()
    if not current_admin or not bool(current_admin.get("is_owner")):
        return jsonify({
            "ok": False,
            "message": "Chỉ Admin Chủ được phép khôi phục dữ liệu.",
        }), 403

    try:
        data = request.get_json(silent=True) or {}
        remote_path = clean_text(data.get("path"))
        result = restore_system_backup(remote_path)
        return jsonify(result)
    except Exception as exc:
        print("[BACKUP RESTORE ERROR]", repr(exc))
        return jsonify({
            "ok": False,
            "message": f"Không khôi phục được backup: {exc}",
        }), 500


@app.route(
    "/setup",
    methods=["GET", "POST"]
)
def setup():

    config = load_setup_config()

    if request.method == "POST":
        section = clean_text(
            request.form.get(
                "section"
            )
        )

        try:
            if section == "club":
                title = clean_text(
                    request.form.get(
                        "club_title"
                    )
                )

                name = clean_text(
                    request.form.get(
                        "club_name"
                    )
                )

                table_count = int(
                    request.form.get(
                        "table_count"
                    )
                    or 12
                )

                table_count = max(
                    1,
                    min(
                        100,
                        table_count
                    )
                )

                if not title or not name:
                    raise ValueError(
                        "Tiêu đề và tên CLB không được để trống."
                    )

                # Không cho giảm số bàn nếu phía trên giới hạn mới
                # vẫn còn bàn đang hoạt động.
                active_rows = (
                    supabase
                    .table(
                        ACTIVITY_TABLE
                    )
                    .select(
                        "ban_so,"
                        "dang_hoat_dong"
                    )
                    .eq(
                        "dang_hoat_dong",
                        True
                    )
                    .execute()
                    .data
                    or []
                )

                blocked = [
                    int(
                        row.get(
                            "ban_so"
                        )
                        or 0
                    )
                    for row in active_rows
                    if int(
                        row.get(
                            "ban_so"
                        )
                        or 0
                    ) > table_count
                ]

                if blocked:
                    raise ValueError(
                        "Không thể giảm số lượng bàn vì "
                        + "các bàn "
                        + ", ".join(
                            str(item)
                            for item in sorted(
                                blocked
                            )
                        )
                        + " đang hoạt động."
                    )

                # Đồng bộ với Supabase TRƯỚC khi lưu số bàn mới vào JSON.
                # Nếu DB vẫn bị khóa giới hạn 12 bàn, việc Lưu sẽ báo lỗi
                # và cấu hình CLB cũ được giữ nguyên.
                ensure_billiards_tables(table_count)

                config["club"] = {
                    "title": title,
                    "name": name,
                    "table_count": table_count,
                }

            elif section == "discounts":
                config["discounts"] = {
                    "percent_enabled": request.form.get("percent_enabled") == "1",
                    "max_percent": max(1, min(100, int(request.form.get("max_percent") or 100)))
                }

            elif section == "pricing":
                labels = (
                    request.form.getlist(
                        "rate_label"
                    )
                )
                starts = (
                    request.form.getlist(
                        "rate_start"
                    )
                )
                ends = (
                    request.form.getlist(
                        "rate_end"
                    )
                )
                prices = (
                    request.form.getlist(
                        "rate_price"
                    )
                )

                pricing = []

                total_rates = max(
                    len(labels),
                    len(starts),
                    len(ends),
                    len(prices)
                )

                for index in range(
                    total_rates
                ):
                    label = clean_text(
                        labels[index]
                        if index < len(labels)
                        else ""
                    )

                    start = clean_text(
                        starts[index]
                        if index < len(starts)
                        else ""
                    )

                    end = clean_text(
                        ends[index]
                        if index < len(ends)
                        else ""
                    )

                    raw_price = clean_text(
                        prices[index]
                        if index < len(prices)
                        else ""
                    )

                    if (
                        not label
                        and not start
                        and not end
                        and not raw_price
                    ):
                        continue

                    start_min = parse_clock_minutes(
                        start
                    )
                    end_min = parse_clock_minutes(
                        end
                    )

                    if end_min <= start_min:
                        raise ValueError(
                            f"Khung giờ '{label}' không hợp lệ."
                        )

                    price = parse_money_value(
                        raw_price
                    )

                    if price <= 0:
                        raise ValueError(
                            f"Biểu phí '{label}' phải lớn hơn 0."
                        )

                    pricing.append({
                        "label": label,
                        "start": start,
                        "end": end,
                        "price_per_hour": price,
                    })

                if not pricing:
                    raise ValueError(
                        "Cần ít nhất 1 khung giờ tính phí."
                    )

                # Không cho các khung giờ chồng lên nhau.
                ordered = sorted(
                    pricing,
                    key=lambda item:
                        parse_clock_minutes(
                            item["start"]
                        )
                )

                previous_end = None

                for item in ordered:
                    current_start = (
                        parse_clock_minutes(
                            item["start"]
                        )
                    )
                    current_end = (
                        parse_clock_minutes(
                            item["end"]
                        )
                    )

                    if (
                        previous_end
                        is not None
                        and current_start
                        < previous_end
                    ):
                        raise ValueError(
                            "Các khung giờ biểu phí đang bị chồng lên nhau."
                        )

                    previous_end = (
                        current_end
                    )

                config["pricing"] = (
                    ordered
                )

                service_codes = (
                    request.form.getlist(
                        "service_code"
                    )
                )
                service_names = (
                    request.form.getlist(
                        "service_name"
                    )
                )
                service_prices = (
                    request.form.getlist(
                        "service_price"
                    )
                )

                services = []

                total_services = max(
                    len(service_codes),
                    len(service_names),
                    len(service_prices)
                )

                for index in range(
                    total_services
                ):
                    code = clean_text(
                        service_codes[index]
                        if index
                        < len(service_codes)
                        else ""
                    )

                    name = clean_text(
                        service_names[index]
                        if index
                        < len(service_names)
                        else ""
                    )

                    raw_price = clean_text(
                        service_prices[index]
                        if index
                        < len(service_prices)
                        else ""
                    )

                    if not name:
                        continue

                    price = parse_money_value(
                        raw_price
                    )

                    if price < 0:
                        price = 0

                    if not code:
                        code = (
                            "dv_"
                            + uuid.uuid4()
                            .hex[:8]
                        )

                    services.append({
                        "code": code,
                        "name": name,
                        "price": price,
                    })

                config["services"] = (
                    services
                )

            elif section == "member_ranks":
                try:
                    config["rank_accumulation_months"] = max(0, min(240, int(request.form.get("rank_accumulation_months") or 0)))
                except (TypeError, ValueError):
                    raise ValueError("Thời hạn tích lũy phải là số tháng hợp lệ.")
                old_ranks = list(config.get("member_ranks") or DEFAULT_MEMBER_RANKS)

                codes = request.form.getlist("rank_code")
                names = request.form.getlist("rank_name")
                icons = request.form.getlist("rank_icon")
                min_plays_values = request.form.getlist("rank_min_plays")
                min_spend_values = request.form.getlist("rank_min_spend")
                discount_values = request.form.getlist("rank_discount")
                inactive_values = request.form.getlist("rank_inactive_months")
                gift_values = request.form.getlist("rank_gift_text")
                birthday_values = request.form.getlist("rank_birthday_text")

                ranks = []

                for index, default_rank in enumerate(DEFAULT_MEMBER_RANKS):
                    code = default_rank["code"]
                    name = clean_text(names[index] if index < len(names) else "")
                    icon = clean_text(icons[index] if index < len(icons) else "") or default_rank["icon"]

                    if not name:
                        raise ValueError(f"Hạng số {index + 1} chưa có tên.")

                    try:
                        min_plays = max(0, int(min_plays_values[index] if index < len(min_plays_values) else 0))
                    except (TypeError, ValueError):
                        min_plays = 0

                    min_spend = parse_money_value(
                        min_spend_values[index] if index < len(min_spend_values) else "0"
                    )

                    try:
                        discount_percent = max(0, min(100, int(discount_values[index] if index < len(discount_values) else 0)))
                    except (TypeError, ValueError):
                        discount_percent = 0

                    try:
                        inactive_months = max(0, min(120, int(inactive_values[index] if index < len(inactive_values) else 0)))
                    except (TypeError, ValueError):
                        inactive_months = 0

                    ranks.append({
                        "code": code,
                        "name": name,
                        "icon": icon,
                        "min_plays": min_plays,
                        "min_spend": min_spend,
                        "discount_percent": discount_percent,
                        "inactive_months": inactive_months,
                        "gift_text": clean_text(gift_values[index] if index < len(gift_values) else ""),
                        "birthday_text": clean_text(birthday_values[index] if index < len(birthday_values) else ""),
                    })

                # Bảo đảm điều kiện tăng dần để tránh hạng sau dễ hơn hạng trước.
                for index in range(1, len(ranks)):
                    if ranks[index]["min_plays"] < ranks[index - 1]["min_plays"]:
                        raise ValueError("Số lượt chơi của các hạng phải tăng dần.")
                    if ranks[index]["min_spend"] < ranks[index - 1]["min_spend"]:
                        raise ValueError("Mức chi tiêu của các hạng phải tăng dần.")

                config["member_ranks"] = ranks

                # Nếu đổi tên hạng, đồng bộ tên đang lưu ở thành viên để tránh lệch dữ liệu.
                for index, new_rank in enumerate(ranks):
                    old_name = clean_text(
                        old_ranks[index].get("name") if index < len(old_ranks) else ""
                    )
                    new_name = clean_text(new_rank.get("name"))

                    if old_name and new_name and old_name != new_name:
                        try:
                            supabase.table(TABLE_NAME).update({
                                "hang_thanh_vien": new_name,
                                "updated_at": datetime.now().astimezone().isoformat(),
                            }).eq("hang_thanh_vien", old_name).execute()
                        except Exception as rank_sync_exc:
                            print("[RANK NAME SYNC WARNING]", repr(rank_sync_exc))

            elif section == "payment":
                bank_code = clean_text(
                    request.form.get(
                        "bank_code"
                    )
                )

                bank_name = ""

                for item in BANK_OPTIONS:
                    if (
                        item["code"]
                        == bank_code
                    ):
                        bank_name = (
                            item["name"]
                        )
                        break

                config["payment"] = {
                    "account_name":
                        clean_text(
                            request.form.get(
                                "account_name"
                            )
                        ),

                    "bank_code":
                        bank_code,

                    "bank_name":
                        bank_name,

                    "account_number":
                        clean_text(
                            request.form.get(
                                "account_number"
                            )
                        ),

                    "note_template":
                        clean_text(
                            request.form.get(
                                "note_template"
                            )
                        )
                        or "Khách lẻ hoặc mã Thành Viên",
                }

            else:
                raise ValueError(
                    "Nhóm cài đặt không hợp lệ."
                )

            config = save_setup_config(
                config
            )

            if section == "member_ranks":
                # Giảm mốc: thành viên đã đủ điều kiện được xét lại ngay;
                # tăng mốc: không hạ hạng đã đạt.
                try:
                    refresh_all_member_ranks()
                except Exception as rank_exc:
                    print("[RANK SETUP REFRESH WARNING]", repr(rank_exc))

            flash(
                "Đã lưu cài đặt.",
                "success"
            )

            return redirect(
                url_for(
                    "setup"
                )
                + "#"
                + section
            )

        except Exception as exc:
            flash(
                f"Không lưu được cài đặt: {exc}",
                "danger"
            )

    admin_rows = []
    can_manage_admin = current_admin_can(
        "quan_tri_admin"
    )

    if can_manage_admin:
        try:
            admin_rows = (
                supabase
                .table(ADMIN_TABLE)
                .select(
                    "id,"
                    "username,"
                    "email,"
                    "ho_va_ten,"
                    "active,"
                    "is_owner,"
                    "quyen_han,"
                    "last_login_at,"
                    "created_at"
                )
                .order(
                    "is_owner",
                    desc=True
                )
                .order(
                    "created_at"
                )
                .execute()
                .data
                or []
            )

            for admin_row in admin_rows:
                admin_row[
                    "quyen_han_chuan"
                ] = admin_row_permissions(
                    admin_row
                )

                last_login = parse_iso_datetime(
                    admin_row.get(
                        "last_login_at"
                    )
                )

                admin_row[
                    "last_login_hien_thi"
                ] = (
                    last_login
                    .astimezone(
                        VIETNAM_TZ
                    )
                    .strftime(
                        "%d/%m/%Y %H:%M"
                    )
                    if last_login
                    else "Chưa đăng nhập"
                )

        except Exception as exc:
            print(
                "[LOAD ADMIN INFO ERROR]",
                repr(exc)
            )
            flash(
                "Không tải được danh sách Admin.",
                "danger"
            )

    return render_template(
        "setup.html",
        setup_config=config,
        setup_storage=setup_storage_info(),
        bank_options=BANK_OPTIONS,
        payment_qr_url=build_payment_qr_url(
            config
        ),
        admin_rows=admin_rows,
        admin_permission_labels=
            ADMIN_PERMISSION_LABELS,
        can_manage_admin=
            can_manage_admin,
    )



# ==========================================================
# SETUP - PHÂN QUYỀN ADMIN
# ==========================================================
@app.post("/setup/admin/them")
def them_admin():
    current_admin = get_current_admin()

    if not current_admin:
        return redirect(
            url_for("admin_login")
        )

    username = clean_text(
        request.form.get("username")
    )

    email = clean_text(
        request.form.get("email")
    ) or None

    full_name = clean_text(
        request.form.get("ho_va_ten")
    )

    password = str(
        request.form.get("password")
        or ""
    )

    active = (
        request.form.get("active")
        == "1"
    )

    requested_owner = (
        request.form.get("is_owner")
        == "1"
    )

    permissions = (
        admin_permissions_from_form(
            request.form
        )
    )

    if not username:
        flash(
            "Username Admin không được để trống.",
            "danger"
        )
        return redirect(
            url_for("setup") + "#admin"
        )

    if not full_name:
        flash(
            "Họ tên Admin không được để trống.",
            "danger"
        )
        return redirect(
            url_for("setup") + "#admin"
        )

    if len(password) < 6:
        flash(
            "Mật khẩu Admin phải có ít nhất 6 ký tự.",
            "danger"
        )
        return redirect(
            url_for("setup") + "#admin"
        )

    # Chỉ Chủ hệ thống mới được tạo thêm Chủ.
    is_owner = (
        requested_owner
        and bool(
            current_admin.get("is_owner")
        )
    )

    if is_owner:
        permissions = dict(
            FULL_ADMIN_PERMISSIONS
        )

    now_iso = (
        datetime.now(
            VIETNAM_TZ
        ).isoformat()
    )

    try:
        (
            supabase
            .table(ADMIN_TABLE)
            .insert({
                "username":
                    username,

                "email":
                    email,

                "ho_va_ten":
                    full_name,

                "password_hash":
                    generate_password_hash(
                        password
                    ),

                "active":
                    active,

                "is_owner":
                    is_owner,

                "quyen_han":
                    permissions,

                "created_by":
                    current_admin.get("id"),

                "created_at":
                    now_iso,

                "updated_at":
                    now_iso,
            })
            .execute()
        )

        flash(
            "Đã tạo tài khoản Admin.",
            "success"
        )

    except Exception as exc:
        print(
            "[ADD ADMIN ERROR]",
            repr(exc)
        )

        message = str(exc)

        if (
            "duplicate" in message.lower()
            or "unique" in message.lower()
        ):
            message = (
                "Username hoặc Email đã tồn tại."
            )

        flash(
            f"Không tạo được Admin: {message}",
            "danger"
        )

    return redirect(
        url_for("setup") + "#admin"
    )


@app.post(
    "/setup/admin/<int:admin_id>/cap-nhat"
)
def cap_nhat_admin(admin_id):
    current_admin = get_current_admin()

    if not current_admin:
        return redirect(
            url_for("admin_login")
        )

    try:
        rows = (
            supabase
            .table(ADMIN_TABLE)
            .select(
                "id,"
                "username,"
                "email,"
                "ho_va_ten,"
                "active,"
                "is_owner,"
                "quyen_han"
            )
            .eq("id", admin_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not rows:
            raise ValueError(
                "Không tìm thấy tài khoản Admin."
            )

        target = rows[0]

        # Admin thường không được sửa tài khoản Chủ.
        if (
            bool(target.get("is_owner"))
            and not bool(
                current_admin.get(
                    "is_owner"
                )
            )
        ):
            raise PermissionError(
                "Chỉ Chủ hệ thống mới được "
                "chỉnh tài khoản Chủ."
            )

        username = clean_text(
            request.form.get("username")
        )

        email = clean_text(
            request.form.get("email")
        ) or None

        full_name = clean_text(
            request.form.get("ho_va_ten")
        )

        password = str(
            request.form.get("password")
            or ""
        )

        active = (
            request.form.get("active")
            == "1"
        )

        requested_owner = (
            request.form.get("is_owner")
            == "1"
        )

        if not username:
            raise ValueError(
                "Username không được để trống."
            )

        if not full_name:
            raise ValueError(
                "Họ tên Admin không được để trống."
            )

        # Không cho tự khóa chính mình.
        if (
            int(current_admin.get("id") or 0)
            == int(admin_id)
            and not active
        ):
            raise ValueError(
                "Không thể tự khóa tài khoản "
                "đang đăng nhập."
            )

        if bool(
            current_admin.get("is_owner")
        ):
            is_owner = requested_owner
        else:
            is_owner = bool(
                target.get("is_owner")
            )

        permissions = (
            dict(FULL_ADMIN_PERMISSIONS)
            if is_owner
            else admin_permissions_from_form(
                request.form
            )
        )

        payload = {
            "username":
                username,

            "email":
                email,

            "ho_va_ten":
                full_name,

            "active":
                active,

            "is_owner":
                is_owner,

            "quyen_han":
                permissions,

            "updated_at":
                datetime.now(
                    VIETNAM_TZ
                ).isoformat(),
        }

        # Để trống mật khẩu = giữ nguyên.
        if password:
            if len(password) < 6:
                raise ValueError(
                    "Mật khẩu mới phải có "
                    "ít nhất 6 ký tự."
                )

            payload[
                "password_hash"
            ] = generate_password_hash(
                password
            )

        (
            supabase
            .table(ADMIN_TABLE)
            .update(payload)
            .eq("id", admin_id)
            .execute()
        )

        # Nếu tự sửa chính mình, cập nhật session.
        if (
            int(current_admin.get("id") or 0)
            == int(admin_id)
        ):
            session[
                "admin_username"
            ] = username

            session[
                "admin_name"
            ] = full_name

            if hasattr(
                g,
                "_current_admin"
            ):
                delattr(
                    g,
                    "_current_admin"
                )

        flash(
            "Đã cập nhật quyền Admin.",
            "success"
        )

    except Exception as exc:
        print(
            "[UPDATE ADMIN ERROR]",
            repr(exc)
        )

        message = str(exc)

        if (
            "duplicate" in message.lower()
            or "unique" in message.lower()
        ):
            message = (
                "Username hoặc Email đã tồn tại."
            )

        flash(
            f"Không cập nhật được Admin: {message}",
            "danger"
        )

    return redirect(
        url_for("setup") + "#admin"
    )


@app.post(
    "/setup/admin/<int:admin_id>/xoa"
)
def xoa_admin(admin_id):
    current_admin = get_current_admin()

    if not current_admin:
        return redirect(
            url_for("admin_login")
        )

    if (
        int(current_admin.get("id") or 0)
        == int(admin_id)
    ):
        flash(
            "Không thể xóa tài khoản "
            "đang đăng nhập.",
            "danger"
        )

        return redirect(
            url_for("setup") + "#admin"
        )

    try:
        rows = (
            supabase
            .table(ADMIN_TABLE)
            .select(
                "id,"
                "is_owner,"
                "username"
            )
            .eq("id", admin_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not rows:
            raise ValueError(
                "Không tìm thấy Admin."
            )

        target = rows[0]

        if bool(target.get("is_owner")):
            if not bool(
                current_admin.get(
                    "is_owner"
                )
            ):
                raise PermissionError(
                    "Chỉ Chủ hệ thống mới được "
                    "xóa tài khoản Chủ."
                )

            owner_rows = (
                supabase
                .table(ADMIN_TABLE)
                .select(
                    "id"
                )
                .eq(
                    "is_owner",
                    True
                )
                .eq(
                    "active",
                    True
                )
                .execute()
                .data
                or []
            )

            if len(owner_rows) <= 1:
                raise ValueError(
                    "Phải giữ lại ít nhất "
                    "1 Admin Chủ đang hoạt động."
                )

        (
            supabase
            .table(ADMIN_TABLE)
            .delete()
            .eq("id", admin_id)
            .execute()
        )

        flash(
            "Đã xóa tài khoản Admin.",
            "success"
        )

    except Exception as exc:
        print(
            "[DELETE ADMIN ERROR]",
            repr(exc)
        )

        flash(
            f"Không xóa được Admin: {exc}",
            "danger"
        )

    return redirect(
        url_for("setup") + "#admin"
    )


def ensure_billiards_tables(table_count):
    """Đồng bộ các bàn 1..N trong Supabase; không xóa bàn/hóa đơn cũ.

    Có thể gọi lại an toàn. UNIQUE(ban_so) bảo vệ khi nhiều worker cùng tạo.
    Nếu DB lỗi phải báo lỗi thay vì dựng trạng thái bàn giả.
    """
    table_count = int(table_count)
    if not 1 <= table_count <= 100:
        raise ValueError("Số bàn phải từ 1 đến 100.")

    query = (supabase.table(ACTIVITY_TABLE)
             .select("ban_so")
             .order("ban_so")
             .execute())
    existing = {int(row["ban_so"]) for row in (query.data or [])}
    missing = sorted(set(range(1, table_count + 1)) - existing)

    for number in missing:
        try:
            supabase.table(ACTIVITY_TABLE).insert({
                "ban_so": number,
                "dang_hoat_dong": False,
                "thoi_gian_bat_dau": None,
                "dich_vu_them": "[]",
            }).execute()
        except Exception:
            # Worker khác có thể vừa tạo bàn. Chỉ bỏ qua nếu bàn đã tồn tại.
            recheck = (supabase.table(ACTIVITY_TABLE)
                       .select("ban_so").eq("ban_so", number)
                       .limit(1).execute().data or [])
            if not recheck:
                raise

    # Xác minh trạng thái DB thay vì coi lệnh ghi thành công một cách mặc định.
    final_rows = (supabase.table(ACTIVITY_TABLE)
                  .select("ban_so")
                  .order("ban_so")
                  .execute().data or [])
    final_numbers = {int(row["ban_so"]) for row in final_rows}
    still_missing = sorted(set(range(1, table_count + 1)) - final_numbers)
    if still_missing:
        raise RuntimeError("Supabase chưa tạo được bàn: " + ", ".join(map(str, still_missing)))
    return len(missing)


@app.get("/hoat-dong")
def hoat_dong():

    setup_config = (
        load_setup_config()
    )

    table_count = int(
        setup_config.get(
            "club",
            {}
        ).get(
            "table_count",
            12
        )
        or 12
    )

    # =============================
    # 1. DANH SÁCH THÀNH VIÊN
    # =============================
    try:
        members = (
            supabase.table(TABLE_NAME)
            .select(
                "id,"
                "ma_thanh_vien,"
                "ho_va_ten,"
                "gioi_tinh,"
                "ngay_sinh,"
                "dia_chi,"
                "so_dien_thoai,"
                "hang_thanh_vien,"
                "so_lan_da_choi"
            )
            .order("ho_va_ten")
            .execute().data
            or []
        )

        for member in members:
            member["ngay_sinh"] = (
                format_birthdate_display(
                    member.get(
                        "ngay_sinh"
                    )
                )
            )

    except Exception as exc:
        print(
            "[LOAD MEMBERS ERROR]",
            repr(exc)
        )
        members = []


    # =============================
    # 2. TRẠNG THÁI BÀN
    # =============================
    table_sync_error = ""
    ban_rows = []
    try:
        # Hỗ trợ cả CLB mới cài và dữ liệu cũ chưa được đồng bộ.
        ensure_billiards_tables(table_count)
        ban_rows_all = (
            supabase.table(ACTIVITY_TABLE)
            .select("id,ban_so,dang_hoat_dong,thoi_gian_bat_dau,dich_vu_them")
            .order("ban_so")
            .execute().data or []
        )
        # Giảm số bàn chỉ ẩn các bàn vượt mức; giữ dữ liệu nguyên vẹn.
        ban_rows = [row for row in ban_rows_all
                    if 1 <= int(row.get("ban_so") or 0) <= table_count]
        ban_rows.sort(key=lambda row: int(row.get("ban_so") or 0))
    except Exception as exc:
        print("[LOAD TABLE STATE ERROR]", repr(exc))
        table_sync_error = (
            "Không đồng bộ được danh sách bàn với Supabase. "
            "Kiểm tra SQL giới hạn số bàn và kết nối database. "
            + str(exc)
        )
        # Không tạo trạng thái bàn giả; chỉ báo lỗi để tránh mất phiên chơi.


    return render_template(
        "Hoatdong.html",
        members=members,
        ban_states=ban_rows,
        table_sync_error=table_sync_error,
        setup_config=setup_config,
    )


@app.get("/hoa-don")
def hoa_don():
    q = clean_text(
        request.args.get("q")
    )

    try:
        query = (
            supabase.table(INVOICE_TABLE)
            .select("*")
            .order(
                "created_at",
                desc=True
            )
        )

        if q:
            safe_q = (
                q.replace(",", " ")
                .strip()
            )

            filters = [
                f"ma_hoa_don.ilike.%{safe_q}%",
                f"ma_thanh_vien.ilike.%{safe_q}%",
                f"ho_va_ten.ilike.%{safe_q}%",
                f"so_dien_thoai.ilike.%{safe_q}%"
            ]

            # Nếu nhập số 1 -> 12
            # thì tìm luôn theo bàn
            try:
                ban_search = int(safe_q)

                table_count = int(
                    load_setup_config()
                    .get(
                        "club",
                        {}
                    )
                    .get(
                        "table_count",
                        12
                    )
                    or 12
                )

                if 1 <= ban_search <= table_count:
                    filters.append(
                        f"ban_so.eq.{ban_search}"
                    )

            except ValueError:
                pass


            query = query.or_(
                ",".join(filters)
            )

        rows = (
            query
            .execute()
            .data
            or []
        )


        for row in rows:

            row[
                "chi_tiet_tien_ban"
            ] = normalize_table_fee_detail(
                row.get(
                    "chi_tiet_tien_ban"
                )
            )

            # =========================
            # THỜI GIAN HIỂN THỊ
            # =========================
            created_dt = parse_iso_datetime(
                row.get("created_at")
            )

            if created_dt:
                row["created_at_hien_thi"] = (
                    created_dt
                    .astimezone()
                    .strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )
                )
            else:
                row["created_at_hien_thi"] = ""


            # =========================
            # THỜI LƯỢNG HIỂN THỊ
            # =========================
            duration = int(
                row.get(
                    "thoi_luong_choi_phut"
                )
                or 0
            )

            hours = duration // 60
            minutes = duration % 60

            if hours and minutes:
                row["thoi_luong_hien_thi"] = (
                    f"{hours} giờ "
                    f"{minutes} phút"
                )

            elif hours:
                row["thoi_luong_hien_thi"] = (
                    f"{hours} giờ"
                )

            else:
                row["thoi_luong_hien_thi"] = (
                    f"{minutes} phút"
                )

            # =========================
            # TIỀN HIỂN THỊ
            # =========================
            row["tong_tien_hien_thi"] = format_money_vn(
                row.get("tong_tien")
            )

            row["so_tien_giam_hien_thi"] = format_money_vn(
                row.get("so_tien_giam")
            )

            row["thanh_tien_hien_thi"] = format_money_vn(
                row.get("thanh_tien")
            )


            # =========================
            # GIẢM GIÁ HIỂN THỊ
            # =========================
            loai_giam = clean_text(
                row.get(
                    "loai_giam_gia"
                )
            )

            gia_tri = row.get(
                "gia_tri_giam"
            ) or 0

            so_tien_giam = int(
                row.get(
                    "so_tien_giam"
                )
                or 0
            )

            if (
                loai_giam == "percent"
                and float(gia_tri) > 0
            ):
                row[
                    "giam_gia_hien_thi"
                ] = (
                    f"{float(gia_tri):g}% "
                    f"(-{so_tien_giam:,} đ)"
                )

            elif so_tien_giam > 0:
                row[
                    "giam_gia_hien_thi"
                ] = (
                    f"-{so_tien_giam:,} đ"
                )

            else:
                row[
                    "giam_gia_hien_thi"
                ] = "0 đ"


    except Exception as exc:
        print(
            "[LOAD HOADON ERROR]",
            repr(exc)
        )

        rows = []

        flash(
            f"Không đọc được hóa đơn: {exc}",
            "danger"
        )


    return render_template(
        "Hoadon.html",
        rows=rows,
        q=q
    )


@app.post("/hoa-don/<int:invoice_id>/xac-nhan-thanh-toan")
def xac_nhan_thanh_toan_hoa_don(invoice_id):
    """Admin xác nhận đã nhận tiền; thao tác một chiều và an toàn khi gửi lặp."""
    token = request.form.get("csrf", "")
    expected = session.get("invoice_confirm_csrf", "")
    if not expected or not hmac.compare_digest(str(token), str(expected)):
        flash("Phiên xác nhận không hợp lệ. Vui lòng tải lại trang.", "danger")
        return redirect(url_for("hoa_don"))

    back_q = clean_text(request.form.get("return_q"))
    try:
        current = (supabase.table(INVOICE_TABLE)
                   .select("id,ma_thanh_vien,trang_thai")
                   .eq("id", invoice_id).limit(1).execute().data or [])
        if not current:
            flash("Không tìm thấy hóa đơn.", "danger")
        elif current[0].get("trang_thai") == "Đã thanh toán":
            flash("Hóa đơn này đã được xác nhận thanh toán trước đó.", "success")
        elif current[0].get("trang_thai") != "Chờ thanh toán":
            flash("Không thể xác nhận hóa đơn đang ở trạng thái này.", "danger")
        else:
            changed = (supabase.table(INVOICE_TABLE)
                       .update({"trang_thai": "Đã thanh toán",
                                "updated_at": datetime.now(VIETNAM_TZ).isoformat()})
                       .eq("id", invoice_id)
                       .eq("trang_thai", "Chờ thanh toán")
                       .execute().data or [])
            if changed:
                code = clean_text(changed[0].get("ma_thanh_vien") or current[0].get("ma_thanh_vien"))
                if code:
                    update_member_play_stats(code)
                flash("Đã xác nhận thanh toán hóa đơn thành công.", "success")
            else:
                flash("Hóa đơn đã được người khác xử lý. Vui lòng tải lại.", "danger")
    except Exception as exc:
        print("[INVOICE PAYMENT CONFIRM ERROR]", repr(exc))
        flash("Không thể xác nhận thanh toán. Vui lòng thử lại.", "danger")
    return redirect(url_for("hoa_don", q=back_q) if back_q else url_for("hoa_don"))


@app.post(
    "/hoa-don/<int:invoice_id>/xoa"
)
def xoa_hoa_don(invoice_id):

    try:
        invoice_rows = (
            supabase.table(
                INVOICE_TABLE
            )
            .select(
                "id,"
                "ma_thanh_vien"
            )
            .eq(
                "id",
                invoice_id
            )
            .limit(1)
            .execute()
            .data
            or []
        )

        if not invoice_rows:

            flash(
                "Không tìm thấy hóa đơn.",
                "danger"
            )

            return redirect(
                url_for("hoa_don")
            )


        member_code = clean_text(
            invoice_rows[0]
            .get("ma_thanh_vien")
        ).upper()


        # Xóa hóa đơn
        (
            supabase.table(
                INVOICE_TABLE
            )
            .delete()
            .eq(
                "id",
                invoice_id
            )
            .execute()
        )


        # Nếu hóa đơn thuộc thành viên
        # thì tính lại thống kê chơi
        if member_code:
            update_member_play_stats(
                member_code
            )


        flash(
            "Đã xóa hóa đơn.",
            "success"
        )


    except Exception as exc:

        print(
            "[DELETE HOADON ERROR]",
            repr(exc)
        )

        flash(
            f"Không xóa được hóa đơn: {exc}",
            "danger"
        )


    return redirect(
        url_for(
            "hoa_don",
            q=clean_text(
                request.form.get(
                    "return_q"
                )
            )
        )
    )

@app.post(
    "/api/ban/<int:ban_so>/bat-dau"
)
def api_bat_dau_ban(ban_so):

    try:

        setup_config = (
            load_setup_config()
        )


        table_count = int(
            setup_config
            .get(
                "club",
                {}
            )
            .get(
                "table_count",
                12
            )
            or 12
        )


        if not (
            1
            <= ban_so
            <= table_count
        ):

            return jsonify({
                "ok": False,

                "message":
                    f"Bàn {ban_so} "
                    "không hợp lệ."
            }), 400


        now_iso = (
            datetime.now(
                VIETNAM_TZ
            )
            .isoformat()
        )


        payload = {

            "dang_hoat_dong":
                True,

            "thoi_gian_bat_dau":
                now_iso,

            "dich_vu_them":
                "[]",

            "updated_at":
                now_iso,
        }


        result = (
            supabase
            .table(
                ACTIVITY_TABLE
            )
            .update(
                payload
            )
            .eq(
                "ban_so",
                ban_so
            )
            .eq(
                "dang_hoat_dong",
                False
            )
            .execute()
        )


        rows = (
            result.data
            or []
        )


        if not rows:
            check_rows = (
                supabase
                .table(
                    ACTIVITY_TABLE
                )
                .select(
                    "ban_so,"
                    "dang_hoat_dong"
                )
                .eq(
                    "ban_so",
                    ban_so
                )
                .limit(1)
                .execute()
                .data
                or []
            )


            if not check_rows:

                return jsonify({
                    "ok": False,

                    "message":
                        f"Không tìm thấy "
                        f"Bàn {ban_so}."
                }), 404


            if (
                check_rows[0]
                .get(
                    "dang_hoat_dong"
                )
                is True
            ):

                return jsonify({
                    "ok": False,

                    "message":
                        f"Bàn {ban_so} "
                        "đang hoạt động."
                }), 409


            return jsonify({
                "ok": False,

                "message":
                    "Không cập nhật được "
                    "trạng thái bàn."
            }), 500


        return jsonify({

            "ok":
                True,

            "ban_so":
                ban_so,

            "thoi_gian_bat_dau":
                now_iso,

            "dich_vu_them":
                "[]",
        })


    except Exception as exc:

        print(
            "[START TABLE ERROR]",
            type(exc).__name__,
            repr(exc)
        )


        return jsonify({

            "ok": False,

            "message":
                "Không bắt đầu được bàn: "
                + str(exc)

        }), 500


@app.post("/api/ban/<int:ban_so>/gio-hang")
def api_luu_gio_hang(ban_so):

    table_count = int(
        load_setup_config()
        .get(
            "club",
            {}
        )
        .get(
            "table_count",
            12
        )
        or 12
    )

    if not 1 <= ban_so <= table_count:
        return jsonify({
            "ok": False,
            "message": "Bàn không hợp lệ."
        }), 400

    data = request.get_json(silent=True) or {}

    services = data.get("dich_vu") or []

    try:

        # Kiểm tra bàn đang hoạt động
        rows = (
            supabase.table(ACTIVITY_TABLE)
            .select(
                "ban_so,"
                "dang_hoat_dong"
            )
            .eq("ban_so", ban_so)
            .limit(1)
            .execute().data
            or []
        )

        if not rows:
            return jsonify({
                "ok": False,
                "message": "Không tìm thấy bàn."
            }), 404

        if rows[0].get("dang_hoat_dong") is not True:

            return jsonify({
                "ok": False,
                "message":
                    "Bàn chưa hoạt động nên không thể thêm dịch vụ."
            }), 400


        # ==============================
        # LÀM SẠCH DANH SÁCH DỊCH VỤ
        # ==============================

        clean_services = []

        for item in services:

            ten = clean_text(
                (item or {}).get("ten")
            )

            if not ten:
                continue

            try:
                so_luong = max(
                    1,
                    int(
                        (item or {}).get("so_luong")
                        or 1
                    )
                )

            except (TypeError, ValueError):
                so_luong = 1

            clean_services.append({
                "ten": ten,
                "so_luong": so_luong
            })


        # ==============================
        # CHUYỂN THÀNH TEXT SUPABASE
        # ==============================

        if clean_services:

            dich_vu_them = (
                "["
                + ", ".join(
                    f"{item['ten']} x{item['so_luong']}"
                    for item in clean_services
                )
                + "]"
            )

        else:
            dich_vu_them = "[]"


        (
            supabase.table(ACTIVITY_TABLE)
            .update({
                "dich_vu_them": dich_vu_them,

                "updated_at":
                    datetime.now()
                    .astimezone()
                    .isoformat(),
            })
            .eq("ban_so", ban_so)
            .execute()
        )


        return jsonify({
            "ok": True,
            "dich_vu_them": dich_vu_them,
            "dich_vu": clean_services
        })

    except Exception as exc:

        print("[SAVE TABLE CART ERROR]", repr(exc))

        return jsonify({
            "ok": False,
            "message":
                f"Không lưu được dịch vụ: {exc}"
        }), 500



def parse_iso_datetime(value):
    raw = clean_text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


# ============================================================
# THÀNH VIÊN V2: Một nguồn hạng trong Supabase, không xếp hạng
# bằng trigger lượt chơi cũ. Khóa mã hạng ổn định dù đổi tên.
# ============================================================
RANK_CODES = tuple(rank["code"] for rank in DEFAULT_MEMBER_RANKS)


def rank_code_from_member(member, ranks=None):
    ranks = ranks or load_setup_config().get("member_ranks") or DEFAULT_MEMBER_RANKS
    code = clean_text(member.get("hang_code") if isinstance(member, dict) else "").upper()
    if code in RANK_CODES:
        return code
    value = member.get("hang_thanh_vien") if isinstance(member, dict) else member
    return RANK_CODES[get_member_rank_index(value, ranks)]


def rank_summary_for_member(code, *, now=None, include_expiry=False):
    """Chỉ tính hóa đơn đã thanh toán trong N tháng gần nhất khi xét NÂNG hạng.

    Hóa đơn cũ vẫn nguyên vẹn; thống kê tổng lịch sử ở nơi khác không bị sửa.
    Mốc N tháng trượt tính theo ngày kết thúc phiên chơi, giờ Việt Nam.
    """
    code = clean_text(code).upper()
    now = now or datetime.now(VIETNAM_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=VIETNAM_TZ)
    now = now.astimezone(VIETNAM_TZ)
    months = int(load_setup_config().get("rank_accumulation_months") or 0)
    cutoff = _add_calendar_months(now, -months) if months > 0 else None
    invoices = []
    offset = 0
    while code:
        query = (supabase.table(INVOICE_TABLE)
                 .select("id,thanh_tien,thoi_gian_ket_thuc")
                 .eq("ma_thanh_vien", code).eq("trang_thai", "Đã thanh toán")
                 .order("id").range(offset, offset + 999))
        batch = query.execute().data or []
        invoices.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    eligible = []
    all_dates = []
    for row in invoices:
        played_at = parse_iso_datetime(row.get("thoi_gian_ket_thuc"))
        if not played_at:
            continue
        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=VIETNAM_TZ)
        played_at = played_at.astimezone(VIETNAM_TZ)
        if played_at > now:
            continue
        all_dates.append(played_at)
        # Tại thời điểm chạm đúng hạn N tháng, lượt hết hiệu lực.
        if cutoff is not None and played_at <= cutoff:
            continue
        eligible.append({"played_at": played_at,
                         "amount": max(0, int(row.get("thanh_tien") or 0)),
                         "id": row.get("id")})
    result = {"plays": len(eligible),
              "spend": sum(i["amount"] for i in eligible),
              "last_play": max(all_dates, default=None),
              "months": months,
              "window_start": cutoff,
              "window_end": now}
    if include_expiry:
        expiring = []
        if months:
            notice_end = _add_calendar_months(now, 1)
            for item in eligible:
                expires_at = _add_calendar_months(item["played_at"], months)
                if now <= expires_at <= notice_end:
                    expiring.append({
                        "played_at": item["played_at"].strftime("%d/%m/%Y"),
                        "expires_at": expires_at.strftime("%d/%m/%Y"),
                        "amount": item["amount"],
                        "amount_display": f"{item['amount']:,}",
                        "expires_at_iso": expires_at.isoformat(),
                    })
            expiring.sort(key=lambda x: x["expires_at_iso"])
        result["expiring"] = expiring
        result["earliest_play"] = min((x["played_at"] for x in eligible), default=None)
        result["next_start_after_expiry"] = (min((x["played_at"] for x in eligible
            if not expiring or x["played_at"] > parse_iso_datetime(expiring[0]["expires_at_iso"])), default=None))
        result["remaining_plays"] = len(eligible)
        result["remaining_spend"] = result["spend"]
    return result


def rank_log_change(member, previous, new_code, reason, stats, changed_at):
    try:
        supabase.table("Thanhvien_hang_lichsu").insert({
            "thanh_vien_id": member["id"], "ma_thanh_vien": member.get("ma_thanh_vien"),
            "hang_cu": previous, "hang_moi": new_code, "ly_do": reason,
            "so_luot_luc_doi": stats["plays"], "tong_chi_luc_doi": stats["spend"],
            "thay_doi_at": changed_at.isoformat()
        }).execute()
    except Exception as exc:
        print("[RANK HISTORY WARNING]", repr(exc))


def evaluate_member_rank(member_code, *, stats=None, require_activity=False):
    """Chỉ NÂNG nếu đạt lượt HOẶC tiền. Không hạ do thay chỉ tiêu.
    Sau khi từng bị hạ vì nghỉ chơi, một hóa đơn mới chỉ phục hồi tối đa 1 bậc.
    """
    member_code = clean_text(member_code).upper()
    if not member_code:
        return None
    rows = (supabase.table(TABLE_NAME).select("*")
            .eq("ma_thanh_vien", member_code).limit(1).execute().data or [])
    if not rows:
        return None
    member = rows[0]
    ranks = load_setup_config().get("member_ranks") or DEFAULT_MEMBER_RANKS
    old_code = rank_code_from_member(member, ranks)
    old_idx = RANK_CODES.index(old_code)
    stats = stats or rank_summary_for_member(member_code)
    now = datetime.now(VIETNAM_TZ)
    downgraded_at = parse_iso_datetime(member.get("hang_giam_gan_nhat_at"))
    last_play = stats.get("last_play")
    if require_activity and not last_play:
        return old_code
    # Sau hạ hạng: không được nhảy lên lại từ tổng thống kê lịch sử nếu chưa chơi lại.
    if downgraded_at and (not last_play or last_play <= downgraded_at):
        return old_code
    target_idx = old_idx
    reason = ""
    for idx in range(len(ranks) - 1, old_idx, -1):
        min_plays = int(ranks[idx].get("min_plays") or 0)
        min_spend = int(ranks[idx].get("min_spend") or 0)
        plays_ok = min_plays > 0 and stats["plays"] >= min_plays
        spend_ok = min_spend > 0 and stats["spend"] >= min_spend
        if plays_ok or spend_ok:
            target_idx = idx
            reason = "LUOT_CHO" if plays_ok else "CHI_TIEU"
            break
    if target_idx <= old_idx:
        return old_code
    if downgraded_at:
        target_idx = min(target_idx, old_idx + 1)
    new_code = RANK_CODES[target_idx]
    payload = {"hang_code": new_code, "hang_thanh_vien": ranks[target_idx]["name"],
               "hang_thay_doi_at": now.isoformat(), "hang_nang_nguon": reason,
               "updated_at": now.isoformat()}
    if downgraded_at:
        # Mỗi hóa đơn mới chỉ khôi phục được một cấp hạng đã mất.
        payload["hang_giam_gan_nhat_at"] = (last_play or now).isoformat()
    result = (supabase.table(TABLE_NAME).update(payload)
              .eq("id", member["id"]).eq("hang_code", old_code).execute())
    if result.data:
        rank_log_change(member, old_code, new_code, reason, stats, now)
    return new_code


def _add_calendar_months(value, months):
    import calendar
    month_index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def downgrade_inactive_member(member, now=None):
    """Một lần kiểm tra có thể hạ nhiều cấp nếu bỏ chơi nhiều tháng,
    mỗi cấp dùng thời hạn riêng; dừng ở LOYAL.
    """
    now = now or datetime.now(VIETNAM_TZ)
    ranks = load_setup_config().get("member_ranks") or DEFAULT_MEMBER_RANKS
    old_code = rank_code_from_member(member, ranks)
    initial_idx = RANK_CODES.index(old_code)
    if initial_idx <= 1:
        return old_code
    last_play = parse_iso_datetime(member.get("thoi_gian_choi_gan_nhat"))
    rank_changed = parse_iso_datetime(member.get("hang_thay_doi_at"))
    registered = parse_iso_datetime(member.get("created_at"))
    anchor = max((dt for dt in (last_play, rank_changed, registered) if dt), default=now)
    index = initial_idx
    while index > 1:
        months = int(ranks[index].get("inactive_months") or 0)
        if months <= 0:
            break
        deadline = _add_calendar_months(anchor, months)
        if now < deadline:
            break
        anchor = deadline
        index -= 1
    if index == initial_idx:
        return old_code
    new_code = RANK_CODES[index]
    stats = {"plays": int(member.get("so_lan_da_choi") or 0), "spend": 0}
    changes = {"hang_code": new_code, "hang_thanh_vien": ranks[index]["name"],
               "hang_thay_doi_at": anchor.isoformat(),
               "hang_giam_gan_nhat_at": now.isoformat(), "hang_nang_nguon": None,
               "updated_at": now.isoformat()}
    result = (supabase.table(TABLE_NAME).update(changes)
              .eq("id", member["id"]).eq("hang_code", old_code).execute())
    if result.data:
        rank_log_change(member, old_code, new_code, "KHONG_HOAT_DONG", stats, now)
    return new_code


def refresh_all_member_ranks():
    """Đồng bộ nâng hạng theo Setup cho tất cả thành viên; không tự hạ hạng."""
    offset = 0
    changed = 0
    while True:
        batch = (supabase.table(TABLE_NAME).select("id,ma_thanh_vien,hang_code,hang_thanh_vien")
                 .order("id").range(offset, offset + 199).execute().data or [])
        if not batch:
            break
        for row in batch:
            try:
                before = rank_code_from_member(row)
                if evaluate_member_rank(row["ma_thanh_vien"]) != before:
                    changed += 1
            except Exception as exc:
                print("[RANK REEVALUATE ERROR]", row.get("id"), repr(exc))
        offset += len(batch)
        if len(batch) < 200:
            break
    return changed


def process_inactive_member_ranks():
    """Gọi từ worker hằng ngày trên VPS; không cần mở website."""
    offset = 0
    changed = 0
    while True:
        batch = (supabase.table(TABLE_NAME).select("*")
                 .order("id").range(offset, offset + 199).execute().data or [])
        if not batch:
            break
        for row in batch:
            try:
                before = rank_code_from_member(row)
                if downgrade_inactive_member(row) != before:
                    changed += 1
            except Exception as exc:
                print("[RANK DAILY ERROR]", row.get("id"), repr(exc))
        offset += len(batch)
        if len(batch) < 200:
            break
    return changed


def sync_rank_display(member, ranks=None):
    ranks = ranks or load_setup_config().get("member_ranks") or DEFAULT_MEMBER_RANKS
    code = rank_code_from_member(member, ranks)
    idx = RANK_CODES.index(code)
    member["hang_thanh_vien_index"] = idx
    member["hang_thanh_vien_hien_thi"] = ranks[idx]["name"]
    return idx

def generate_invoice_code():
    now = datetime.now().astimezone()
    suffix = uuid.uuid4().hex[:4].upper()
    return f"HD{now:%Y%m%d-%H%M%S}-{suffix}"


def update_member_play_stats(member_code):
    """
    Cập nhật thống kê chơi theo MÃ THÀNH VIÊN.
    Bảng Hoadon liên kết thành viên bằng ma_thanh_vien.
    """
    member_code = clean_text(
        member_code
    ).upper()

    if not member_code:
        return

    try:
        invoices = (
            supabase
            .table(INVOICE_TABLE)
            .select(
                "thoi_gian_ket_thuc,"
                "thoi_luong_choi_phut"
            )
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .eq(
                "trang_thai",
                "Đã thanh toán"
            )
            .execute()
            .data
            or []
        )

        durations = [
            int(
                item.get(
                    "thoi_luong_choi_phut"
                )
                or 0
            )
            for item in invoices
            if int(
                item.get(
                    "thoi_luong_choi_phut"
                )
                or 0
            ) > 0
        ]

        end_times = [
            clean_text(
                item.get(
                    "thoi_gian_ket_thuc"
                )
            )
            for item in invoices
            if clean_text(
                item.get(
                    "thoi_gian_ket_thuc"
                )
            )
        ]

        payload = {
            "so_lan_da_choi":
                len(invoices),

            "thoi_gian_choi_gan_nhat":
                max(end_times)
                if end_times
                else None,

            "thoi_gian_choi_lau_nhat":
                max(durations)
                if durations
                else None,

            "thoi_gian_choi_ngan_nhat":
                min(durations)
                if durations
                else None,

            "updated_at":
                datetime.now()
                .astimezone()
                .isoformat(),
        }

        (
            supabase
            .table(TABLE_NAME)
            .update(payload)
            .eq(
                "ma_thanh_vien",
                member_code
            )
            .execute()
        )

        # Đảm bảo đã lưu thống kê trước khi xét nâng hạng theo Setup.
        evaluate_member_rank(member_code)

    except Exception as exc:
        print(
            "[UPDATE MEMBER PLAY STATS ERROR]",
            type(exc).__name__
        )


@app.post("/api/hoa-don")
def api_tao_hoa_don():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:

        # ==========================================
        # 1. THÔNG TIN BÀN
        # ==========================================
        ban_so = int(
            data.get(
                "ban_so"
            )
            or 0
        )

        setup_config = (
            load_setup_config()
        )

        table_count = int(
            setup_config.get(
                "club",
                {}
            ).get(
                "table_count",
                12
            )
            or 12
        )

        if not 1 <= ban_so <= table_count:

            return jsonify({
                "ok": False,
                "message":
                    "Bàn số không hợp lệ."
            }), 400


        start_dt = parse_iso_datetime(
            data.get(
                "thoi_gian_bat_dau"
            )
        )

        end_dt = parse_iso_datetime(
            data.get(
                "thoi_gian_ket_thuc"
            )
        )

        if (
            not start_dt
            or not end_dt
            or end_dt <= start_dt
        ):

            return jsonify({
                "ok": False,
                "message":
                    "Thời gian chơi không hợp lệ."
            }), 400


        duration_seconds = (
            end_dt
            - start_dt
        ).total_seconds()

        duration_minutes = max(
            1,
            math.ceil(
                duration_seconds / 60
            )
        )

        table_fee_result = (
            calculate_table_fee_detail(
                start_dt,
                end_dt,
                setup_config.get(
                    "pricing",
                    []
                )
            )
        )

        duration_minutes = int(
            table_fee_result.get(
                "tong_phut",
                duration_minutes
            )
            or duration_minutes
        )

        tien_ban = int(
            table_fee_result.get(
                "tong_tien",
                0
            )
            or 0
        )

        chi_tiet_tien_ban = (
            table_fee_result.get(
                "chi_tiet",
                []
            )
            or []
        )


        # ==========================================
        # 2. DỊCH VỤ
        # ==========================================
        services_raw = (
            data.get("dich_vu")
            or []
        )

        services = []
        tien_dich_vu = 0

        for item in services_raw:

            ten = clean_text(
                (item or {}).get(
                    "ten"
                )
            )

            if not ten:
                continue

            try:

                so_luong = max(
                    1,
                    int(
                        (item or {}).get(
                            "so_luong"
                        )
                        or 1
                    )
                )

            except (
                TypeError,
                ValueError
            ):
                continue

            service_row = next(
                (
                    service
                    for service in (
                        setup_config.get(
                            "services",
                            []
                        )
                    )
                    if clean_text(
                        service.get(
                            "name"
                        )
                    ) == ten
                ),
                None
            )

            if not service_row:
                return jsonify({
                    "ok": False,
                    "message":
                        f"Dịch vụ '{ten}' không còn trong Setup."
                }), 400

            don_gia = max(
                0,
                int(
                    service_row.get(
                        "price"
                    )
                    or 0
                )
            )

            thanh_tien_dich_vu = (
                so_luong
                * don_gia
            )

            tien_dich_vu += (
                thanh_tien_dich_vu
            )

            services.append({
                "ten":
                    ten,

                "so_luong":
                    so_luong,

                "don_gia":
                    don_gia,

                "thanh_tien":
                    thanh_tien_dich_vu,
            })


        dich_vu_them = (
            "["
            + ", ".join(
                f"{item['ten']} x{item['so_luong']}"
                for item in services
            )
            + "]"
            if services
            else "[]"
        )


        tong_tien = (
            tien_ban
            + tien_dich_vu
        )


        # ==========================================
        # 3. THÀNH VIÊN
        # ==========================================
        member_code = clean_text(
            data.get(
                "ma_thanh_vien"
            )
        ).upper()

        if not member_code:
            member_code = None


        # ==========================================
        # 4. ƯU ĐÃI TỪ VOUCHER / QUÀ TẶNG
        #
        # Nếu có ưu đãi:
        # - Bắt buộc phải có thành viên
        # - Backend tự đọc giá trị thật từ Supabase
        # - Không tin giá trị tiền gửi từ frontend
        #
        # Nếu không có:
        # - Giữ nguyên giảm giá thủ công
        # ==========================================
        benefit_type = clean_text(
            data.get(
                "uu_dai_loai"
            )
        ).upper()

        benefit_code = clean_text(
            data.get(
                "uu_dai_ma"
            )
        ).upper()

        benefit_amount = 0
        benefit_kind = "NONE"
        benefit_name = ""
        benefit_table = None
        benefit_code_field = None
        benefit_status_after = None


        if (
            benefit_type
            or benefit_code
        ):

            if (
                benefit_type
                not in {
                    "VOUCHER",
                    "QUA"
                }
            ):

                return jsonify({
                    "ok": False,
                    "message":
                        "Loại Voucher/Quà giảm giá không hợp lệ."
                }), 400


            if not benefit_code:

                return jsonify({
                    "ok": False,
                    "message":
                        "Thiếu mã Voucher/Quà tặng."
                }), 400


            if not member_code:

                return jsonify({
                    "ok": False,
                    "message":
                        "Voucher/Quà tặng chỉ áp dụng cho thành viên."
                }), 400


            # ======================================
            # VOUCHER
            # ======================================
            if (
                benefit_type
                == "VOUCHER"
            ):

                benefit_rows = (
                    supabase
                    .table(
                        PROMOTION_VOUCHER_TABLE
                    )
                    .select(
                        "ma_voucher,"
                        "ma_thanh_vien,"
                        "ten_voucher,"
                        "gia_tri,"
                        "dieu_kien,"
                        "ngay_bat_dau,"
                        "ngay_het_han,"
                        "trang_thai,"
                        "kich_hoat_at"
                    )
                    .eq(
                        "ma_voucher",
                        benefit_code
                    )
                    .eq(
                        "ma_thanh_vien",
                        member_code
                    )
                    .limit(1)
                    .execute()
                    .data
                    or []
                )

                if not benefit_rows:

                    return jsonify({
                        "ok": False,
                        "message":
                            "Voucher không thuộc thành viên đã chọn."
                    }), 400


                benefit = (
                    benefit_rows[0]
                )


                benefit_status = clean_text(
                    benefit.get(
                        "trang_thai"
                    )
                )

                allowed_voucher_status = {
                    "",
                    "Chưa sử dụng",
                    "Chưa kích hoạt",
                }

                if benefit_status not in allowed_voucher_status:

                    return jsonify({
                        "ok": False,
                        "message":
                            f"Voucher hiện có trạng thái: {benefit_status}."
                    }), 400


                now = datetime.now(
                    VIETNAM_TZ
                )

                today_vn = now.date()

                start_voucher = (
                    parse_iso_datetime(
                        benefit.get(
                            "ngay_bat_dau"
                        )
                    )
                )

                end_voucher = (
                    parse_iso_datetime(
                        benefit.get(
                            "ngay_het_han"
                        )
                    )
                )

                if start_voucher:

                    start_date_vn = (
                        start_voucher
                        .astimezone(
                            VIETNAM_TZ
                        )
                        .date()
                    )

                    if today_vn < start_date_vn:

                        return jsonify({
                            "ok": False,
                            "message":
                                "Voucher chưa đến ngày sử dụng."
                        }), 400


                if end_voucher:

                    end_date_vn = (
                        end_voucher
                        .astimezone(
                            VIETNAM_TZ
                        )
                        .date()
                    )

                    if today_vn > end_date_vn:

                        return jsonify({
                            "ok": False,
                            "message":
                                "Voucher đã hết hạn."
                        }), 400


                condition_ok, unmet_conditions = (
                    check_voucher_conditions(
                        benefit.get("dieu_kien"),
                        tong_tien=tong_tien,
                        thoi_luong_phut=duration_minutes,
                        tien_dich_vu=tien_dich_vu
                    )
                )

                if not condition_ok:
                    return jsonify({
                        "ok": False,
                        "message":
                            "Voucher chưa đủ điều kiện sử dụng: "
                            + " ".join(
                                unmet_conditions
                            )
                    }), 400


                benefit_kind, benefit_amount = discount_benefit_value(
                    benefit.get("gia_tri"), allow_bare_amount=False
                )

                benefit_name = clean_text(
                    benefit.get(
                        "ten_voucher"
                    )
                )

                benefit_table = (
                    PROMOTION_VOUCHER_TABLE
                )

                benefit_code_field = (
                    "ma_voucher"
                )

                benefit_status_after = (
                    "Đã sử dụng"
                )


            # ======================================
            # QUÀ TẶNG CÓ GIÁ TRỊ TIỀN
            # ======================================
            else:

                benefit_rows = (
                    supabase
                    .table(
                        PROMOTION_REDEEM_TABLE
                    )
                    .select(
                        "ma_qua,"
                        "ma_thanh_vien,"
                        "ten_qua_tang,"
                        "trang_thai,"
                        "kich_hoat_at"
                    )
                    .eq(
                        "ma_qua",
                        benefit_code
                    )
                    .eq(
                        "ma_thanh_vien",
                        member_code
                    )
                    .limit(1)
                    .execute()
                    .data
                    or []
                )

                if not benefit_rows:

                    return jsonify({
                        "ok": False,
                        "message":
                            "Quà tặng không thuộc thành viên đã chọn."
                    }), 400


                benefit = (
                    benefit_rows[0]
                )


                benefit_status = clean_text(
                    benefit.get(
                        "trang_thai"
                    )
                )

                allowed_reward_status = {
                    "",
                    "Chưa nhận quà",
                    "Chưa sử dụng",
                }

                if benefit_status not in allowed_reward_status:

                    return jsonify({
                        "ok": False,
                        "message":
                            f"Quà tặng hiện có trạng thái: {benefit_status}."
                    }), 400


                benefit_name = clean_text(
                    benefit.get(
                        "ten_qua_tang"
                    )
                )

                benefit_kind, benefit_amount = discount_benefit_value(benefit_name)

                # Không cho gấu bông / nước / vật phẩm
                if benefit_amount <= 0:

                    return jsonify({
                        "ok": False,
                        "message":
                            "Quà tặng này không phải ưu đãi tiền và không thể áp dụng vào hóa đơn."
                    }), 400


                benefit_table = (
                    PROMOTION_REDEEM_TABLE
                )

                benefit_code_field = (
                    "ma_qua"
                )

                benefit_status_after = (
                    "Đã đổi quà"
                )


            if benefit_amount <= 0:

                return jsonify({
                    "ok": False,
                    "message":
                        "Voucher/Quà tặng không có giá trị giảm tiền hợp lệ."
                }), 400


        # ==========================================
        # 5. GIẢM GIÁ KẾT HỢP
        #
        # Voucher/Quà tiền và giảm thủ công là 2 lớp riêng:
        # 1. Trừ Voucher/Quà trước
        # 2. Giảm thủ công trên số tiền còn lại
        # ==========================================
        if benefit_kind == "PERCENT":
            rules = setup_config.get("discounts", {})
            if not rules.get("percent_enabled", True):
                return jsonify({"ok": False, "message": "CLB chưa bật ưu đãi theo phần trăm trong Setup."}), 400
            if benefit_amount > int(rules.get("max_percent", 100)):
                return jsonify({"ok": False, "message": "Voucher vượt mức giảm phần trăm cho phép trong Setup."}), 400

        benefit_discount = discount_benefit_amount(
            benefit_kind, benefit_amount, tong_tien
        )

        remaining_after_benefit = max(
            0,
            tong_tien - benefit_discount
        )

        loai_giam = clean_text(
            data.get(
                "loai_giam_gia"
            )
        ) or "amount"

        try:
            gia_tri_giam = max(
                0,
                float(
                    data.get(
                        "gia_tri_giam"
                    )
                    or 0
                )
            )

        except (
            TypeError,
            ValueError
        ):
            gia_tri_giam = 0

        manual_discount = 0

        if loai_giam == "percent":

            gia_tri_giam = min(
                gia_tri_giam,
                100
            )

            manual_discount = round(
                remaining_after_benefit
                * gia_tri_giam
                / 100
            )

        else:

            loai_giam = "amount"

            manual_discount = min(
                round(
                    gia_tri_giam
                ),
                remaining_after_benefit
            )

        so_tien_giam = min(
            tong_tien,
            benefit_discount
            + manual_discount
        )

        thanh_tien = max(
            0,
            tong_tien
            - so_tien_giam
        )


        # ==========================================
        # 6. LƯU HÓA ĐƠN
        # ==========================================
        now_iso = (
            datetime.now()
            .astimezone()
            .isoformat()
        )

        payload = {

            "ma_hoa_don":
                generate_invoice_code(),

            "ma_thanh_vien":
                member_code,

            "ho_va_ten":
                clean_text(
                    data.get(
                        "ho_va_ten"
                    )
                )
                or None,

            "so_dien_thoai":
                clean_text(
                    data.get(
                        "so_dien_thoai"
                    )
                )
                or None,

            "ban_so":
                ban_so,

            "thoi_gian_bat_dau":
                start_dt.isoformat(),

            "thoi_gian_ket_thuc":
                end_dt.isoformat(),

            "thoi_luong_choi_phut":
                duration_minutes,

            "tien_ban":
                tien_ban,

            "chi_tiet_tien_ban":
                chi_tiet_tien_ban,

            "dich_vu_them":
                dich_vu_them,

            "tien_dich_vu":
                tien_dich_vu,

            "tong_tien":
                tong_tien,

            "loai_giam_gia":
                loai_giam,

            "gia_tri_giam":
                gia_tri_giam,

            "so_tien_giam":
                so_tien_giam,

            "thanh_tien":
                thanh_tien,

            "phuong_thuc_thanh_toan":
                None,

            "trang_thai":
                "Chờ thanh toán",

            "ghi_chu":
                None,

            "created_at":
                now_iso,

            "updated_at":
                now_iso,
        }


        # Chỉ thêm các cột liên kết khi có sử dụng
        # Voucher/Quà tiền.
        if benefit_code:

            payload[
                "loai_uu_dai"
            ] = benefit_type

            payload[
                "ma_uu_dai"
            ] = benefit_code

            # Lưu số tiền THỰC TẾ đã giảm, giữ tương thích cột số của Hoadon.
            payload["gia_tri_uu_dai"] = benefit_discount


        result = (
            supabase
            .table(
                INVOICE_TABLE
            )
            .insert(
                payload
            )
            .execute()
        )

        saved = (
            result.data
            or [payload]
        )[0]


        # ==========================================
        # 7. NẾU HÓA ĐƠN ĐÃ LƯU THÀNH CÔNG
        #    -> KÍCH HOẠT VOUCHER / ĐỔI QUÀ
        # ==========================================
        if (
            benefit_code
            and benefit_table
            and benefit_code_field
        ):

            (
                supabase
                .table(
                    benefit_table
                )
                .update({
                    "trang_thai":
                        benefit_status_after,

                    "kich_hoat_at":
                        now_iso,

                    "updated_at":
                        now_iso,
                })
                .eq(
                    benefit_code_field,
                    benefit_code
                )
                .eq(
                    "ma_thanh_vien",
                    member_code
                )
                .execute()
            )


        # ==========================================
        # 8. RESET BÀN
        # ==========================================
        (
            supabase
            .table(
                ACTIVITY_TABLE
            )
            .update({
                "dang_hoat_dong":
                    False,

                "thoi_gian_bat_dau":
                    None,

                "dich_vu_them":
                    "[]",

                "updated_at":
                    datetime.now()
                    .astimezone()
                    .isoformat(),
            })
            .eq(
                "ban_so",
                ban_so
            )
            .execute()
        )


        update_member_play_stats(
            member_code
        )


        created_dt = (
            parse_iso_datetime(
                saved.get(
                    "created_at"
                )
            )
            or datetime.now()
            .astimezone()
        )


        response_invoice = dict(
            saved
        )

        response_invoice[
            "dich_vu_chi_tiet"
        ] = services

        response_invoice[
            "chi_tiet_tien_ban"
        ] = chi_tiet_tien_ban

        response_invoice[
            "ngay_lap_hien_thi"
        ] = (
            created_dt
            .astimezone()
            .strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        )

        if benefit_code:

            response_invoice[
                "uu_dai_hien_thi"
            ] = (
                f"{benefit_code}"
                f" - {discount_benefit_display(benefit_kind, benefit_amount)}"
            )


        return jsonify({
            "ok": True,
            "hoa_don":
                response_invoice
        })


    except Exception as exc:

        print(
            "[CREATE INVOICE ERROR]",
            repr(exc)
        )

        return jsonify({
            "ok": False,
            "message":
                f"Không lưu được hóa đơn: {exc}"
        }), 500



if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
