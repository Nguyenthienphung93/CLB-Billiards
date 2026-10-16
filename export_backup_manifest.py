import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from supabase_client import supabase


BACKUP_MANIFEST_TABLE = "backup_manifests"
BACKUP_MANIFEST_ITEM_TABLE = "backup_manifest_items"

# =========================================================
# CÁC BẢNG NGHIỆP VỤ CẦN BACKUP
# Không backup các bảng điều khiển backup để tránh tự lồng dữ liệu.
# =========================================================
BACKUP_TABLES = [
    "student",
    "hocphi",
    "ketqua",
    "hoatdong",
    "activity_events",
    "notifications",
    "notification_reads",
    "payment_settings",
    "app_settings",
    "exam_infos",
    "coaches",
]

PRIMARY_KEY_MAP = {
    "student": ["license"],
    "hocphi": ["id"],
    "ketqua": ["id"],
    "hoatdong": ["id"],
    "activity_events": ["id"],
    "notifications": ["id"],
    "notification_reads": ["id"],
    "payment_settings": ["id"],
    "app_settings": ["key"],
    "exam_infos": ["id"],
    "coaches": ["id"],
}

# =========================================================
# SUPABASE STORAGE
# =========================================================
BACKUP_STORAGE_BUCKETS = [
    "student-photos",
    "system-assets",
]

# =========================================================
# BACKUP MÃ NGUỒN
# Không sao lưu file bí mật và thư mục không cần thiết.
# =========================================================
APP_EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".env",
    "node_modules",

    "backup_manifest_test",
    "backup_manifest_supabase_test",

    "storage_exports",
    "database_exports",
    "app_exports",
    "logs",
    "log",
    "tmp",
    "temp",
}

APP_EXCLUDED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "rclone.conf",
}

APP_EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".swp",
    ".sqlite",
    ".sqlite3",
    ".db",
}

# Không sao lưu các file backup cũ vào trong backup mới.
APP_EXCLUDED_ARCHIVE_SUFFIXES = {
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
    ".7z",
    ".rar",
}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def sha256_file(file_path):
    digest = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def write_json_file(file_path, data):
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
            default=json_default,
        )


def reset_output_subdirectory(directory):
    """
    Xóa thư mục xuất cũ để tránh file cũ bị lẫn vào backup mới.
    """
    directory = Path(directory)

    if directory.exists():
        shutil.rmtree(directory)

    directory.mkdir(parents=True, exist_ok=True)


# =========================================================
# DATABASE
# =========================================================
def fetch_all_rows(table_name, page_size=1000):
    all_rows = []
    start = 0

    while True:
        end = start + page_size - 1

        response = (
            supabase.table(table_name)
            .select("*")
            .range(start, end)
            .execute()
        )

        rows = response.data or []
        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        start += page_size

    return all_rows


def get_columns_info(rows):
    column_names = set()

    for row in rows:
        if isinstance(row, dict):
            column_names.update(row.keys())

    return [
        {"name": column_name}
        for column_name in sorted(column_names)
    ]


def export_database_tables(output_directory):
    exports_directory = (
        Path(output_directory)
        / "database_exports"
    )

    reset_output_subdirectory(exports_directory)

    manifest_items = []
    total_rows = 0

    for table_name in BACKUP_TABLES:
        print(f"[TABLE] Đang xuất: {table_name}")

        file_path = exports_directory / f"{table_name}.json"

        try:
            rows = fetch_all_rows(table_name)
            write_json_file(file_path, rows)

            file_size = file_path.stat().st_size
            checksum = sha256_file(file_path)
            record_count = len(rows)

            total_rows += record_count

            manifest_items.append({
                "item_type": "table",
                "item_name": table_name,
                "source_name": table_name,
                "backup_path": (
                    f"database_exports/{table_name}.json"
                ),
                "record_count": record_count,
                "file_count": 1,
                "total_size": file_size,
                "primary_key_columns": PRIMARY_KEY_MAP.get(
                    table_name,
                    ["id"],
                ),
                "columns_info": get_columns_info(rows),
                "checksum_sha256": checksum,
                "item_status": "ready",
                "error_message": None,
                "extra_info": {},
            })

            print(
                f"[OK] {table_name}: "
                f"{record_count} dòng, "
                f"{file_size} byte"
            )

        except Exception as error:
            error_text = str(error)

            manifest_items.append({
                "item_type": "table",
                "item_name": table_name,
                "source_name": table_name,
                "backup_path": (
                    f"database_exports/{table_name}.json"
                ),
                "record_count": 0,
                "file_count": 0,
                "total_size": 0,
                "primary_key_columns": PRIMARY_KEY_MAP.get(
                    table_name,
                    ["id"],
                ),
                "columns_info": [],
                "checksum_sha256": None,
                "item_status": "error",
                "error_message": error_text,
                "extra_info": {},
            })

            print(
                f"[ERROR] Không xuất được "
                f"{table_name}: {error_text}"
            )

    return manifest_items, total_rows


# =========================================================
# SUPABASE STORAGE
# =========================================================
def normalize_storage_path(path_value):
    return str(path_value or "").replace("\\", "/").strip("/")


def storage_item_is_folder(item):
    """
    Supabase Storage trả folder dưới dạng item không có id
    hoặc không có metadata kích thước.
    """
    if not isinstance(item, dict):
        return False

    if item.get("id"):
        return False

    metadata = item.get("metadata")

    if isinstance(metadata, dict):
        size_value = metadata.get("size")

        if size_value not in [None, ""]:
            return False

    return True


def list_storage_directory(bucket_name, folder_path=""):
    """
    Đọc một thư mục trong bucket với phân trang.
    """
    folder_path = normalize_storage_path(folder_path)
    all_items = []
    offset = 0
    page_size = 1000

    while True:
        options = {
            "limit": page_size,
            "offset": offset,
            "sortBy": {
                "column": "name",
                "order": "asc",
            },
        }

        batch = (
            supabase.storage
            .from_(bucket_name)
            .list(folder_path, options)
            or []
        )

        all_items.extend(batch)

        if len(batch) < page_size:
            break

        offset += page_size

    return all_items


def list_all_storage_files(bucket_name):
    """
    Liệt kê đệ quy toàn bộ file trong bucket.
    """
    files = []
    folders_to_scan = [""]

    while folders_to_scan:
        current_folder = folders_to_scan.pop(0)

        for item in list_storage_directory(
            bucket_name,
            current_folder,
        ):
            item_name = str(
                item.get("name") or ""
            ).strip()

            if not item_name:
                continue

            object_path = normalize_storage_path(
                f"{current_folder}/{item_name}"
            )

            if storage_item_is_folder(item):
                folders_to_scan.append(object_path)
                continue

            metadata = item.get("metadata") or {}

            try:
                file_size = int(
                    metadata.get("size")
                    or item.get("size")
                    or 0
                )
            except Exception:
                file_size = 0

            files.append({
                "path": object_path,
                "name": item_name,
                "size": file_size,
                "mime_type": (
                    metadata.get("mimetype")
                    or metadata.get("contentType")
                    or ""
                ),
                "updated_at": (
                    item.get("updated_at")
                    or item.get("created_at")
                    or ""
                ),
            })

    files.sort(
        key=lambda item: item["path"].lower()
    )

    return files


def download_storage_object(bucket_name, object_path):
    data = (
        supabase.storage
        .from_(bucket_name)
        .download(object_path)
    )

    if isinstance(data, bytes):
        return data

    if isinstance(data, bytearray):
        return bytes(data)

    if hasattr(data, "read"):
        return data.read()

    raise RuntimeError(
        f"Supabase không trả dữ liệu bytes cho {object_path}."
    )


def export_storage_bucket(
    output_directory,
    bucket_name,
):
    bucket_directory = (
        Path(output_directory)
        / "storage_exports"
        / bucket_name
    )

    reset_output_subdirectory(bucket_directory)

    file_entries = []
    total_size = 0

    print(f"[BUCKET] Đang đọc: {bucket_name}")

    storage_files = list_all_storage_files(
        bucket_name
    )

    for index, storage_file in enumerate(
        storage_files,
        start=1,
    ):
        object_path = storage_file["path"]
        local_path = bucket_directory / Path(
            object_path
        )

        local_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        print(
            f"[STORAGE {index}/{len(storage_files)}] "
            f"{bucket_name}/{object_path}"
        )

        file_bytes = download_storage_object(
            bucket_name,
            object_path,
        )

        local_path.write_bytes(file_bytes)

        actual_size = local_path.stat().st_size
        checksum = sha256_file(local_path)
        total_size += actual_size

        file_entries.append({
            "path": object_path,
            "backup_path": (
                f"storage_exports/"
                f"{bucket_name}/"
                f"{object_path}"
            ),
            "size": actual_size,
            "sha256": checksum,
            "mime_type": storage_file.get(
                "mime_type"
            ) or "",
            "updated_at": storage_file.get(
                "updated_at"
            ) or "",
        })

    bucket_index_file = (
        Path(output_directory)
        / "storage_exports"
        / bucket_name
        / "_bucket_manifest.json"
    )

    write_json_file(
        bucket_index_file,
        {
            "bucket_name": bucket_name,
            "file_count": len(file_entries),
            "total_size": total_size,
            "files": file_entries,
        },
    )

    print(
        f"[OK] Bucket {bucket_name}: "
        f"{len(file_entries)} file, "
        f"{total_size} byte"
    )

    return {
        "item_type": "bucket",
        "item_name": bucket_name,
        "source_name": bucket_name,
        "backup_path": (
            f"storage_exports/{bucket_name}"
        ),
        "record_count": 0,
        "file_count": len(file_entries),
        "total_size": total_size,
        "primary_key_columns": [],
        "columns_info": [],
        "checksum_sha256": sha256_file(
            bucket_index_file
        ),
        "item_status": "ready",
        "error_message": None,
        "extra_info": {
            "index_file": (
                f"storage_exports/"
                f"{bucket_name}/"
                f"_bucket_manifest.json"
            ),
            "files": file_entries,
        },
    }


def export_storage_buckets(output_directory):
    storage_root = (
        Path(output_directory)
        / "storage_exports"
    )

    reset_output_subdirectory(storage_root)

    manifest_items = []
    total_file_count = 0
    total_storage_size = 0

    for bucket_name in BACKUP_STORAGE_BUCKETS:
        try:
            item = export_storage_bucket(
                output_directory,
                bucket_name,
            )

        except Exception as error:
            error_text = str(error)

            print(
                f"[ERROR] Không xuất được bucket "
                f"{bucket_name}: {error_text}"
            )

            item = {
                "item_type": "bucket",
                "item_name": bucket_name,
                "source_name": bucket_name,
                "backup_path": (
                    f"storage_exports/{bucket_name}"
                ),
                "record_count": 0,
                "file_count": 0,
                "total_size": 0,
                "primary_key_columns": [],
                "columns_info": [],
                "checksum_sha256": None,
                "item_status": "error",
                "error_message": error_text,
                "extra_info": {},
            }

        manifest_items.append(item)

        if item.get("item_status") == "ready":
            total_file_count += int(
                item.get("file_count") or 0
            )
            total_storage_size += int(
                item.get("total_size") or 0
            )

    return (
        manifest_items,
        total_file_count,
        total_storage_size,
    )


# =========================================================
# MÃ NGUỒN ỨNG DỤNG
# =========================================================
def file_has_archive_suffix(file_name):
    file_name = str(file_name or "").lower()

    return any(
        file_name.endswith(suffix)
        for suffix in APP_EXCLUDED_ARCHIVE_SUFFIXES
    )


def should_exclude_app_path(
    source_root,
    source_path,
    output_directory,
):
    source_path = Path(source_path)
    source_root = Path(source_root).resolve()
    output_directory = Path(output_directory).resolve()

    try:
        resolved_path = source_path.resolve()
    except Exception:
        resolved_path = source_path

    # Không sao lưu chính thư mục output vào bên trong nó.
    try:
        resolved_path.relative_to(output_directory)
        return True
    except ValueError:
        pass

    relative_parts = []

    try:
        relative_parts = list(
            resolved_path.relative_to(
                source_root
            ).parts
        )
    except Exception:
        relative_parts = list(source_path.parts)

    if any(
        part in APP_EXCLUDED_DIR_NAMES
        for part in relative_parts[:-1]
    ):
        return True

    if source_path.is_dir():
        return source_path.name in APP_EXCLUDED_DIR_NAMES

    file_name = source_path.name

    if file_name in APP_EXCLUDED_FILE_NAMES:
        return True

    if source_path.suffix.lower() in APP_EXCLUDED_SUFFIXES:
        return True

    if file_has_archive_suffix(file_name):
        return True

    return False


def export_app_source(
    output_directory,
    app_folder,
):
    source_root = Path(app_folder).resolve()

    if not source_root.exists():
        raise FileNotFoundError(
            f"Không tìm thấy thư mục mã nguồn: {source_root}"
        )

    if not source_root.is_dir():
        raise ValueError(
            f"Đường dẫn mã nguồn không phải thư mục: {source_root}"
        )

    destination_root = (
        Path(output_directory)
        / "app_exports"
        / "application"
    )

    reset_output_subdirectory(
        destination_root
    )

    file_entries = []
    total_size = 0

    for source_path in sorted(
        source_root.rglob("*")
    ):
        if should_exclude_app_path(
            source_root,
            source_path,
            output_directory,
        ):
            continue

        if not source_path.is_file():
            continue

        relative_path = source_path.relative_to(
            source_root
        )

        destination_path = (
            destination_root
            / relative_path
        )

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            source_path,
            destination_path,
        )

        file_size = destination_path.stat().st_size
        checksum = sha256_file(
            destination_path
        )
        total_size += file_size

        relative_text = relative_path.as_posix()

        file_entries.append({
            "path": relative_text,
            "backup_path": (
                f"app_exports/application/"
                f"{relative_text}"
            ),
            "size": file_size,
            "sha256": checksum,
        })

    app_index_file = (
        Path(output_directory)
        / "app_exports"
        / "application"
        / "_app_manifest.json"
    )

    write_json_file(
        app_index_file,
        {
            "source_root": str(source_root),
            "file_count": len(file_entries),
            "total_size": total_size,
            "excluded_secret_files": sorted(
                APP_EXCLUDED_FILE_NAMES
            ),
            "files": file_entries,
        },
    )

    print(
        f"[OK] Mã nguồn: "
        f"{len(file_entries)} file, "
        f"{total_size} byte"
    )

    return {
        "item_type": "app",
        "item_name": "application",
        "source_name": source_root.name,
        "backup_path": "app_exports/application",
        "record_count": 0,
        "file_count": len(file_entries),
        "total_size": total_size,
        "primary_key_columns": [],
        "columns_info": [],
        "checksum_sha256": sha256_file(
            app_index_file
        ),
        "item_status": "ready",
        "error_message": None,
        "extra_info": {
            "source_root": str(source_root),
            "index_file": (
                "app_exports/application/"
                "_app_manifest.json"
            ),
            "secret_files_excluded": sorted(
                APP_EXCLUDED_FILE_NAMES
            ),
        },
    }


# =========================================================
# MANIFEST SUPABASE
# =========================================================
def delete_old_manifest_items(manifest_id):
    (
        supabase.table(BACKUP_MANIFEST_ITEM_TABLE)
        .delete()
        .eq("manifest_id", manifest_id)
        .execute()
    )


def save_manifest_to_supabase(manifest, items):
    backup_name = manifest["backup_name"]

    existing = (
        supabase.table(BACKUP_MANIFEST_TABLE)
        .select("id")
        .eq("backup_name", backup_name)
        .limit(1)
        .execute()
        .data
        or []
    )

    manifest_payload = {
        "backup_name": backup_name,
        "backup_type": manifest.get(
            "backup_type",
            "full",
        ),
        "drive_remote": manifest.get(
            "drive_remote"
        ),
        "drive_path": manifest.get(
            "drive_path"
        ),
        "file_size": int(
            manifest.get("file_size") or 0
        ),
        "file_sha256": manifest.get(
            "file_sha256"
        ),
        "app_folder": manifest.get(
            "app_folder"
        ),
        "app_version": manifest.get(
            "app_version"
        ),
        "database_format": manifest.get(
            "database_format",
            "json_exports",
        ),
        "database_file": manifest.get(
            "database_file",
            "database_exports",
        ),
        "table_count": int(
            manifest.get("table_count") or 0
        ),
        "total_row_count": int(
            manifest.get("total_row_count") or 0
        ),
        "bucket_count": int(
            manifest.get("bucket_count") or 0
        ),
        "storage_file_count": int(
            manifest.get("storage_file_count")
            or 0
        ),
        "storage_size": int(
            manifest.get("storage_size") or 0
        ),
        "backup_status": manifest.get(
            "backup_status",
            "building",
        ),
        "verification_status": (
            manifest.get(
                "verification_status",
                "not_checked",
            )
        ),
        "error_message": manifest.get(
            "error_message"
        ),
        "notes": manifest.get("notes"),
        "created_by": manifest.get(
            "created_by",
            "backup-script",
        ),
        "completed_at": manifest.get(
            "completed_at"
        ),
        "updated_at": utc_now_iso(),
    }

    if existing:
        manifest_id = existing[0]["id"]

        (
            supabase.table(
                BACKUP_MANIFEST_TABLE
            )
            .update(manifest_payload)
            .eq("id", manifest_id)
            .execute()
        )

        delete_old_manifest_items(
            manifest_id
        )

    else:
        result = (
            supabase.table(
                BACKUP_MANIFEST_TABLE
            )
            .insert(manifest_payload)
            .execute()
        )

        inserted = result.data or []

        if not inserted:
            raise RuntimeError(
                "Supabase không trả về ID manifest vừa tạo."
            )

        manifest_id = inserted[0]["id"]

    item_payloads = []

    for item in items:
        item_payloads.append({
            "manifest_id": manifest_id,
            "item_type": item["item_type"],
            "item_name": item["item_name"],
            "source_name": item.get(
                "source_name"
            ),
            "backup_path": item.get(
                "backup_path"
            ),
            "record_count": int(
                item.get("record_count") or 0
            ),
            "file_count": int(
                item.get("file_count") or 0
            ),
            "total_size": int(
                item.get("total_size") or 0
            ),
            "primary_key_columns": (
                item.get(
                    "primary_key_columns",
                    [],
                )
            ),
            "columns_info": item.get(
                "columns_info",
                [],
            ),
            "extra_info": item.get(
                "extra_info",
                {},
            ),
            "checksum_sha256": item.get(
                "checksum_sha256"
            ),
            "item_status": item.get(
                "item_status",
                "ready",
            ),
            "error_message": item.get(
                "error_message"
            ),
        })

    if item_payloads:
        (
            supabase.table(
                BACKUP_MANIFEST_ITEM_TABLE
            )
            .insert(item_payloads)
            .execute()
        )

    return manifest_id


# =========================================================
# MAIN
# =========================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Xuất Database, Supabase Storage "
            "và mã nguồn ứng dụng thành bộ backup manifest."
        )
    )

    parser.add_argument(
        "--output",
        required=True,
        help=(
            "Thư mục tạo database_exports, "
            "storage_exports, app_exports và manifest.json"
        ),
    )

    parser.add_argument(
        "--backup-name",
        required=True,
        help="Tên file backup tar.gz",
    )

    parser.add_argument(
        "--drive-remote",
        default=(
            "gdrive_new:"
            "VPS-Backup-PhungTKD"
        ),
    )

    parser.add_argument(
        "--drive-path",
        default="",
    )

    parser.add_argument(
        "--app-folder",
        default=".",
        help=(
            "Thư mục mã nguồn cần sao lưu. "
            "Local có thể dùng dấu chấm. "
            "VPS dùng /var/www/phungtkdsystem"
        ),
    )

    parser.add_argument(
        "--app-version",
        default="",
    )

    parser.add_argument(
        "--skip-storage",
        action="store_true",
        help="Không xuất Supabase Storage",
    )

    parser.add_argument(
        "--skip-app",
        action="store_true",
        help="Không xuất mã nguồn ứng dụng",
    )

    parser.add_argument(
        "--save-supabase",
        action="store_true",
        help=(
            "Ghi manifest và chi tiết "
            "lên Supabase"
        ),
    )

    args = parser.parse_args()

    output_directory = Path(
        args.output
    ).resolve()

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    created_at = utc_now_iso()
    completed_at = None

    # DATABASE
    table_items, total_rows = (
        export_database_tables(
            output_directory
        )
    )

    # STORAGE
    storage_items = []
    storage_file_count = 0
    storage_size = 0

    if not args.skip_storage:
        (
            storage_items,
            storage_file_count,
            storage_size,
        ) = export_storage_buckets(
            output_directory
        )

    # APP SOURCE
    app_items = []

    if not args.skip_app:
        try:
            app_item = export_app_source(
                output_directory,
                args.app_folder,
            )
            app_items.append(app_item)

        except Exception as error:
            error_text = str(error)

            print(
                "[ERROR] Không backup được mã nguồn: "
                f"{error_text}"
            )

            app_items.append({
                "item_type": "app",
                "item_name": "application",
                "source_name": str(
                    args.app_folder
                ),
                "backup_path": (
                    "app_exports/application"
                ),
                "record_count": 0,
                "file_count": 0,
                "total_size": 0,
                "primary_key_columns": [],
                "columns_info": [],
                "checksum_sha256": None,
                "item_status": "error",
                "error_message": error_text,
                "extra_info": {},
            })

    manifest_items = (
        table_items
        + storage_items
        + app_items
    )

    successful_tables = [
        item
        for item in table_items
        if item.get("item_status") == "ready"
    ]

    successful_buckets = [
        item
        for item in storage_items
        if item.get("item_status") == "ready"
    ]

    failed_items = [
        item
        for item in manifest_items
        if item.get("item_status") == "error"
    ]

    completed_at = utc_now_iso()

    manifest = {
        "manifest_version": 2,
        "backup_name": args.backup_name,
        "backup_type": "full",
        "created_at": created_at,
        "completed_at": completed_at,

        "drive_remote": args.drive_remote,
        "drive_path": (
            args.drive_path
            or (
                f"{args.drive_remote}/"
                f"{args.backup_name}"
            )
        ),

        # Hai giá trị này sẽ được script nén ngoài
        # cập nhật lại sau khi tạo tar.gz.
        "file_size": 0,
        "file_sha256": None,

        "app_folder": str(
            Path(args.app_folder).resolve()
        ),
        "app_version": (
            args.app_version or None
        ),

        "database_format": "json_exports",
        "database_file": (
            "database_exports"
        ),

        "table_count": len(
            successful_tables
        ),
        "total_row_count": total_rows,

        "bucket_count": len(
            successful_buckets
        ),
        "storage_file_count": (
            storage_file_count
        ),
        "storage_size": storage_size,

        "app_file_count": sum(
            int(item.get("file_count") or 0)
            for item in app_items
            if item.get("item_status") == "ready"
        ),
        "app_size": sum(
            int(item.get("total_size") or 0)
            for item in app_items
            if item.get("item_status") == "ready"
        ),

        "backup_status": (
            "ready"
            if not failed_items
            else "ready_with_errors"
        ),

        "verification_status": (
            "not_checked"
        ),

        "error_message": (
            None
            if not failed_items
            else (
                f"Có {len(failed_items)} "
                f"hạng mục không xuất được."
            )
        ),

        "notes": (
            "Không bao gồm file .env, "
            "rclone.conf, database local, "
            "venv, .git và file log."
        ),

        "created_by": "backup-script",

        "tables": table_items,
        "storage": storage_items,
        "application": app_items,
    }

    manifest_file = (
        output_directory
        / "manifest.json"
    )

    write_json_file(
        manifest_file,
        manifest,
    )

    print("")
    print("========================================")
    print(f"Manifest: {manifest_file}")
    print(
        f"Số bảng thành công: "
        f"{len(successful_tables)}"
    )
    print(
        f"Số bucket thành công: "
        f"{len(successful_buckets)}"
    )
    print(
        f"Tổng file Storage: "
        f"{storage_file_count}"
    )
    print(
        f"Tổng dung lượng Storage: "
        f"{storage_size} byte"
    )
    print(
        "Tổng file mã nguồn: "
        f"{manifest['app_file_count']}"
    )
    print(
        f"Tổng số dòng Database: "
        f"{total_rows}"
    )
    print(
        f"Số hạng mục lỗi: "
        f"{len(failed_items)}"
    )
    print("========================================")

    if args.save_supabase:
        manifest_id = save_manifest_to_supabase(
            manifest,
            manifest_items,
        )

        print(
            "Đã ghi manifest lên Supabase. "
            f"ID: {manifest_id}"
        )

    if failed_items:
        sys.exit(2)


if __name__ == "__main__":
    main()