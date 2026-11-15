"""Day-one placeholder logic so CI has real code to test. Replace with your first feature."""


def greet(name: str) -> str:
    """Return a greeting for ``name``, trimmed.

    Raises:
        ValueError: ``name`` is empty or only whitespace.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("name must not be blank")
    return f"Hello, {cleaned}!"
