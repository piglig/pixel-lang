# Standard library

[简体中文](../../zh-CN/reference/standard-library.md) · [English](standard-library.md) · [Documentation](../README.md)

Import standard modules through std/ paths. This page indexes APIs by task; exported declarations define exact signatures.

## Contract and interface

| Module | Purpose |
| --- | --- |
| [std/error.pxl](../../../pixellang/stdlib/error.pxl) | Standard errors with stable codes and bounded messages |
| [std/result.pxl](../../../pixellang/stdlib/result.pxl) | Success/error mapping and short-circuit composition for built-in Result |
| [std/collections.pxl](../../../pixellang/stdlib/collections.pxl) | Mapping, filtering, folding, grouping, stable sorting, value membership and stable deduplication |
| [std/cli.pxl](../../../pixellang/stdlib/cli.pxl) | Program arguments and long-option parsing |
| [std/path.pxl](../../../pixellang/stdlib/path.pxl) | Relative path cleaning, joining and filenames |
| [std/csv.pxl](../../../pixellang/stdlib/csv.pxl) | CSV processing |
| [std/io.pxl](../../../pixellang/stdlib/io.pxl) | Granted text-file operations |
| [std/json.pxl](../../../pixellang/stdlib/json.pxl) | Dynamic JSON inspection |
| [std/stats.pxl](../../../pixellang/stdlib/stats.pxl) | Statistics |
| [std/text.pxl](../../../pixellang/stdlib/text.pxl) | Text processing |

This page describes the current standard-library API. Recoverable data operations return Result; optional queries return Option.

## Streaming files and program arguments

`io.ReadChunk(path, offset, size)` reads UTF-8 at a byte offset and returns `Result[Chunk, Error]`, with text, next and done in Chunk. Size is 4–262144 bytes; next stays on a character boundary.
`io.ForEachLine(path, visit)` reads 64 KiB chunks and calls visit with a one-based line number and its text. It handles LF/CRLF/CR across chunk boundaries, preserves a trailing empty line, and limits a line to 262144 characters.
To accumulate state, create `var reader = io.Lines(path)` and call `io.NextLine(&reader)` in a loop.
It returns `Result[Option[io.Line], Error]`; Line contains number and text, Ok(None) marks the end, and Err reports failure.
Reader progress is published only after a successful line or EOF; failure preserves the input reader value.
Creating a reader performs no I/O; `NextLine` requires effect permission. Readers are ordinary
values: copies advance independently. Store accumulated results in explicit local variables
without writable closure captures. `ForEachLine` uses the same reading implementation.
`ForEachLine` and `Walk` return `Result[int, Error]`, counting successfully visited lines or files.
`io.List(path)` returns `Result[[Entry], Error]`, containing regular files and directories sorted by name on success; `io.Walk(path, visit)` recursively visits regular files, skipping symbolic links. An empty path selects the granted root.
Each directory listing allows at most 10000 entries and has a name budget. Reads still require an explicit read-root capability.

`cli.Args()` returns an independent array of program arguments. Pass program arguments after the host delimiter, for example `pixel run . -- --level WARN`, or use repeated `--arg` options such as `--arg=--level`.
`cli.Parse(values, flags, options)` returns `Result[Parsed, Error]` with named options and positional files on success, supporting `--name=value`, `--name value` and the `--` terminator. Unknown or repeated options return Err with stable cli.* codes.
`path.Clean/Join/Base/Dir/Extension` return `Result[string, Error]`, operate on relative POSIX paths and reject root escapes. Cleaning a path does not grant file access.

Ordinary CLI execution retains no read history. Time debugging records observations within a file-effect budget and restores them without disk reads; use ordinary execution for large batches.

## Logical time

[`std/time.pxl`](../../../pixellang/stdlib/time.pxl) provides checked integer durations and fixed-step clocks. See [native applications](../guides/state-machines.md) for contracts and runnable examples.

`Seconds` and `Milliseconds` return `Result[Duration, Error]`;
`At` returns `Result[Step, Error]`. Invalid ranges, intervals and elapsed-time
overflow return errors with stable `time.*` codes. `Nanoseconds` and `InSeconds`
return ordinary values.

## Values and callbacks

Collection algorithms use ordinary generic values and pure `fn` callbacks.
`Filter`, `Unique` and `SortedBy` return `[T]`; `Map` returns `[U]`;
`GroupBy` returns `map[K, [T]]` with `K: Hashable`. `Fold` takes `fn(A, T) -> A` and retains
accumulator type `A`; `Sum` returns `T`. Local sorting, grouping and edits to
repeated values preserve the input.

`Repeat(value, count)` returns `Result[[T], Error]`. Negative counts return
`collection.negative_count`; zero produces an empty array. Repeated elements
have value semantics, so changing one element preserves the others.

Use `table.get(key)` for optional lookup. Assignment preserves value isolation; no explicit collection copy is required.
`collections.Increment` takes `inout map[K, int]` with `K: Hashable`, called as `collections.Increment(&counts, key)`.

`result.Map`, `MapError` and `AndThen` preserve or transform the `Result` type
parameters directly. External effects use `effect fn`; `io.ForEachLine` and
`io.Walk` accept `effect fn` callbacks.

Release evidence must bind current sources and artifacts; see [verification](../contributing/verification.md).

## Data processing results

`csv.Parse` returns `Result[[[string]], Error]`; `csv.Encode` returns `Result[string, Error]`.
CSV errors provide stable csv.* codes and character offsets (the empty-row encoding error uses a row index).
JSON parsing, field/index access and shape checks return Result; deterministic construction, Kind and Stringify return ordinary values.
`stats.Summary` returns `Option[map[string, int]]`, with None for empty input.

## Further reading

- [Documentation index](../README.md)
