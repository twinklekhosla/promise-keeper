"""Masks contact details and numbers locally before any text leaves the machine.

Names stay (the model needs them to know who promised whom); emails, phone numbers,
links and long digit strings (card, account, OTP) are swapped for placeholders.
"""
import re

PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("LINK", re.compile(r"https?://\S+|www\.\S+")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("NUMBER", re.compile(r"(?<!\d)\d{6,}(?!\d)")),
]


class Masker:
    def __init__(self):
        self.to_placeholder = {}
        self.to_original = {}

    def mask(self, text: str) -> str:
        for label, pattern in PATTERNS:
            text = pattern.sub(lambda m, label=label: self._placeholder(label, m.group(0)), text)
        return text

    def unmask(self, text):
        if not isinstance(text, str):
            return text
        for placeholder, original in self.to_original.items():
            text = text.replace(placeholder, original)
        return text

    def _placeholder(self, label, value):
        if value not in self.to_placeholder:
            placeholder = f"<{label}_{len(self.to_placeholder) + 1}>"
            self.to_placeholder[value] = placeholder
            self.to_original[placeholder] = value
        return self.to_placeholder[value]
