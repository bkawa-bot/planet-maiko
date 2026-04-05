"""Shared pytest fixtures for Planet Maiko tests."""

import os
import pytest
from planet_maiko.database import db as _db


@pytest.fixture
def app(tmp_path):
    """Create test app with in-memory SQLite.

    Sets SQLALCHEMY_DATABASE_URI *before* create_app so the engine
    binds to :memory: from the start.
    """
    # Use a temp directory for config/data so create_app doesn't touch real files
    os.environ["MAIKO_CONFIG_DIR"] = str(tmp_path / "config")
    os.environ["MAIKO_DATA_DIR"] = str(tmp_path / "data")

    from planet_maiko.app import create_app
    app = create_app(start_scheduler=False)
    app.config["TESTING"] = True

    # The engine is already bound via create_app -> db.init_app, but since
    # create_app uses the MAIKO_DATA_DIR temp path, we get an isolated DB.
    # For true in-memory, override and recreate tables:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"

    with app.app_context():
        # Re-bind engine to the in-memory URI
        _db.engine.dispose()
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()

    # Clean up env vars
    os.environ.pop("MAIKO_CONFIG_DIR", None)
    os.environ.pop("MAIKO_DATA_DIR", None)


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def db(app):
    """Database session scoped to the test app context."""
    with app.app_context():
        yield _db
