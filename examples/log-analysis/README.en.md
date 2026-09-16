# Multifile log analysis

[中文](README.md) · [English](README.en.md)

Five PixelLang files decode configuration, validate calendars, process rows, filter
time windows, group and compute statistics. Input is JSON Lines: each nonempty physical
line is a record. Blank lines are ignored; data errors are reported and processing
continues. Read failures use line 0 and processing continues with the next file.

```sh
.venv/bin/pixel test examples/log-analysis
.venv/bin/pixel build examples/log-analysis
mkdir -p /tmp/pixel-log-output
.venv/bin/pixel run examples/log-analysis/dist/program.png \
  --input examples/log-analysis/fixtures/config.json \
  --read-root examples/log-analysis/fixtures \
  --write-root /tmp/pixel-log-output
```

Configuration requires files (relative paths) and output (relative output path).
since/until may be absent/null for unbounded sides; supplied values must be valid
timestamps. The window includes since and excludes until. Optional minimum_level
accepts INFO, WARN or ERROR, keeping that level and higher; invalid configuration
fails before file reads. Configuration/events use jsonDecode[T], rejecting missing
required or extra fields.

```json
{"timestamp":"2026-09-09T10:00:00Z","level":"INFO","service":"api","duration_ms":10,"message":"Request complete"}
```

Timestamps require fixed-width UTC YYYY-MM-DDTHH:MM:SSZ, Gregorian leap-year validation
and years 0001–9999. Fractional seconds, offsets and leap seconds are unsupported.
Levels are INFO/WARN/ERROR, duration is nonnegative int64 and service is nonempty.
Validation precedes time/level filtering, so invalid excluded rows still become
problems. LF, CRLF and CR are supported with physical line numbers preserved.

Reports include event/error/filtered counts, duration total/float64 average/maximum,
and counts by service, level and minute. problems retain filename, one-based physical
line, message, stable code and path. Generic stable sorting orders problems by file
and line; read failures use 0. Sum overflow and resource limits stop execution.
Files follow configuration order; duplicates are counted repeatedly.

Fixtures supply independent mixed/empty expectations. tests/test_log_analysis.py
uses Python datetime and Counter as independent generated-data oracles, checks
century leap years and verifies replay without repeated writes.
