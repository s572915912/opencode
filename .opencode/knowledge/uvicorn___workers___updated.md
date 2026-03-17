# Uvicorn / Workers / Updated

- [UPDATE] `uvicorn_workers_updated: 4 (was: 2)` — user explicitly said increasing workers from `2` to `4` helped with `ConnectionResetError: [Errno 104] Connection reset by peer` | supersedes:implicit_worker_examples
- [FACT] user explicitly reported a later batch size preference: `batch size of 12 seems to be the sweet spot for balancing memory constraints and throughput during training`
- [FACT] user explicitly reported validation metrics `BLEU-4 score of 32.5`, `METEOR score of 27.1`, `CIDEr score of 98.3` after `10 epochs` on `COCO val2017`
- [FACT] user explicitly instructed `Please respond only in English.`
- [FACT] user explicitly instructed `Always mention deployment strategies when I ask about production updates.`
- [FACT] user explicitly stated a serverless preference: `I prefer using serverless for cost efficiency and automatic scaling despite the initial cold start challenges.`
- [FACT] user explicitly instructed `Always mention serverless scaling strategies when I ask about backend deployment.`
- [FACT] user explicitly stated `I've locked the PyTorch version to 1.13.1 and Transformers to v4.29 for production stability` in the webhook discussion
- [FACT] later user-reported evaluation metrics `BLEU-4 score of 38.7`, `METEOR score of 31.4`, `CIDEr score of 112.5` were introduced in a COCO evaluation question and were not captured in older summaries
- [FACT] user reported beam-search latency improvement `280ms (was: 450ms)` on `RTX 3090` during custom CUDA kernel optimization discussion
- [FACT] user reported Lambda cold start latency reduced to `150ms` with provisioned concurrency using `2 pre-warmed instances`
- [FACT] user later explicitly said current sprint is `Sprint 9` with tasks dated `2024-07-01` to `2024-07-25`
- [DECISION] because the user locked `PyTorch 1.13.1` and `Transformers v4.29` for production stability → future implementation guidance for production integrations should avoid suggesting incompatible upgrades unless explicitly requested
- [UPDATE] `evaluation_bleu4_latest_reported: 39.2 (was: 38.7)` — later user said final evaluation BLEU-4 was slightly improved to `39.2` after last-minute hyperparameter tuning | supersedes:evaluation_bleu4_later_reported
- [FACT] user added a new persistent preference: `Always include final evaluation metrics when I ask about model performance.`
- [FACT] user later asked how to automatically append final evaluation metrics `BLEU-4`, `METEOR`, and `CIDEr` to model performance queries in code using `t5-base` examples
