# Native order state machine

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Run this example with the current toolchain. Complete [installation](../../docs/en/guides/installation.md); commands below run from the repository root.

Typed events, enum states, pure functions and native `flow` cells implement every transition in PixelVM. The transition function directly matches `(previous.status, event)`; guards check amount, deadline and tracking details, and the final arm explicitly rejects invalid transitions.

## Run and replay

```sh
pixel application examples/native-orders/main.pxl --events examples/native-orders/events.json --steps 5 --record /tmp/orders.pxevents
pixel application examples/native-orders/main.pxl --replay /tmp/orders.pxevents --steps 0
```

The sequence is Tick 0, Tick 1, payment, Tick 2, shipment. Payment must match 1299 cents; shipping requires payment and a nonempty tracking number. Unpaid orders expire at logical time 3 seconds. A business rejection returns a typed Err and preserves the order, but consumes and records the event so later inputs can proceed. An execution fault rolls back the step and retains the pending event.

Run **PixelLang: Open Native Application** in VS Code and select `main.pxl`. Step once to initialize, then choose an event and enter its fields. Seek history, save recordings, and use **Debug event** to inspect a selected transition in source.

Output is `Decision{order, outcome}`: order is the state after this step, and
outcome is `Result[Status, Rejection]`. Rejections include
`AmountMismatch(expected, actual)`, `PaymentExpired`,
`InvalidTransition(status, event)` and `MissingTracking`. Callers can exhaustively
match error variants without parsing messages. Ticks also produce successful
results, giving each historical event a corresponding output.

The example simulates order state and does not perform real payment or shipment.
External operations require a separate command execution, idempotency and result
feedback protocol.

Initial order data is supplied through `Order(initial)`, rather than embedded in the flow declaration. Order identity, amount and deadline survive state transitions. Run `pixel run examples/native-orders/instances.pxl` for orders A and B with different amounts and deadlines. Paying B with A's amount is rejected without affecting A.
