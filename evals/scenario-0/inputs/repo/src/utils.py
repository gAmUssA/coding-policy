"""Utility functions for the data pipeline."""


def normalize_email(email: str) -> str:
    """Return the email address in lowercase with whitespace stripped."""
    return email.strip().lower()


def format_currency(amount: float, symbol: str = "$") -> str:
    """Return a formatted currency string, e.g. '$12.50'."""
    return f"{symbol}{amount:.2f}"


def greet_user(name: str, title: str = "") -> str:
    """Return a personalised greeting string for the given name."""
    if title:
        return f"Hello, {title} {name}!"
    return f"Hello, {name}!"
