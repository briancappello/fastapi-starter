#!/bin/bash
# Run Kafka consumers in standalone mode (without HTTP server).
#
# Usage:
#   ./run_consumer.sh                    # Run consumers from KAFKA_CONSUMERS env
#   ./run_consumer.sh feed-one           # Run only feed-one
#   ./run_consumer.sh feed-one,feed-two  # Run feed-one and feed-two
#
# Environment variables:
#   KAFKA_ENABLED            - Set to "false" to disable Kafka (default: true)
#   KAFKA_BOOTSTRAP_SERVERS  - Kafka broker addresses (default: localhost:9092)
#   KAFKA_CONSUMERS          - Which consumers to run (default: all)

set -e

exec uv run app kafka run "$@"
