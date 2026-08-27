"""LectureSift Render entrypoint."""

from lecturesift.app import app
from lecturesift.commerce_routes import install_commerce_routes
from lecturesift.durable_runtime import install_durable_runtime
from lecturesift.rollout_routes import install_rollout_routes
from lecturesift.security import install_security
from lecturesift.social_routes import install_social_routes

install_rollout_routes(app)
install_commerce_routes(app)
install_social_routes(app)
install_security(app)
install_durable_runtime()

__all__ = ["app"]
