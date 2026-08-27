"""LectureSift Render entrypoint."""

from lecturesift.app import app
from lecturesift.email_auth import install_email_auth

install_email_auth(app)

__all__ = ["app"]
