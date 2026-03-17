# User / Later / Reported

- [FACT] user later reported session metadata cache hit rate `70%`, distinct from earlier broader Redis cache hit rate `85%`
- [EVENT] user later refactored `ChatInput.js` for encrypted message sending and receiving and asked to integrate it with Diffie-Hellman | order:101
- [EVENT] user later added fallback UI for WebSocket disconnection with automatic reconnection attempts every `5 seconds` in `WebSocket.js` and asked to fix the reconnection process | order:102
- [DECISION] because the user separated WebSocket handling into its own microservice on `port 7000` → memory optimization, Redis pub/sub, monitoring, and deployment on Kubernetes became separate concerns | supersedes:none
- [EVENT] user explicitly added new standing preference: “Always include user satisfaction metrics when I ask about UI/UX design improvements.” before the final summary request | order:214
- [FACT] user reported `90% satisfaction rate` for the multi-language switching feature during user testing sessions
