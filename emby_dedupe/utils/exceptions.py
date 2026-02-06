"""
Custom exceptions for the Emby Dedupe tool.
"""


class EmbyServerConnectionError(Exception):
    """
    Exception raised when there is an issue connecting to the Emby server.
    This could be due to invalid credentials, network issues, or server problems.
    """
    pass
