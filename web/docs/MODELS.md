# Model providers

`ModelProvider` normalizes requests and responses so orchestration does not depend on an SDK. `MockModelProvider` is explicit and test-only/development-only; it is never silently substituted for a missing production provider. Future adapters should read credentials from server environment variables, classify provider failures, apply timeouts, and redact secrets from logs and events.
