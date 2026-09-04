"""Create a staff account interactively.

    python -m scripts.create_admin

The production alternative to `seed_demo`, which must never be run against a
real database because it creates accounts with a published password.

Passwords are read from a prompt rather than an argument, so they do not end up
in shell history or in the process list.
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.constants import Role  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.services.auth import AuthService  # noqa: E402

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STAFF_ROLES = {
    "1": (Role.OFFICER, "Food Safety Officer — sees vendor-level detail, audited"),
    "2": (Role.RETAILER, "Retailer — SATVA Shelf"),
    "3": (Role.FARMER, "Farmer / FPO — Trace and Direct"),
    "4": (Role.ADMIN, "Administrator — full access"),
}


def prompt_password() -> str:
    """Read a password twice, enforcing a usable minimum length.

    Twelve characters rather than eight: these accounts can read vendor-level
    data, and the hashing cost is high enough that a short password's only real
    defence is length.
    """
    while True:
        password = getpass.getpass("Password (min 12 characters): ")
        if len(password) < 12:
            print("  Too short.\n")
            continue
        if password != getpass.getpass("Confirm password: "):
            print("  Passwords do not match.\n")
            continue
        return password


def main() -> int:
    print("\nSATVA — create a staff account")
    print("=" * 46)

    for key, (role, description) in STAFF_ROLES.items():
        print(f"  {key}. {role.value:<9} {description}")

    choice = input("\nRole [1]: ").strip() or "1"
    if choice not in STAFF_ROLES:
        print("Unknown role.")
        return 1
    role, _ = STAFF_ROLES[choice]

    email = input("Email: ").strip().lower()
    if not EMAIL_RE.match(email):
        print("That does not look like an email address.")
        return 1

    display_name = input("Display name: ").strip() or None

    designation = jurisdiction = None
    if role is Role.OFFICER:
        designation = input("Designation [Food Safety Officer]: ").strip() or "Food Safety Officer"
        jurisdiction = input("Jurisdiction (district): ").strip() or None

    password = prompt_password()

    try:
        with session_scope() as db:
            user = AuthService(db).create_staff_user(
                email=email,
                password=password,
                role=role,
                display_name=display_name,
                officer_designation=designation,
                officer_jurisdiction=jurisdiction,
            )
            user_id = str(user.id)
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not create the account: {exc}")
        return 1

    print("\nCreated.")
    print(f"  id    : {user_id}")
    print(f"  email : {email}")
    print(f"  role  : {role.value}")
    if role is Role.OFFICER:
        print(
            "\n  This account can see vendor-level detail. Every such view is "
            "written to identity.audit_log."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
