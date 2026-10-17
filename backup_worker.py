#!/usr/bin/env python3
from app import create_system_backup

if __name__ == "__main__":
    result = create_system_backup(kind="automatic")
    print(
        "[BACKUP OK]",
        result.get("created_at"),
        result.get("path"),
        "size=", result.get("size"),
        "deleted_old=", result.get("deleted_old"),
    )
