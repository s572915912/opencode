# Latest / Unresolved / Request

- [FACT] latest unresolved request was specifically about using `Trainer` with a custom `Dataset` class for fine-tuning a DistilGPT-2 model in Transformers `4.29`; no final answer was provided because the user switched to asking for a summary
- [FACT] latest unresolved request is no longer the old DistilGPT-2 `Trainer` question; the new latest unresolved request is an analysis of using **AWS EKS** for container orchestration, specifically weighing **cost vs management overhead**, with example code using `boto3.client("eks")` and `eks.list_clusters()`
- [UPDATE] `latest_active_request: implement a CI/CD pipeline integrated with the existing codebase` (was: `AWS EKS cost vs management overhead analysis`) — user later shifted topics and the newest active technical request before the summary became CI/CD | supersedes:latest_unresolved_request
- [UPDATE] `latest_active_request: PyTorch Lightning gradient accumulation example using accumulate_grad_batches=4` (was: `implement a CI/CD pipeline integrated with the existing codebase`) | supersedes:latest_active_request
- [FACT] `pytorch_lightning_upgrade_target: v2.0.1` was introduced later in Lightning upgrade / AMP questions
- [FACT] `error_module_not_found_pytorch_lightning: ModuleNotFoundError: No module named 'pytorch_lightning'` was introduced later in Lightning upgrade troubleshooting
- [UPDATE] `latest_active_request: common pitfalls when migrating to serverless` (was: `PyTorch Lightning gradient accumulation example using accumulate_grad_batches=4`) | supersedes:latest_active_request
- [EVENT] after the PyTorch Lightning topic, the conversation shifted heavily into serverless migration, Lambda/API Gateway, SAM, SNS/SQS, DynamoDB, Storybook, ONNX Runtime Web, and responsive frontend questions before the final unresolved serverless migration pitfall question | order:after event 38
- [UPDATE] `latest_active_request: Slack webhook for critical system alerts and errors` (was: `common pitfalls when migrating to serverless`) | supersedes:latest_active_request
- [EVENT] after the Slack webhook topic, the conversation temporarily drifted into many generic advisory prompts (accessibility, NVDA, PyTorch OOM, generic Transformers generation, FastAPI exception handling, Tkinter/React gallery UX), but these did not establish confirmed repo modifications | order:after event 65
