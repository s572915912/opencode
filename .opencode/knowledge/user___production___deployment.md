# User / Production / Deployment

- [FACT] user’s production deployment architecture includes `separate ECS services for detection, tracking, and API gateway, all with service discovery enabled`
- [FACT] user’s ECS production cluster consists of `3 t3.medium instances behind an AWS ALB on port 80/443`
- [DECISION] Because the user’s current production architecture uses `separate ECS services for detection, tracking, and API gateway, all with service discovery enabled` behind an ALB, the next continuation should focus on **ALB target group stickiness configuration**, **WebSocket routing implications**, and **service-to-service communication guidance**, not local frame dtype optimization | supersedes:generic_frame_optimization_advice
- [DECISION] Because the user’s current production architecture uses `separate ECS services for detection, tracking, and API gateway, all with service discovery enabled` behind an ALB, the next continuation should focus on **ALB target group stickiness configuration**, **WebSocket routing implications**, and **service-to-service communication guidance**, not local frame dtype optimization | supersedes:generic_frame_optimization_advice
- [FACT] `production_deployment_monitoring_deadline: June 21, 2024`
- [FACT] `production_deployment_monitoring_estimate: 16 hours`
- [UPDATE] `project_transformer_training_deadline_new: June 10 (was: May 30)` | supersedes:project_transformer_training_deadline_old
- [UPDATE] `project_deployment_deadline_new: August 10 (was: July 10)` | supersedes:project_deployment_deadline_old
