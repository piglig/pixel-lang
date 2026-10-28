"""Independent token oracle for native lexer tests; never a compiler fallback.

Preserves the host scanner's independently implemented string, number, comment,
and location rules while recognizing the current native punctuation alphabet.
"""
import json
import math
import re
from pixellang.text import Lexeme

def lex(source):
    text = source.text
    tokens = []
    i = 0
    while i < len(text):
        start = i
        c = text[i]
        if c in " \t\r":
            i += 1
            continue
        if c == "\n":
            tokens.append(Lexeme("separator", "\n", i, i + 1))
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                source.error("Unterminated block comment", i, len(text))
            # A multiline comment preserves a statement boundary.
            if "\n" in text[i : end + 2]:
                tokens.append(Lexeme("separator", "\n", i, end + 2))
            i = end + 2
            continue
        if c == '"':
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    break
                if text[i] == "\n":
                    source.error("Use \\n inside a quoted string", start, i)
                i += 1
            if i >= len(text):
                source.error("Unterminated string", start, len(text))
            i += 1
            try:
                value = json.loads(text[start:i])
                value.encode("utf-8")
            except (ValueError, UnicodeError):
                source.error("Invalid string escape or Unicode scalar", start, i)
            tokens.append(Lexeme("str", value, start, i))
            continue
        if c.isdigit() and c.isascii():
            i += 1
            while i < len(text) and (
                text[i].isdigit() and text[i].isascii() or text[i] == "_"
            ):
                i += 1
            raw = text[start:i]
            if (
                i < len(text)
                and text[i] == "."
                and i + 1 < len(text)
                and text[i + 1].isascii()
                and text[i + 1].isdigit()
            ):
                i += 1
                while i < len(text) and (text[i] in "0123456789_"):
                    i += 1
            if i < len(text) and text[i] in "eE":
                i += 1
                if i < len(text) and text[i] in "+-":
                    i += 1
                while i < len(text) and text[i] in "0123456789_":
                    i += 1
            number = text[start:i]
            if number != raw:
                digits = r"[0-9]+(?:_[0-9]+)*"
                if not re.fullmatch(
                    digits + r"(?:\." + digits + r")?(?:[eE][+-]?" + digits + r")?",
                    number,
                ):
                    source.error("Invalid float64 literal", start, i)
                value = float(number.replace("_", ""))
                if not math.isfinite(value):
                    source.error("Float64 literal exceeds finite range", start, i)
                tokens.append(Lexeme("float64", value, start, i))
                continue
            if not re.fullmatch(r"[0-9]+(?:_[0-9]+)*", raw):
                source.error("Invalid integer separator", start, i)
            if len(raw.replace("_", "")) > 20:
                source.error("Integer literal exceeds signed 64-bit range", start, i)
            tokens.append(Lexeme("int", int(raw.replace("_", "")), start, i))
            continue
        if c == "_" or c.isalpha():
            i += 1
            while i < len(text) and (text[i] == "_" or text[i].isalnum()):
                i += 1
            tokens.append(Lexeme("name", text[start:i], start, i))
            continue
        pair = text[i : i + 2]
        if pair in (
            "::",
            "=>",
            "->",
            ":=",
            "==",
            "!=",
            "<=",
            ">=",
            "&&",
            "||",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
        ):
            tokens.append(Lexeme("symbol", pair, i, i + 2))
            i += 2
            continue
        if c in "(){}[],:;.+-*/%<>=!&?":
            tokens.append(Lexeme("symbol", c, i, i + 1))
            i += 1
            continue
        source.error(f"Unexpected character {c!r}", i)
    tokens.append(Lexeme("eof", "<eof>", len(text), len(text)))
    return tokens
