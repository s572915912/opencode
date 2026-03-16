# Several / Ecs / Github

- [DECISION] Because several ECS/GitHub Actions examples incorrectly used `aws ecs update-service --image` or assumed task-definition behavior → next agent should correct deployment flow toward proper task-definition registration and service update logic
- [DECISION] Because several encryption examples generated new random keys/IVs incorrectly across encrypt/decrypt paths → next agent should correct toward shared-session key derivation, unique nonce per encryption, and proper auth-tag handling
- [FACT] latest unresolved ECS/GitHub Actions deployment topic still present: repeated workflow examples incorrectly used `aws ecs update-service --image ...`; next agent should correct toward task-definition registration + service deployment flow
