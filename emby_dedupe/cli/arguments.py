"""
Command-line argument parsing for the Emby Dedupe tool.
"""

import os

from emby_dedupe.utils.logging import logger


def get_env_variable(name: str) -> str | None:
    """Get the value of an environment variable.

    Args:
        name (str): The name of the environment variable to retrieve.

    Returns:
        Optional[str]: The value of the environment variable, if it exists.
    """
    return os.environ.get(name)


def override_warning(arg_name: str, cmd_val: str | None, env_val: str | None) -> None:
    """Print a warning if a command-line argument overrides an environment variable.

    Args:
        arg_name (str): The name of the argument being overridden.
        cmd_val (str): The value from the command line.
        env_val (str): The value from the environment variable.
    """
    if cmd_val and env_val:
        logger.warning(
            f"Warning: The command-line argument {arg_name} ('{cmd_val}') "
            f"overrides the environment variable ('{env_val}')."
        )


def validate_required_arguments(
    host: str | None,
    api_key: str | None,
    libraries: list,
    doit: bool,
    username: str | None = None,
    password: str | None = None,
) -> None:
    """Validate that required arguments are provided.

    Args:
        host (Optional[str]): The host of the Emby server.
        api_key (Optional[str]): The API key for the Emby server.
        libraries (list): A list of libraries to scan for duplicates.
        doit (bool): True if the script will perform deletions.
        username (Optional[str], optional): The username to use for authentication. Defaults to None.
        password (Optional[str], optional): The password to use for authentication. Defaults to None.
    """
    missing_args = []

    for arg, value in {
        "host": host,
        "api-key": api_key,
    }.items():
        if not value:
            missing_args.append(arg)

    if not libraries:
        missing_args.append("library")

    # Check for username and password if deletions will be performed
    if doit:
        if not username:
            missing_args.append("username")
        if not password:
            missing_args.append("password")

    if missing_args:
        missing_args_str = ", ".join(missing_args)
        logger.error(f"Error: Missing required arguments: {missing_args_str}")
        logger.error("Use -h for help.")
        import sys
        sys.exit(1)
