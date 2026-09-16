"""Explicit bounded profile for running the compiler as a PixelVM program."""

SELFHOST_HEAP_LIMITS = {
    "max_heap_items": 16_000_000,
    "max_heap_objects": 1_000_000,
}

# Whole-compiler analysis and serialized AST/token output need explicit bounds.
SELFHOST_MAX_STEPS = 1_000_000_000
SELFHOST_MAX_OUTPUT_CHARS = 64_000_000
