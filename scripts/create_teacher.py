"""Create a teacher login. Teachers cannot sign themselves up; an admin runs this.

Usage:
    python scripts/create_teacher.py teacher@college.edu "Full Name"

Reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from the environment or from .env.
The password is asked for at the prompt so it never lands in shell history.
When input is piped (tests, scripts), the first line of stdin is used instead.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    email, full_name = sys.argv[1], sys.argv[2]

    load_env()
    url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not service_key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see .env.example).")

    if sys.stdin.isatty():
        password = getpass.getpass("Password for the teacher: ")
    else:
        password = sys.stdin.readline().strip()

    body = {
        "email": email,
        "password": password,
        "email_confirm": True,
        # app_metadata is only writable with the service key, so it is safe to trust for the role.
        "app_metadata": {"role": "teacher"},
        "user_metadata": {"full_name": full_name},
    }
    request = urllib.request.Request(
        f"{url}/auth/v1/admin/users",
        data=json.dumps(body).encode(),
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            user = json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"Failed ({error.code}): {error.read().decode()}")

    print(f"Created teacher {user['email']} with id {user['id']}")


if __name__ == "__main__":
    main()
