"""
Pytest Configuration and Safety Isolation Fixtures.
Strictly verifies that tests never run against a production Oracle instance or schema.
"""
import os
import sys
import pytest
from app import create_app
from app.config import Config, TestingConfig
from app.extensions import db


def assert_test_environment_safety():
    """
    Safety Guard (Instruction c):
    Verifies test database configuration parameters are isolated from production.
    Aborts immediately if test DSN, schema, or user matches production.
    """
    prod_service = os.environ.get("PROD_DB_SERVICE_NAME", Config.DB_SERVICE_NAME)
    prod_user = (os.environ.get("PROD_DB_USER") or (Config.DB_USER if Config.DB_USER != "isi_pos_test" else "isi") or "").lower()
    prod_schema = (os.environ.get("PROD_DB_SCHEMA") or (Config.DB_SCHEMA if Config.DB_SCHEMA != "ISI_POS_TEST" else "isi") or "").lower()

    test_service = os.environ.get("TEST_DB_SERVICE_NAME") or TestingConfig.TEST_DB_SERVICE_NAME
    test_user = (os.environ.get("TEST_DB_USER") or TestingConfig.TEST_DB_USER or "").lower()
    test_schema = (os.environ.get("TEST_DB_SCHEMA") or TestingConfig.TEST_DB_SCHEMA or "").lower()

    if test_user and prod_user and test_user == prod_user and (not test_service or not prod_service or test_service == prod_service):
        sys.exit(
            "FATAL SAFETY FAILURE: Test DB service and user match production credentials! "
            "Automated tests are strictly prevented from running against production."
        )

    if test_schema and prod_schema and test_schema == prod_schema:
        sys.exit(
            "FATAL SAFETY FAILURE: Test schema matches production schema! "
            "Tests must point to an isolated schema (e.g., ISI_POS_TEST)."
        )


def pytest_configure(config):
    """Safety guard executed before test collection/execution."""
    assert_test_environment_safety()


@pytest.fixture(scope="session")
def app():
    """Application fixture initialized in testing mode with safety verification."""
    assert_test_environment_safety()

    app_instance = create_app("testing")
    app_instance.config["WTF_CSRF_ENABLED"] = False

    if "manager_only_test" not in app_instance.view_functions:
        from flask_login import login_required
        from app.utils.decorators import role_required

        @app_instance.route("/manager-only")
        @login_required
        @role_required("manager")
        def manager_only_test():
            return "manager access ok"

    return app_instance


@pytest.fixture(scope="function")
def client(app):
    """Test client for HTTP requests (CSRF disabled for general flows)."""
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_client()


@pytest.fixture(scope="function")
def csrf_client(app):
    """Test client with CSRF protection strictly enabled."""
    app.config["WTF_CSRF_ENABLED"] = True
    yield app.test_client()
    app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture(scope="function")
def runner(app):
    """CLI runner fixture for testing Flask commands."""
    return app.test_cli_runner()
