"""
Logging configuration for the RAG Chatbot API.

This module provides a centralized logger that can be imported and used
across the entire application. Using one shared logger ensures consistent
formatting and makes it easy to change logging behavior in a single place.

Why logging matters:
    - Track every request and response for debugging
    - Detect errors with full context (timestamp, file, line number)
    - Provide an audit trail for the API's behavior during demos
    - Industry-standard practice for production-ready APIs
"""

import logging
import sys


def setup_logger(name: str = "rag_chatbot") -> logging.Logger:
    """
    Create and configure a logger instance for the application.

    Args:
        name: The name of the logger. Defaults to "rag_chatbot".
              Different names allow grouping logs by module if needed.

    Returns:
        A configured logging.Logger instance ready to use.

    Example:
        >>> logger = setup_logger()
        >>> logger.info("Server started")
        2026-05-16 10:30:45 | INFO | rag_chatbot | Server started
    """
    # Get (or create) a logger with the given name
    logger = logging.getLogger(name)

    # Set the minimum level: everything from INFO upward will be shown
    # (DEBUG messages will be hidden — useful in development but noisy)
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers if setup_logger is called multiple times
    # (this can happen with FastAPI's auto-reload during development)
    if logger.handlers:
        return logger

    # Create a handler that writes log messages to the console (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Define the format of each log line:
    # Example: 2026-05-16 10:30:45 | INFO | rag_chatbot | Server started
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)

    # Attach the handler to the logger
    logger.addHandler(console_handler)

    return logger


# Create a single shared logger instance that other modules can import directly.
# This is the "singleton pattern" — one shared object used everywhere.
logger = setup_logger()