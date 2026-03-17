# Port / Translation / Service

- [UPDATE] port_translation_service: 4500 (was: translation service not previously assigned in PK; earlier microservice set emphasized 3000/4000/5000) — later architecture explicitly separated translation service on `4500` | supersedes:none
- [UPDATE] translation_service_latency_current: 180ms (was: earlier translation timing discussed as 220ms average response time) | supersedes:none
- [UPDATE] port_translation_service: 4500 (was: translation service not previously assigned in PK; earlier microservice set emphasized 3000/4000/5000) — later architecture explicitly separated translation service on `4500` | supersedes:none
- [UPDATE] translation_service_latency_current: 180ms (was: earlier translation timing discussed as 220ms average response time) | supersedes:none
- [FACT] token_verification_latency_current_example: 100ms
- [FACT] token_verification_latency_target_example: under 50ms
- [DECISION] because token verification latency was around 100ms and user wanted under 50ms → caching decoded tokens in Redis and optimizing verification path were suggested | supersedes:none
- [UPDATE] redis_pubsub_latency_example: 50ms | supersedes:none
- [FACT] redis_pubsub_max_payload_example: 1048576
- [FACT] redis_pubsub_max_channels_example: 10000
- [FACT] redis_maxmemory_example: 1gb
- [FACT] redis_maxmemory_policy_example: allkeys-lru
- [FACT] redis_tcp_keepalive_example: 60
- [FACT] redis_tcp_backlog_example: 511
