# Slow upstream dependency
Symptoms: ReadTimeout calling another service, high latency on pages that need that service, no recent deploy, healthy local resources.
Steps: 1) check the dependency's health and latency; 2) contact its owning team; 3) add caching, circuit breaker or fallback data; 4) tune timeouts and retries so slow calls do not exhaust workers.
