# Structured recoverable errors

[中文](../../../zh-CN/reference/language/errors.md) · [English](errors.md) · [Documentation](../../README.md)

This page defines recoverable errors. Absence is represented by `Option[T]`;
see [enums and match](enums.md).

catch binds an immutable Error with read-only string fields:

| Field | Meaning |
| --- | --- |
| kind | Category: json, io, numeric, collection, text, value or user |
| code | Stable dotted code used for program decisions |
| message | Human-readable diagnostic; wording is not a branching contract |
| path | JSON path, requested relative file path, collection key/index, or empty |
| operation | Operation that failed, e.g. jsonDecode, read, write or parseInt |
| expected | Expected type/value description when applicable, otherwise empty |
| actual | Actual type/value description when applicable, otherwise empty |

JSON codes include json.invalid_syntax, json.duplicate_key, json.type_mismatch,
json.missing_field, json.unknown_field, json.numeric_range, json.missing_key,
json.index_bounds, json.structure_limit and json.cycle. JSON paths start at $;
identifier fields use .name, other object keys use ["quoted.key"], arrays use [i].
Missing-field paths identify the absent field. JSON parsing failures use $ when
called through jsonDecode. Context survives nested conversion and is attached once.

File codes include io.access_denied, io.not_found, io.invalid_path, io.invalid_file,
io.too_large, io.invalid_encoding, io.no_space, io.unsupported_host and io.failure.
Classification uses explicit capability/path checks and OS error codes, not message
matching. File observations record the structured error and replay it without
re-accessing the filesystem, even when the external state has changed.

Other codes include numeric.invalid_text, numeric.range, numeric.division_by_zero,
collection.missing_key, collection.duplicate_key, collection.index_bounds,
collection.empty, text.empty_separator, value.cycle and value.depth. fail(message)
raises user.failure. All retain the same try/catch control-flow model.

Error message, expected and actual are bounded to 256 characters; path is bounded
to 1024 and operation to 64. File recording budgets account for structured error
payloads. Assertions, arithmetic overflow, VM limits and cancellation remain fatal.
An exception's diagnostic phase is separate from the Error.kind category.
