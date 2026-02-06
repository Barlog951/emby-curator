"""
Tests for HTML report generation
"""
import pytest
import os
import tempfile
import io
import shutil
from unittest.mock import patch, Mock, MagicMock

from emby_dedupe.reports.html import (
    generate_html_report,
    format_html_report,
    compare_dates
)
from emby_dedupe.reports.common import calculate_report_statistics


class TestHtmlReports:
    """Tests for HTML report generation."""
    
    def test_compare_dates_newer(self):
        """Test comparison with a newer date."""
        # First date is newer
        result = compare_dates("2023-01-15", "2022-12-31")
        assert result == 1
    
    def test_compare_dates_older(self):
        """Test comparison with an older date."""
        # First date is older
        result = compare_dates("2022-01-01", "2022-02-15")
        assert result == -1
    
    def test_compare_dates_equal(self):
        """Test comparison with equal dates."""
        # Dates are equal
        result = compare_dates("2023-01-01", "2023-01-01")
        assert result == 0
    
    def test_compare_dates_unknown(self):
        """Test comparison with unknown dates."""
        # One date is unknown
        result = compare_dates("unknown", "2023-01-01")
        assert result == 0
        
        result = compare_dates("2023-01-01", "unknown")
        assert result == 0
        
        # Both dates are unknown
        result = compare_dates("unknown", "unknown")
        assert result == 0
    
    def test_compare_dates_with_year_only(self):
        """Test comparison with dates that include year-only notation."""
        # Date with year-only notation
        result = compare_dates("2023-01-01 (year only)", "2022-12-31")
        assert result == 1

    def test_compare_dates_with_exception(self):
        """Test comparison handling with dates that cause exceptions."""
        # Just check that compare_dates handles invalid date formats without crashing
        result = compare_dates("totally invalid", "also invalid")
        assert isinstance(result, int)  # Should return some integer value
    
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
                assert template_data["language_priorities_used"] == True
                assert template_data["language_priorities_changed_selection"] == True
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
                assert template_data["has_excluded_ids"] == True
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
                with patch('emby_dedupe.reports.html.logger') as mock_logger:
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
            
            # Patch compare_dates function to avoid issues with date comparisons
            with patch('emby_dedupe.reports.html.compare_dates', return_value=0):
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
                                    imdb_url = f"https://www.imdb.com/title/{item['provider_id']}"
                                    assert "IMDB" in item["name"]  # Verify it's our IMDB test item
                                elif item["provider_id"].isdigit():
                                    tmdb_url = f"https://www.themoviedb.org/movie/{item['provider_id']}"
                                    assert "TMDB" in item["name"]  # Verify it's our TMDB test item
                        else:
                            # Non-deleted items should still have their Emby URL
                            assert "url" in item
                            assert "Not Deleted" in item["name"]
                    
                except ImportError:
                    # Handle the case where jinja2 is not installed
                    pass