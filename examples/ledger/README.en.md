# Ledger — a PixelLang data-processing project

[中文](README.md) · [English](README.en.md)

Configuration, CSV parsing, row validation, filtering, grouping, statistics, sorting
and report generation are implemented in PixelLang. The host supplies general JSON
operations, granted file I/O and the language runtime. From the repository root:

```sh
uv sync --locked
.venv/bin/pixel test examples/ledger
.venv/bin/pixel build examples/ledger
mkdir -p /tmp/ledger-output
.venv/bin/pixel run examples/ledger/dist/program.png \
  --input examples/ledger/fixtures/config.json \
  --read-root examples/ledger/fixtures \
  --write-root /tmp/ledger-output
```

The report is written to /tmp/ledger-output/report.json and printed. The PNG contains
the application and standard library, so original .pxl files are unnecessary; runtime
and granted data directories are still required.

## Input and rules

CSV headers are category,amount_minor. Amounts use integer minor currency units in
±1,000,000,000,000. Categories are trimmed; empty categories and invalid integers are
rejected rows. Logical CSV records determine error row numbers, with the header at 1;
quoted newlines do not increment record count.

Configuration requires source and output. categories and minimum_minor may be absent
or null, defaulting to all categories and 0:

```json
{"source":"sales.csv","output":"report.json","categories":[],"minimum_minor":0}
```

source/output are relative to read/write grant roots. Empty categories selects all;
minimum_minor is inclusive. Validate before filtering: invalid rows in excluded
categories still count as errors. Optional maximum_minor is inclusive; absent/null
means unbounded. An upper bound below the lower bound fails before reading CSV.
For example minimum_minor: 100 and maximum_minor: 500 accepts 100 through 500.

Reports contain accepted, rejected, filtered, groups and errors. Groups sort by total
descending, then category Unicode order ascending. Each group has count, total,
minimum, maximum and float64 average in minor units. Amounts, totals and thresholds
remain integers. Empty/header-only files yield empty reports. CSV syntax, header,
configuration and access errors stop the task; row data errors allow later rows.

## Fixtures and Studio

- bounded-config.json selects the inclusive range 2..3, accepting both endpoints and filtering three rows, checked against independent expectations.
- sales.csv includes negatives, empty fields, invalid/out-of-range numbers, column errors, quoted commas and multiline categories: 6 accepted, 5 rejected, 2 filtered.
- changed.csv changes inputs and includes tied totals: 5 accepted, no errors.
- empty.csv expects an empty report.

Each case independently checks printed results and file bytes in a temporary output
directory. tests/test_ledger.py additionally checks filters and replay. clean_install.py
tests/builds the wheel, then removes application/library source before using images
with fresh input.

In VS Code open this directory, set pixellang.readRoot to fixtures and writeRoot to
an existing report directory. Supply the JSON in Studio Runtime input or F5 launch
input. Python needs the runtime dependencies. Optional configuration uses Option[T];
collections.Sort uses an anonymous comparator with inferred Group type. Float averages
are for presentation, not integer monetary settlement.
