"""Autonomous multilingual whiteboard animation engine."""

__version__ = "1.0.0"

LANGUAGE_KEYS = ("english", "urdu", "roman_urdu", "pashto")

# Canonical language codes used throughout the pipeline.
LANGUAGE_CODES = {
    "english": "en",
    "urdu": "ur",
    "roman_urdu": "roman_urdu",
    "pashto": "ps",
}

RTL_CODES = {"ur", "ps"}
