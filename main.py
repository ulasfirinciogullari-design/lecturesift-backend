"""LectureSift Render entrypoint."""

from lecturesift.app import app
from lecturesift.durable_runtime import install_durable_runtime
from lecturesift.rollout_routes import install_rollout_routes
from lecturesift.social_routes import install_social_routes

install_rollout_routes(app)
install_social_routes(app)
install_durable_runtime()

__all__ = ["app"]
