# Order fulfillment

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Follow two orders through payment, stock reservation and shipping. Orders and a depot are entities in one immutable space; typed links record reserved quantities. A submitted batch either publishes its final snapshot or preserves the previous snapshot entirely.

Complete [installation](../../docs/en/guides/installation.md), then run these commands from the repository root:

```sh
pixel run examples/order-fulfillment
pixel test examples/order-fulfillment
```

## Follow the orders

The depot starts with three items. Each order needs two.

| Submission | Result | Available / reserved |
| --- | --- | --- |
| Create A-100 and B-200 | Both pending | 3 / 0 |
| Pay both in one batch | Insufficient stock; neither payment applied | 3 / 0 |
| Pay A-100 | Paid, reservation created | 1 / 2 |
| Ship A-100 without tracking | Rejected; snapshot preserved | 1 / 2 |
| Ship A-100 with tracking | Shipped, reservation removed | 1 / 0 |
| Cancel shipped A-100 | Rejected; snapshot preserved | 1 / 0 |

The program then reads the saved payment snapshot: A-100 is still paid and its reservation still exists. Shipping moves the current order to physical coordinates `(10, 0, 2)` meters. Coordinates describe location; business state lives in the order payload.

Cancelling a paid order returns its reserved stock. Cancelling a pending order needs no stock adjustment. Tests also cover duplicate orders, repeated payment, invalid quantities, independent flow instances and rollback of a partially evaluated batch.

## Replay events

```sh
pixel application examples/order-fulfillment/application.pxl --events examples/order-fulfillment/events.json --steps 6 --record /tmp/fulfillment.pxevents
pixel application examples/order-fulfillment/application.pxl --replay /tmp/fulfillment.pxevents --steps 0
```

In VS Code, run **PixelLang: Open Native Application** and select `application.pxl` to use the event application. Each `Submit` carries an array of commands. A business rejection is recorded and consumes its event, while retaining the space snapshot. An execution fault instead rolls back the step and leaves the event pending.

## Read the implementation

- [model.pxl](model.pxl): orders, commands, outcomes and typed reservation data.
- [operations.pxl](operations.pxl): validated transitions, stock queries and batch evaluation.
- [workflow.pxl](workflow.pxl): publishes successful snapshots and keeps the previous one on rejection.
- [main.pxl](main.pxl): a runnable walkthrough with saved historical snapshots.
- [tests/fulfillment_test.pxl](tests/fulfillment_test.pxl): business invariants and rejection cases.

Order keys contain 1–244 characters. Internal prefixes separate order entities and reservation links from the depot. Typed identities belong to the space; snapshots of that space retain the same identities. `Open` requires nonnegative stock. `Find` returns an optional order; `Available` and `Reserved` report stock, with reservations obtained through outgoing links from the depot.

Batch atomicity here means publishing only the final immutable value. It does not provide an optimized storage transaction: intermediate updates still copy spatial data. The example models payment and shipment without external side effects; real integrations need their own idempotency and delivery protocol.

## Measure development costs

```sh
python scripts/benchmark_order_fulfillment.py --sizes 50 150 450 --trials 3 --output /tmp/fulfillment-benchmark.json
```

The benchmark reports compiler metrics for a single function-body edit separately from VM execution of increasing batch sizes. It includes source and compiler hashes. Spatial allocation counts are accounting units, not retained memory or process bytes. Use consistent power and machine conditions when comparing runs.

The Python regression suite additionally verifies PNG export/recovery and recorded history:

```sh
python -m unittest tests.test_order_fulfillment -q
```
