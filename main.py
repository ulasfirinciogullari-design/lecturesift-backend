"""LectureSift Render entrypoint."""

from lecturesift.app import app
from lecturesift.email_auth import install_email_auth
from lecturesift.social_routes import install_social_routes

install_email_auth(app)
install_social_routes(app)

__all__ = ["app"]
