"""LectureSift Render entrypoint."""

from lecturesift.app import app
from lecturesift.social_routes import install_social_routes

install_social_routes(app)

__all__ = ["app"]
