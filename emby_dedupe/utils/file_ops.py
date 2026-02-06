"""
File operation utilities for the Emby Dedupe tool.
"""

import json
import os
from typing import Any, Optional

from emby_dedupe.utils.logging import logger


def truncate_string(value: str, max_length: int) -> str:
    """
    Truncates a string to a maximum number of characters, adding an ellipsis if needed.

    Args:
        value (str): The string to truncate.
        max_length (int): The maximum allowed length of the string.

    Returns:
        str: The truncated string with ellipsis if the original was longer than max_length.
    """
    return (value[: max_length - 1] + "…") if len(value) > max_length else value


def dump_object_to_file(obj: Any, base_filename: str) -> None:
    """
    Attempts to serialize and save an object to a file with the specified base filename.
    The base filename can include a relative or absolute path. The directory will be created
    if it doesn't exist. It saves objects as:
        - Pretty JSON if the object is a dictionary or list.
        - Plain text if it's a string.
        - Binary if the object is bytes.

    Args:
        obj (Any): The object to be saved to a file.
        base_filename (str): The base name for the file to save to, which can include a path.

    Raises:
        ValueError: If the object type cannot be determined or handled by the function.
    """
    # Extract directory path from base filename
    directory = os.path.dirname(base_filename)
    if directory:  # If the path is not empty
        # Ensure that the directory exists
        os.makedirs(directory, exist_ok=True)

    # Construct full file paths with appropriate extension
    json_path = f"{base_filename}.json"
    text_path = f"{base_filename}.txt"
    bin_path = f"{base_filename}.bin"

    # Check if the object is serializable to JSON (dict or list)
    if isinstance(obj, (dict, list)):
        try:
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(obj, json_file, indent=4)
            logger.debug(f"Object saved as JSON to {json_path}")
            return
        except TypeError as e:
            logger.error(f"Failed to serialize object to JSON: {e}")
            # Fall through to other types if JSON serialization fails

    # Check if it's text data (string)
    if isinstance(obj, str):
        with open(text_path, "w", encoding="utf-8") as text_file:
            text_file.write(obj)
        logger.debug(f"Text object saved to {text_path}")
        return

    # Check if it's binary data (bytes)
    if isinstance(obj, bytes):
        with open(bin_path, "wb") as bin_file:
            bin_file.write(obj)
        logger.debug(f"Binary object saved to {bin_path}")
        return

    # If none of the above, raise an error
    raise ValueError("Unsupported object type for dumping to a file.")


def read_json_file(file_path: str) -> Optional[Any]:
    """
    Attempts to read a JSON file and return its contents as a Python object.

    Args:
        file_path (str): Path to the JSON file to be read.

    Returns:
        Optional[Any]: Parsed JSON data if the file is successfully read and parsed, None otherwise.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except json.JSONDecodeError as exc:
        logger.error(f"Error parsing JSON file at {file_path}: {exc}")
    except Exception as exc:
        logger.error(
            f"An unexpected error occurred while reading file {file_path}: {exc}"
        )

    return None
