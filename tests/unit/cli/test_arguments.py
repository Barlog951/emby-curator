"""
Tests for CLI argument parsing
"""
import os
import pytest
from unittest.mock import patch
from argparse import Namespace

from emby_dedupe.cli.arguments import (
    parse_args,
    get_env_variable,
    override_warning,
    validate_required_arguments
)


class TestArguments:
    """Tests for CLI argument parsing."""

    def test_parse_args_defaults(self):
        """Test parsing arguments with defaults."""
        with patch('sys.argv', ['emby-dedupe']):
            args = parse_args()
            assert args.verbosity == 0
            assert args.host is None
            assert args.port is None
            assert args.api_key is None
            assert args.library is None
            assert args.doit is False
            assert args.username is None
            assert args.password is None

    def test_parse_args_with_values(self):
        """Test parsing arguments with provided values."""
        with patch('sys.argv', [
            'emby-dedupe',
            '--host', 'emby.example.com',
            '--port', '8096',
            '--api-key', 'api_key_value',
            '--library', 'Movies',
            '--doit',
            '--username', 'user',
            '--password', 'pass',
            '-v'
        ]):
            args = parse_args()
            assert args.verbosity == 1
            assert args.host == 'emby.example.com'
            assert args.port == 8096
            assert args.api_key == 'api_key_value'
            # In the current implementation, library is a list to support multiple libraries
            assert args.library == ['Movies']
            assert args.doit is True
            assert args.username == 'user'
            assert args.password == 'pass'

    def test_get_env_variable(self):
        """Test getting environment variables."""
        # Test with a non-existent variable
        assert get_env_variable('NONEXISTENT_VAR') is None
        
        # Test with an existing variable
        with patch.dict('os.environ', {'TEST_VAR': 'test_value'}):
            assert get_env_variable('TEST_VAR') == 'test_value'

    def test_override_warning(self):
        """Test warning when command-line args override env vars."""
        # Testing override_warning logic directly
        # No warning if no env value - nothing should happen
        result1 = override_warning('--host', 'cmd_value', None)
        assert result1 is None
        
        # No warning if no cmd value - nothing should happen
        result2 = override_warning('--host', None, 'env_value')
        assert result2 is None
        
        # For both values present we're relying on logger.warning being called
        # which is difficult to test without complex mocking
        # Just verifying the if condition is met
        with patch('emby_dedupe.cli.arguments.logger') as mock_logger:
            override_warning('--host', 'cmd_value', 'env_value')
            assert mock_logger.warning.called

    def test_validate_required_arguments_all_valid(self):
        """Test validation with all required arguments."""
        # Without doit
        validate_required_arguments(
            host='emby.example.com',
            api_key='api_key',
            libraries=['Movies'],
            doit=False
        )
        
        # With doit and credentials
        validate_required_arguments(
            host='emby.example.com',
            api_key='api_key',
            libraries=['Movies'],
            doit=True,
            username='user',
            password='pass'
        )

    def test_validate_required_arguments_missing(self):
        """Test validation with missing required arguments."""
        # Missing host
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host=None,
                api_key='api_key',
                libraries=['Movies'],
                doit=False
            )
        
        # Missing API key
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host='emby.example.com',
                api_key=None,
                libraries=['Movies'],
                doit=False
            )
        
        # Missing libraries
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host='emby.example.com',
                api_key='api_key',
                libraries=[],
                doit=False
            )
        
        # With doit but missing username
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host='emby.example.com',
                api_key='api_key',
                libraries=['Movies'],
                doit=True,
                username=None,
                password='pass'
            )
        
        # With doit but missing password
        with pytest.raises(SystemExit):
            validate_required_arguments(
                host='emby.example.com',
                api_key='api_key',
                libraries=['Movies'],
                doit=True,
                username='user',
                password=None
            )