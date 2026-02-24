"""Receipt template parser and placeholder substitution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_VARIABLE_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


@dataclass
class TemplateElement:
    """Single parsed template element."""

    type: str
    text: str = ""
    align: str = "left"
    style: str = "normal"
    cond_key: str | None = None


def substitute(text: str, context: dict[str, object]) -> str:
    """Substitute {{key}} placeholders with context values."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key, "")
        return "" if value is None else str(value)

    return _VARIABLE_PATTERN.sub(replace, text)


def load_template(path: str) -> list[TemplateElement]:
    """Load and parse template DSL from disk."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    elements: list[TemplateElement] = []
    condition_stack: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped == "":
            elements.append(
                TemplateElement(
                    type="text",
                    text="",
                    cond_key=condition_stack[-1] if condition_stack else None,
                )
            )
            continue

        if stripped.startswith("[[IF ") and stripped.endswith("]]"):
            key = stripped[5:-2].strip()
            if key:
                condition_stack.append(key)
            continue

        if stripped == "[[ENDIF]]":
            if condition_stack:
                condition_stack.pop()
            continue

        cond_key = condition_stack[-1] if condition_stack else None

        if stripped == "[[HR]]":
            elements.append(TemplateElement(type="hr", cond_key=cond_key))
            continue

        if stripped.startswith("[[QR]]"):
            elements.append(
                TemplateElement(
                    type="qr",
                    text=stripped[len("[[QR]]") :].strip(),
                    cond_key=cond_key,
                )
            )
            continue

        if stripped.startswith("[[IMG]]"):
            elements.append(
                TemplateElement(
                    type="img",
                    text=stripped[len("[[IMG]]") :].strip(),
                    cond_key=cond_key,
                )
            )
            continue

        align = "left"
        style = "normal"
        text = stripped

        if stripped.startswith("[[") and "]]" in stripped:
            tag = stripped[2 : stripped.index("]]")]
            body = stripped[stripped.index("]]") + 2 :].lstrip()
            tokens = [token.strip().lower() for token in tag.split("|")]
            if "center" in tokens:
                align = "center"
            if "right" in tokens:
                align = "right"
            if "left" in tokens:
                align = "left"
            if "b" in tokens or "bold" in tokens:
                style = "bold"
            if "title" in tokens:
                style = "title"
            text = body

        elements.append(
            TemplateElement(
                type="text",
                text=text,
                align=align,
                style=style,
                cond_key=cond_key,
            )
        )

    return elements
