"""Tests for Arth CLI module."""

import pytest
from click.testing import CliRunner
from rich.console import Console

from src.cli.main import main


class TestCLI:
    """Test CLI functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_main_command_help(self):
        """Test that main command shows help."""
        result = self.runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Arth - Personal Finance System CLI" in result.output

    def test_main_command_version(self):
        """Test that version option works."""
        result = self.runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_status_command(self):
        """Test status command."""
        result = self.runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "Arth Personal Finance System" in result.output
        assert "System Status" in result.output
        assert "CLI" in result.output
        assert "Database Models" in result.output

    def test_edit_command_help(self):
        """Test that edit command shows help."""
        result = self.runner.invoke(main, ["edit", "--help"])
        assert result.exit_code == 0
        assert "Edit data manually" in result.output

    def test_add_transaction_dry_run(self):
        """Test add transaction command with dry run."""
        result = self.runner.invoke(
            main,
            [
                "edit",
                "add-txn",
                "--account",
                "test-account",
                "--date",
                "2024-01-01",
                "--amount",
                "100.0",
                "--type",
                "credit",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "test-account" in result.output
        assert "2024-01-01" in result.output
        assert "100.0" in result.output
        assert "credit" in result.output

    def test_add_transaction_without_dry_run(self):
        """Test add transaction command without dry run."""
        result = self.runner.invoke(
            main,
            [
                "edit",
                "add-txn",
                "--account",
                "test-account",
                "--date",
                "2024-01-01",
                "--amount",
                "100.0",
                "--type",
                "credit",
            ],
        )
        assert result.exit_code == 0
        assert "Error" in result.output
        assert "not yet implemented" in result.output

    def test_update_holding(self):
        """Test update holding command."""
        result = self.runner.invoke(
            main,
            [
                "edit",
                "update-holding",
                "--id",
                "holding-1",
                "--qty",
                "50.0",
            ],
        )
        assert result.exit_code == 0
        assert "Error" in result.output
        assert "not yet implemented" in result.output

    def test_reprice_asset(self):
        """Test reprice asset command."""
        result = self.runner.invoke(
            main,
            [
                "edit",
                "reprice-asset",
                "--symbol",
                "AAPL",
                "--price",
                "150.0",
                "--date",
                "2024-01-01",
            ],
        )
        assert result.exit_code == 0
        assert "Error" in result.output
        assert "not yet implemented" in result.output

    def test_missing_required_options(self):
        """Test that missing required options show appropriate errors."""
        result = self.runner.invoke(main, ["edit", "add-txn"])
        assert result.exit_code != 0
        assert "Missing option" in result.output

    def test_invalid_command(self):
        """Test that invalid commands show appropriate errors."""
        result = self.runner.invoke(main, ["invalid-command"])
        assert result.exit_code != 0
        assert "No such command" in result.output


class TestCLIFunctions:
    """Test individual CLI functions."""

    def test_console_initialization(self):
        """Test that console is properly initialized."""
        from src.cli.main import console
        assert isinstance(console, Console)

    def test_main_function_exists(self):
        """Test that main function exists and is callable."""
        assert callable(main) 