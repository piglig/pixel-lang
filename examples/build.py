"""Generate current spatial examples directly from semantic tokens."""

from pathlib import Path

from pixellang.builder import Grid, I
from pixellang.codec import save

ROOT = Path(__file__).parent


def examples():
    addition = (
        Grid()
        .row("entry", "fn", I(99), "(", ")", "->", "unit")
        .row("print", 10, "+", 20, indent=1)
        .build()
    )
    factorial = (
        Grid()
        .row("fn", I(1), "(", I(0), ":", "int", ")", "->", "int")
        .row("if", I(0), "<=", 1, indent=1)
        .row("return", 1, indent=2)
        .row("else", indent=1)
        .row("return", I(0), "*", I(1), "(", I(0), "-", 1, ")", indent=2)
        .row("entry", "fn", I(99), "(", ")", "->", "unit")
        .row("print", I(1), "(", 6, ")", indent=1)
        .build()
    )
    loop = (
        Grid()
        .row("entry", "fn", I(99), "(", ")", "->", "unit")
        .row("var", I(0), ":", "int", "=", 0, indent=1)
        .row("var", I(1), ":", "int", "=", 0, indent=1)
        .row("while", I(0), "<", 5, indent=1)
        .row("set", I(1), "=", I(1), "+", I(0), indent=2)
        .row("set", I(0), "=", I(0), "+", 1, indent=2)
        .row("print", I(1), indent=1)
        .build()
    )
    library = (
        Grid()
        .row("export", "fn", I(2), "(", I(0), ":", "int", ")", "->", "int")
        .row("return", I(0), "*", 2, indent=1)
        .build()
    )
    imported = (
        Grid()
        .row("import", I(7))
        .row("entry", "fn", I(99), "(", ")", "->", "unit")
        .row("print", I(7), ".", I(2), "(", 21, ")", indent=1)
        .build()
    )
    reverse = (
        Grid()
        .row("entry", "fn", I(99), "(", ")", "->", "unit")
        .row("print", 10, "-", 20, indent=1)
        .connect("next", (2, 2), (8, 2))
        .connect("next", (8, 2), (6, 2))
        .connect("next", (6, 2), (4, 2))
        .build()
    )
    branch = (
        Grid()
        .row("entry", "fn", I(99), "(", ")", "->", "unit")
        .row("if", True, indent=1)
        .row("print", 42, indent=2)
        .row("else", indent=1)
        .row("print", 0, indent=2)
        .build()
    )
    return {
        "addition": addition,
        "factorial": factorial,
        "loop": loop,
        "7": library,
        "modules": imported,
        "connections": reverse,
        "branch": branch,
    }


if __name__ == "__main__":
    for name, doc in examples().items():
        save(doc, ROOT / f"{name}.pixel")
    save(examples()["addition"], ROOT / "addition.png")
    save(examples()["connections"], ROOT / "connections.png")
