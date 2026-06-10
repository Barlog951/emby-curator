"""
Tests for HTML report generation
"""
from unittest.mock import MagicMock, patch

from emby_dedupe.reports.html import (
    _create_language_priority_message,
    _detect_language_priority_usage,
    _ensure_quality_fields,
    _process_delete_item,
    _validate_decisions,
    format_html_report,
    generate_html_report,
)


class TestHtmlReports:
    """Tests for HTML report generation."""

    @patch('jinja2.Environment')
    @patch('jinja2.FileSystemLoader')
    @patch('emby_dedupe.reports.common.calculate_report_statistics')
    def test_format_html_report_basic(self, mock_calculate_stats, mock_loader, mock_env):
        """Test the HTML report formatting with basic data."""
        # Setup mocks for the Jinja template system
        mock_env_instance = MagicMock()
        mock_env.return_value = mock_env_instance

        mock_template = MagicMock()
        mock_env_instance.get_template.return_value = mock_template
        mock_template.render.return_value = "<html>Test Content</html>"

        # Prepare test data
        base_url = "http://example.com"
        decisions = []

        # Mock statistics calculation
        mock_calculate_stats.return_value = {
            "total_groups": 0,
            "total_items_to_keep": 0,
            "total_items_to_delete": 0,
            "deleted_items": 0,
            "failed_deletions": 0,
            "skipped_deletions": 0,
            "formatted_size_to_keep": "0 B",
            "formatted_size_to_delete": "0 B",
            "percentage_saved": 0
        }

        # Patch the tqdm progress bar
        with patch('emby_dedupe.reports.html.tqdm') as mock_tqdm:
            mock_progress_bar = MagicMock()
            mock_tqdm.return_value = mock_progress_bar

            # Call the function
            try:
                result = format_html_report(base_url, decisions)

                # Verify output
                assert result == "<html>Test Content</html>"
                assert mock_template.render.called
            except ImportError:
                # Handle the case where jinja2 is not installed (might happen in test environment)
                pass


    @patch('jinja2.Environment')
    @patch('jinja2.FileSystemLoader')
    @patch('emby_dedupe.reports.common.calculate_report_statistics')
    def test_format_html_report_with_language_priorities(self, mock_calculate_stats, mock_loader, mock_env):
        """Test HTML report with language prioritization."""
        # Setup mocks for the Jinja template system
        mock_env_instance = MagicMock()
        mock_env.return_value = mock_env_instance

        mock_template = MagicMock()
        mock_env_instance.get_template.return_value = mock_template
        mock_template.render.return_value = "<html>Test Content with Language Priorities</html>"

        # Prepare test data with language priorities
        base_url = "http://example.com"
        decisions = [
            {
                "keep": {
                    "id": "keep1",
                    "name": "Keep Item 1",
                    "serverid": "server1",
                    "selected_by_language_priority": True,
                    "changed_by_language_priority": True,
                    "priority_language_used": "eng",
                    "language_priority_list": ["eng", "spa", "fre"],
                    "quality_description": {
                        "video": {"codec": "h265", "resolution": "4K"},
                        "audio": {"codec": "dts", "channels": 6, "languages": ["eng", "spa"]},
                        "date_added": "2023-01-15"
                    }
                },
                "delete": [
                    {
                        "id": "delete1",
                        "name": "Delete Item 1",
                        "deletion_result": {"status": "success", "error": None},
                        "quality_description": {
                            "video": {"codec": "h264", "resolution": "1080p"},
                            "audio": {"codec": "aac", "channels": 2, "languages": ["fre"]},
                            "date_added": "2022-12-31"
                        }
                    }
                ]
            }
        ]

        # Mock statistics calculation
        mock_calculate_stats.return_value = {
            "total_groups": 1,
            "total_items_to_keep": 1,
            "total_items_to_delete": 1,
            "deleted_items": 1,
            "failed_deletions": 0,
            "skipped_deletions": 0,
            "formatted_size_to_keep": "1 GB",
            "formatted_size_to_delete": "500 MB",
            "percentage_saved": 33.3
        }

        # Patch the tqdm progress bar
        with patch('emby_dedupe.reports.html.tqdm') as mock_tqdm:
            mock_progress_bar = MagicMock()
            mock_tqdm.return_value = mock_progress_bar

            # Call the function
            try:
                result = format_html_report(base_url, decisions)

                # Verify output
                assert result == "<html>Test Content with Language Priorities</html>"
                assert mock_template.render.called

                # Verify template data contains language priority information
                template_data = mock_template.render.call_args[1]
                assert template_data["language_priorities_used"]
                assert template_data["language_priorities_changed_selection"]
                assert template_data["language_priorities_list"] == ["eng", "spa", "fre"]
            except ImportError:
                # Handle the case where jinja2 is not installed
                pass

    @patch('jinja2.Environment')
    @patch('jinja2.FileSystemLoader')
    @patch('emby_dedupe.reports.common.calculate_report_statistics')
    def test_format_html_report_with_excluded_ids(self, mock_calculate_stats, mock_loader, mock_env):
        """Test HTML report with excluded provider IDs."""
        # Setup mocks for the Jinja template system
        mock_env_instance = MagicMock()
        mock_env.return_value = mock_env_instance

        mock_template = MagicMock()
        mock_env_instance.get_template.return_value = mock_template
        mock_template.render.return_value = "<html>Test Content with Excluded IDs</html>"

        # Prepare test data with excluded IDs
        base_url = "http://example.com"
        decisions = [
            {
                "keep": {
                    "id": "keep1",
                    "name": "Keep Item 1",
                    "serverid": "server1",
                    "quality_description": {
                        "video": {"codec": "h265", "resolution": "4K"},
                        "audio": {"codec": "dts", "channels": 6, "languages": ["eng"]},
                        "date_added": "2023-01-15"
                    }
                },
                "delete": [
                    {
                        "id": "delete1",
                        "name": "Delete Item 1",
                        "deletion_result": {"status": "success", "error": None},
                        "quality_description": {
                            "video": {"codec": "h264", "resolution": "1080p"},
                            "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                            "date_added": "2022-12-31"
                        }
                    }
                ]
            }
        ]

        # Create metadata with excluded IDs
        metadata = {
            "excluded_ids": ["tt0120737", "tt0167261", "550"],
            "excluded_groups_count": 3,
            "excluded_titles": {
                "tt0120737": {
                    "title": "The Lord of the Rings: The Fellowship of the Ring",
                    "year": 2001,
                    "image_url": "https://image.tmdb.org/t/p/w300/6oom5QYQ2yQTMJIbnvbkBL9cHo6.jpg"
                },
                "tt0167261": {
                    "title": "The Lord of the Rings: The Two Towers",
                    "year": 2002,
                    "image_url": "https://image.tmdb.org/t/p/w300/5VTN0pR8gcqV3EPUHHfMGnJYN9L.jpg"
                },
                "550": {
                    "title": "Fight Club",
                    "year": 1999,
                    "image_url": "https://image.tmdb.org/t/p/w300/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg"
                }
            }
        }

        # Mock statistics calculation
        mock_calculate_stats.return_value = {
            "total_groups": 1,
            "total_items_to_keep": 1,
            "total_items_to_delete": 1,
            "deleted_items": 1,
            "failed_deletions": 0,
            "skipped_deletions": 0,
            "formatted_size_to_keep": "1 GB",
            "formatted_size_to_delete": "500 MB",
            "percentage_saved": 33.3
        }

        # Patch the tqdm progress bar
        with patch('emby_dedupe.reports.html.tqdm') as mock_tqdm:
            mock_progress_bar = MagicMock()
            mock_tqdm.return_value = mock_progress_bar

            # Call the function
            try:
                result = format_html_report(base_url, decisions, metadata)

                # Verify output
                assert result == "<html>Test Content with Excluded IDs</html>"
                assert mock_template.render.called

                # Verify template data contains excluded IDs information
                template_data = mock_template.render.call_args[1]
                assert template_data["has_excluded_ids"]
                assert len(template_data["excluded_ids"]) == 3
                assert "tt0120737" in template_data["excluded_ids"]
                assert template_data["excluded_groups_count"] == 3
                assert "tt0120737" in template_data["excluded_titles"]
                assert template_data["excluded_titles"]["tt0120737"]["title"] == "The Lord of the Rings: The Fellowship of the Ring"
                assert template_data["excluded_titles"]["tt0120737"]["year"] == 2001
            except ImportError:
                # Handle the case where jinja2 is not installed
                pass

    @patch('emby_dedupe.reports.html.format_html_report')
    @patch('tempfile.gettempdir')
    @patch('time.time')
    def test_generate_html_report(self, mock_time, mock_tempdir, mock_format_html):
        """Test HTML report generation to a file."""
        # Setup mocks
        base_url = "http://example.com"
        decisions = [{"keep": {"id": "123"}, "delete": [{"id": "456"}]}]
        mock_format_html.return_value = "<html>Test content</html>"
        mock_tempdir.return_value = "/tmp"
        mock_time.return_value = 1234567890

        # Create a simple mock for file operations
        m = MagicMock()
        m_handle = MagicMock()
        m.return_value.__enter__.return_value = m_handle

        # Create a mock for path joining
        path_join_mock = MagicMock(return_value="/tmp/emby_dedupe_report_1234567890.html")

        # Patch the necessary functions
        with patch('builtins.open', m):
            with patch('os.path.join', path_join_mock):
                with patch('shutil.copy2'):
                    result = generate_html_report(base_url, decisions)

        # Verify the result is the file path
        assert "emby_dedupe_report_1234567890.html" in result

    @patch('emby_dedupe.reports.html.format_html_report')
    def test_generate_html_report_with_css_error(self, mock_format_html):
        """Test HTML report generation handling CSS copy errors gracefully."""
        # Setup
        base_url = "http://emby.server"
        decisions = [{"keep": {"id": "123"}, "delete": [{"id": "456"}]}]

        mock_format_html.return_value = "<html>Test content</html>"

        # Mock shutil.copy2 to raise an IOError
        with patch('shutil.copy2', side_effect=IOError("Test error")):
            # Mock open to avoid actual file operations
            with patch('builtins.open', MagicMock()):
                # Mock logger to check error is logged
                with patch('emby_dedupe.reports.html.logger'):
                    # Mock os.path.join
                    with patch('os.path.join', return_value="/tmp/report.html"):
                        with patch('tempfile.gettempdir', return_value="/tmp"):
                            with patch('time.time', return_value=1234567890):
                                result = generate_html_report(base_url, decisions)

                                # Just verify the function returns some string
                                assert isinstance(result, str)

    @patch('jinja2.Environment')
    @patch('jinja2.FileSystemLoader')
    @patch('emby_dedupe.reports.common.calculate_report_statistics')
    def test_format_html_report_with_deleted_items_external_links(self, mock_calculate_stats, mock_loader, mock_env):
        """Test HTML report with deleted items showing external links."""
        # Setup mocks for the Jinja template system
        mock_env_instance = MagicMock()
        mock_env.return_value = mock_env_instance

        mock_template = MagicMock()
        mock_env_instance.get_template.return_value = mock_template
        mock_template.render.return_value = "<html>Test Content with Deleted Items</html>"

        # Prepare test data with deleted items that have provider IDs
        base_url = "http://example.com"
        decisions = [
            {
                "keep": {
                    "id": "keep1",
                    "name": "Item to Keep",
                    "serverid": "server1",
                    "quality_description": {
                        "video": {"codec": "h265", "resolution": "4K"},
                        "audio": {"codec": "dts", "channels": 6, "languages": ["eng"]},
                        "date_added": "2023-01-15"
                    }
                },
                "delete": [
                    {
                        "id": "delete1",
                        "name": "IMDB Item",
                        "url": "http://example.com/item/delete1",
                        "provider_id": "tt1234567",  # IMDB ID
                        "deletion_result": {"status": "success", "error": None},
                        "quality_description": {
                            "video": {"codec": "h264", "resolution": "1080p"},
                            "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                            "date_added": "2022-12-31"
                        }
                    },
                    {
                        "id": "delete2",
                        "name": "TMDB Item",
                        "url": "http://example.com/item/delete2",
                        "provider_id": "123456",  # TMDB ID
                        "deletion_result": {"status": "success", "error": None},
                        "quality_description": {
                            "video": {"codec": "h264", "resolution": "1080p"},
                            "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                            "date_added": "2022-12-31"
                        }
                    },
                    {
                        "id": "delete3",
                        "name": "Not Deleted Item",
                        "url": "http://example.com/item/delete3",
                        "deletion_result": {"status": "skipped", "error": None},
                        "quality_description": {
                            "video": {"codec": "h264", "resolution": "1080p"},
                            "audio": {"codec": "aac", "channels": 2, "languages": ["eng"]},
                            "date_added": "2022-12-31"
                        }
                    }
                ]
            }
        ]

        # Mock statistics calculation
        mock_calculate_stats.return_value = {
            "total_groups": 1,
            "total_items_to_keep": 1,
            "total_items_to_delete": 3,
            "deleted_items": 2,
            "failed_deletions": 0,
            "skipped_deletions": 1,
            "formatted_size_to_keep": "1 GB",
            "formatted_size_to_delete": "1.5 GB",
            "percentage_saved": 33.3
        }

        # Patch the tqdm progress bar
        with patch('emby_dedupe.reports.html.tqdm') as mock_tqdm:
            mock_progress_bar = MagicMock()
            mock_tqdm.return_value = mock_progress_bar

            # Call the function
            try:
                # Create a mock function that captures the render arguments
                def mock_render_with_capture(**kwargs):
                    mock_render_with_capture.last_kwargs = kwargs
                    return "<html>Test Content with Deleted Items</html>"

                mock_template.render.side_effect = mock_render_with_capture

                result = format_html_report(base_url, decisions)

                # Verify output
                assert result == "<html>Test Content with Deleted Items</html>"
                assert mock_template.render.called

                # First, verify the function actually ran and captured kwargs
                assert hasattr(mock_render_with_capture, 'last_kwargs')

                # Extract template data from our captured kwargs
                template_data = mock_render_with_capture.last_kwargs

                # Check the duplicate_groups are passed correctly
                assert "duplicate_groups" in template_data
                assert len(template_data["duplicate_groups"]) == 1

                # Verify that delete items have proper metadata for template
                delete_items = template_data["duplicate_groups"][0]["delete"]
                assert len(delete_items) == 3

                # Test the conditional rendering for external links
                for item in decisions[0]["delete"]:
                    # If the item is deleted and has a provider ID,
                    # it should show external links
                    if item.get("deletion_result", {}).get("status") == "success":
                        if "provider_id" in item:
                            if item["provider_id"].startswith("tt"):
                                f"https://www.imdb.com/title/{item['provider_id']}"
                                assert "IMDB" in item["name"]  # Verify it's our IMDB test item
                            elif item["provider_id"].isdigit():
                                f"https://www.themoviedb.org/movie/{item['provider_id']}"
                                assert "TMDB" in item["name"]  # Verify it's our TMDB test item
                    else:
                        # Non-deleted items should still have their Emby URL
                        assert "url" in item
                        assert "Not Deleted" in item["name"]

            except ImportError:
                # Handle the case where jinja2 is not installed
                pass

# ========== SAFETY NET TESTS FOR format_html_report (Grade-F Function) ==========

class TestFormatHtmlReportSafetyNet:
    """Safety net tests for format_html_report (CC 93) to protect Phase 3 refactoring."""

    def test_format_html_report_missing_quality_description(self):
        """Test format_html_report handles missing quality_description gracefully."""
        decisions = [
            {
                "keep": {"id": "keep1", "name": "Item 1"},
                "delete": [{"id": "del1", "name": "Delete 1"}]
            }
        ]

        result = format_html_report("http://emby.local", decisions, None)

        assert result is not None
        assert isinstance(result, str)

    def test_format_html_report_empty_decisions(self):
        """Test format_html_report with empty decisions list."""
        result = format_html_report("http://emby.local", [], None)

        assert result is not None
        assert isinstance(result, str)

    def test_format_html_report_unicode_handling(self):
        """Test format_html_report handles unicode characters correctly."""
        decisions = [
            {
                "keep": {
                    "id": "k1",
                    "name": "Película Española",
                    "quality_description": {"video": {"codec": "h264"}}
                },
                "delete": [
                    {
                        "id": "d1",
                        "name": "Český film",
                        "quality_description": {"video": {"codec": "h264"}}
                    }
                ]
            }
        ]

        result = format_html_report("http://emby.local", decisions, {"api_key": "test-key"})

        assert result is not None
        assert isinstance(result, str)


class TestHtmlReportHelpers:
    """Tests for helper functions extracted from format_html_report."""

    def test_validate_decisions_all_valid(self):
        """Test validation with all valid decisions."""
        decisions = [
            {"keep": {"id": "1"}, "delete": [{"id": "2"}]},
            {"keep": {"id": "3"}, "delete": [{"id": "4"}]},
        ]

        result = _validate_decisions(decisions)

        assert len(result) == 2
        assert result == decisions

    def test_validate_decisions_filters_invalid(self):
        """Test that invalid decisions are filtered out."""
        decisions = [
            {"keep": {"id": "1"}, "delete": [{"id": "2"}]},  # Valid
            {"keep": None, "delete": [{"id": "3"}]},  # Invalid: no keep
            {"keep": {"id": "4"}, "delete": []},  # Invalid: no delete items
            {"keep": {}, "delete": [{"id": "5"}]},  # Invalid: keep has no id
            {"keep": {"id": "6"}, "delete": [{"id": "7"}]},  # Valid
        ]

        result = _validate_decisions(decisions)

        assert len(result) == 2
        assert result[0]["keep"]["id"] == "1"
        assert result[1]["keep"]["id"] == "6"

    def test_detect_language_priority_not_used(self):
        """Test detection when language priority was not used."""
        decisions = [
            {"keep": {"id": "1"}, "delete": [{"id": "2"}]},
        ]

        used, changed, priority_list = _detect_language_priority_usage(decisions)

        assert used is False
        assert changed is False
        assert priority_list is None

    def test_detect_language_priority_used_not_changed(self):
        """Test detection when language priority was used but didn't change selection."""
        decisions = [
            {
                "keep": {
                    "id": "1",
                    "selected_by_language_priority": True,
                    "changed_by_language_priority": False,
                    "language_priority_list": ["sk", "cs"],
                },
                "delete": [{"id": "2"}],
            },
        ]

        used, changed, priority_list = _detect_language_priority_usage(decisions)

        assert used is True
        assert changed is False
        assert priority_list == ["sk", "cs"]

    def test_detect_language_priority_changed_selection(self):
        """Test detection when language priority changed the selection."""
        decisions = [
            {
                "keep": {
                    "id": "1",
                    "selected_by_language_priority": True,
                    "changed_by_language_priority": True,
                    "language_priority_list": ["cs"],
                },
                "delete": [{"id": "2"}],
            },
        ]

        used, changed, priority_list = _detect_language_priority_usage(decisions)

        assert used is True
        assert changed is True
        assert priority_list == ["cs"]

    def test_ensure_quality_fields_empty_dict_returns_early(self):
        """Test that empty dict returns early without modification."""
        quality_desc = {}

        _ensure_quality_fields(quality_desc)

        # Empty dict evaluates to falsy, so function returns early
        assert quality_desc == {}

    def test_ensure_quality_fields_adds_missing_video(self):
        """Test that missing video section is added to non-empty dict."""
        quality_desc = {"some_field": "value"}

        _ensure_quality_fields(quality_desc)

        assert "video" in quality_desc
        assert quality_desc["video"]["codec"] == "unknown"
        assert quality_desc["video"]["resolution"] == "unknown"

    def test_ensure_quality_fields_adds_missing_audio(self):
        """Test that missing audio section is added."""
        quality_desc = {"video": {"codec": "h264"}}

        _ensure_quality_fields(quality_desc)

        assert "audio" in quality_desc
        assert quality_desc["audio"]["codec"] == "unknown"
        assert quality_desc["audio"]["languages"] == ["unknown"]

    def test_ensure_quality_fields_fixes_callable_languages(self):
        """Test that callable languages field is fixed."""
        quality_desc = {
            "video": {"codec": "h264"},
            "audio": {"codec": "aac", "languages": lambda: ["eng"]},  # Callable - should be fixed
        }

        _ensure_quality_fields(quality_desc)

        assert quality_desc["audio"]["languages"] == ["unknown"]

    def test_ensure_quality_fields_preserves_valid_data(self):
        """Test that valid quality fields are preserved."""
        quality_desc = {
            "video": {"codec": "hevc", "resolution": "2160p"},
            "audio": {"codec": "eac3", "channels": 6, "languages": ["eng", "cze"]},
        }
        original = quality_desc.copy()

        _ensure_quality_fields(quality_desc)

        assert quality_desc == original

    def test_create_language_priority_message_changed(self):
        """Test message when language priority changed selection."""
        keep_item = {
            "selected_by_language_priority": True,
            "changed_by_language_priority": True,
            "priority_language_used": "sk",
            "language_priority_list": ["sk", "cs"],
        }

        message = _create_language_priority_message(keep_item)

        assert "overriding quality-based selection" in message
        assert "sk" in message
        assert "sk, cs" in message

    def test_create_language_priority_message_not_changed(self):
        """Test message when language priority used but didn't change selection."""
        keep_item = {
            "selected_by_language_priority": True,
            "changed_by_language_priority": False,
            "priority_language_used": "cs",
            "language_priority_list": ["cs", "sk"],
        }

        message = _create_language_priority_message(keep_item)

        assert "has the best quality" in message
        assert "cs" in message

    def test_create_language_priority_message_empty(self):
        """Test message when language priority not applicable."""
        keep_item = {}

        message = _create_language_priority_message(keep_item)

        assert message == ""

    def test_process_delete_item_success_status(self):
        """Test processing delete item with success status."""
        item = {
            "id": "123",
            "name": "Test Item",
            "deletion_result": {"status": "success"},
            "quality_description": {"video": {"codec": "h264"}},
        }

        result = _process_delete_item(item, "http://emby.local", "server1")

        assert result["id"] == "123"
        assert result["status_class"] == "status-success"
        assert result["status_text"] == "Deleted"
        assert "http://emby.local" in result["url"]

    def test_process_delete_item_failed_status(self):
        """Test processing delete item with failed status."""
        item = {
            "id": "456",
            "name": "Failed Item",
            "deletion_result": {"status": "failed", "error": "Network error"},
            "quality_description": {},
        }

        result = _process_delete_item(item, "http://emby.local", "server1")

        assert result["status_class"] == "status-error"
        assert result["status_text"] == "Failed"
        assert result["error"] == "Network error"

    def test_process_delete_item_pending_status(self):
        """Test processing delete item with pending status."""
        item = {
            "id": "789",
            "name": "Pending Item",
            "deletion_result": {},
            "quality_description": {},
        }

        result = _process_delete_item(item, "http://emby.local", "server1")

        assert result["status_class"] == "status-pending"
        assert result["status_text"] == "Pending"
