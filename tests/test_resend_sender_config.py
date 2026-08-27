import os
import subprocess
import sys


def _load_sender(tmp_path, from_email: str) -> tuple[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "LECTURESIFT_WORK_DIR": str(tmp_path),
            "RESEND_SENDING_DOMAIN": "mail.lecturesift.com",
            "RESEND_FROM_EMAIL": from_email,
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from lecturesift import config; "
                "print(config.RESEND_FROM_EMAIL); "
                "print(str(config.RESEND_FROM_EMAIL_CORRECTED).lower())"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    sender, corrected = result.stdout.strip().splitlines()
    return sender, corrected


def test_unverified_parent_sender_falls_back_to_verified_subdomain(tmp_path):
    sender, corrected = _load_sender(tmp_path, "LectureSift <noreply@lecturesift.com>")
    assert sender == "LectureSift <no-reply@mail.lecturesift.com>"
    assert corrected == "true"


def test_sender_on_verified_subdomain_is_preserved(tmp_path):
    sender, corrected = _load_sender(tmp_path, "LectureSift <verify@mail.lecturesift.com>")
    assert sender == "LectureSift <verify@mail.lecturesift.com>"
    assert corrected == "false"
