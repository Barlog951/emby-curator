"""
Tests for the __main__ module.
"""
from unittest.mock import patch


class TestMain:
    """Tests for the __main__ entry point."""

    def test_main_callable(self):
        """The main entry point must be importable and callable."""
        from emby_dedupe.__main__ import main
        assert callable(main)

    def test_main_calls_app(self):
        """__main__.main() must delegate to the typer app."""
        with patch("emby_dedupe.__main__.app") as mock_app:
            from emby_dedupe.__main__ import main
            main()
            mock_app.assert_called_once()
