"""Tests for CLI main commands (dev, urls)."""

from unittest.mock import patch

from click.testing import CliRunner

# Import the commands to register them with the main group
import app.cli.main  # noqa: F401

from app.cli.groups import main


class TestDevCommand:
    """Tests for the 'dev' command."""

    def test_dev_help(self):
        """Test that dev --help works."""
        runner = CliRunner()
        result = runner.invoke(main, ["dev", "--help"])
        assert result.exit_code == 0
        assert "Run the development server with hot reload" in result.output
        assert "--port" in result.output or "-p" in result.output

    def test_dev_help_short_flag(self):
        """Test that dev -h works (our custom click module feature)."""
        runner = CliRunner()
        result = runner.invoke(main, ["dev", "-h"])
        assert result.exit_code == 0
        assert "Run the development server with hot reload" in result.output

    def test_dev_default_port(self):
        """Test that dev runs with default port 8000."""
        runner = CliRunner()
        with patch("app.cli.main.uvicorn.run") as mock_run:
            result = runner.invoke(main, ["dev"])
            assert result.exit_code == 0
            mock_run.assert_called_once_with(
                "app:app", host="0.0.0.0", port=8000, reload=True
            )

    def test_dev_custom_port(self):
        """Test that dev accepts a custom port."""
        runner = CliRunner()
        with patch("app.cli.main.uvicorn.run") as mock_run:
            result = runner.invoke(main, ["dev", "--port", "9000"])
            assert result.exit_code == 0
            mock_run.assert_called_once_with(
                "app:app", host="0.0.0.0", port=9000, reload=True
            )

    def test_dev_custom_port_short_flag(self):
        """Test that dev accepts -p for port."""
        runner = CliRunner()
        with patch("app.cli.main.uvicorn.run") as mock_run:
            result = runner.invoke(main, ["dev", "-p", "3000"])
            assert result.exit_code == 0
            mock_run.assert_called_once_with(
                "app:app", host="0.0.0.0", port=3000, reload=True
            )


class TestUrlsCommand:
    """Tests for the 'urls' command."""

    def test_urls_help(self):
        """Test that urls --help works."""
        runner = CliRunner()
        result = runner.invoke(main, ["urls", "--help"])
        assert result.exit_code == 0
        assert "List all routes registered on the app" in result.output

    def test_urls_output(self):
        """Test that urls command outputs route table."""
        runner = CliRunner()
        result = runner.invoke(main, ["urls"])
        assert result.exit_code == 0

        # Check table headers
        assert "Method" in result.output
        assert "URL" in result.output
        assert "Endpoint" in result.output

        # Check some expected routes exist
        assert "/" in result.output
        assert "/api/v1/" in result.output
        assert "/auth/v1/" in result.output or "/auth/v1/jwt/login" in result.output

    def test_urls_includes_admin_routes(self):
        """Test that urls includes admin panel routes."""
        runner = CliRunner()
        result = runner.invoke(main, ["urls"])
        assert result.exit_code == 0
        assert "/admin/" in result.output

    def test_urls_includes_auth_routes(self):
        """Test that urls includes authentication routes."""
        runner = CliRunner()
        result = runner.invoke(main, ["urls"])
        assert result.exit_code == 0
        assert "/auth/v1/jwt/login" in result.output
        assert "/auth/v1/cookie/login" in result.output
        assert "/auth/v1/register" in result.output


class TestMainGroup:
    """Tests for the main CLI group itself."""

    def test_main_help(self):
        """Test that main --help works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "dev" in result.output
        assert "urls" in result.output
        assert "users" in result.output

    def test_main_help_short_flag(self):
        """Test that main -h works."""
        runner = CliRunner()
        result = runner.invoke(main, ["-h"])
        assert result.exit_code == 0
        assert "dev" in result.output

    def test_main_without_subcommand_runs_dev(self):
        """Test that running main without a subcommand defaults to dev."""
        runner = CliRunner()
        with patch("app.cli.main.uvicorn.run") as mock_run:
            result = runner.invoke(main, [])
            assert result.exit_code == 0
            mock_run.assert_called_once()
