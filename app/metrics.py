"""Prometheus metrics for the event pipeline.

All metrics are defined here in one place so it's easy to see what's
being tracked and to grep for usage.  Each pipeline component imports
the metrics it needs directly::

    from app.metrics import OUTBOX_EMIT_DURATION, OUTBOX_EMIT_TOTAL

Metric naming follows Prometheus conventions:
- ``_total`` for counters
- ``_seconds`` for histograms/summaries measuring duration
- ``_ratio`` / ``_bytes`` etc. for gauges with units

Exposed via ``GET /metrics`` (see ``app/views/metrics.py``).
"""

from prometheus_client import Counter, Gauge, Histogram


# ──────────────────────────────────────────────────────────────────────
# Outbox emit (app/events/outbox.py)
# Answers: How fast are we writing events? Is the DB insert a bottleneck?
# ──────────────────────────────────────────────────────────────────────

OUTBOX_EMIT_TOTAL = Counter(
    "outbox_emit_total",
    "Total events written to the outbox",
    ["event_type", "source"],
)

OUTBOX_EMIT_DURATION = Histogram(
    "outbox_emit_duration_seconds",
    "Time to INSERT an event into the outbox (excludes caller's commit)",
    ["event_type"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# ──────────────────────────────────────────────────────────────────────
# Outbox relay (app/events/relay.py)
# Answers: Where is the relay spending time? Is it the SELECT, the
# publishes, or the commit?  How big are the batches?  Is the relay
# keeping up with the emit rate?
# ──────────────────────────────────────────────────────────────────────

RELAY_BATCH_TOTAL = Counter(
    "relay_batch_total",
    "Total relay batches executed (including empty polls)",
)

RELAY_BATCH_DURATION = Histogram(
    "relay_batch_duration_seconds",
    "Total wall time per relay batch (SELECT + publish + UPDATE + COMMIT)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

RELAY_BATCH_SIZE = Histogram(
    "relay_batch_size",
    "Number of events relayed per batch (0 = empty poll)",
    buckets=(0, 1, 5, 10, 25, 50, 100, 200, 500),
)

RELAY_EVENTS_TOTAL = Counter(
    "relay_events_total",
    "Total events successfully relayed to the broker",
)

RELAY_PUBLISH_DURATION = Histogram(
    "relay_publish_duration_seconds",
    "Time for asyncio.gather of all publishes in a batch",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

RELAY_PUBLISH_ERRORS = Counter(
    "relay_publish_errors_total",
    "Publish failures within a relay batch (events left for retry)",
)

RELAY_COMMIT_DURATION = Histogram(
    "relay_commit_duration_seconds",
    "Time to UPDATE relayed_at + COMMIT (the shielded critical section)",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)

RELAY_PENDING_GAUGE = Gauge(
    "relay_outbox_pending",
    "Approximate number of un-relayed events in the outbox "
    "(sampled each batch, not continuously polled)",
)

# ──────────────────────────────────────────────────────────────────────
# Broker / RabbitMQ (app/events/broker.py)
# Answers: How long does a single publish take?  Is the AMQP channel
# the bottleneck?
# ──────────────────────────────────────────────────────────────────────

BROKER_PUBLISH_DURATION = Histogram(
    "broker_publish_duration_seconds",
    "Time for a single broker.publish() call (serialize + AMQP write)",
    ["event_type"],
    buckets=(0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)

BROKER_PUBLISH_TOTAL = Counter(
    "broker_publish_total",
    "Total messages published to the broker",
    ["event_type"],
)

# ──────────────────────────────────────────────────────────────────────
# Event worker / consumer (app/events/worker.py)
# Answers: How long does end-to-end message processing take?  Which
# handlers are slow?  Are we retrying / dead-lettering too much?
# ──────────────────────────────────────────────────────────────────────

WORKER_MESSAGE_DURATION = Histogram(
    "worker_message_duration_seconds",
    "Total time to process a message (deserialize + dispatch all handlers + commit)",
    ["group"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

WORKER_HANDLER_DURATION = Histogram(
    "worker_handler_duration_seconds",
    "Time for a single handler function call",
    ["group", "handler"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0),
)

WORKER_MESSAGES_TOTAL = Counter(
    "worker_messages_total",
    "Total messages processed by outcome",
    ["group", "outcome"],  # outcome: "ack", "retry", "dlq", "error"
)

WORKER_RETRIES_TOTAL = Counter(
    "worker_retries_total",
    "Total message retries",
    ["group"],
)

WORKER_DEAD_LETTERS_TOTAL = Counter(
    "worker_dead_letters_total",
    "Total messages sent to the dead-letter queue",
    ["group"],
)

WORKER_IN_FLIGHT = Gauge(
    "worker_messages_in_flight",
    "Messages currently being processed (should be 0 or 1 with prefetch=1)",
    ["group"],
)

# ──────────────────────────────────────────────────────────────────────
# Database connection pool (app/db/__init__.py)
# Answers: Are we running out of connections?  How much overflow are
# we using?  Are requests waiting for connections?
# ──────────────────────────────────────────────────────────────────────

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured base pool size",
)

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out",
    "Number of connections currently checked out (in use)",
)

DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow",
    "Number of overflow connections currently open (above pool_size)",
)

DB_POOL_CHECKED_IN = Gauge(
    "db_pool_checked_in",
    "Number of connections sitting idle in the pool",
)

DB_POOL_WAIT_TOTAL = Counter(
    "db_pool_wait_total",
    "Number of times a checkout had to wait for a connection",
)
