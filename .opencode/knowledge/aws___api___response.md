# Aws / Api / Response

- [UPDATE] `aws_api_response_time_additional: 110ms under 100 concurrent users` | supersedes:none
- [FACT] user stated `pagerduty_response_time_target: under 5 minutes` in the PagerDuty alerting thread
- [FACT] user stated `user_stated_twitter_latency: 150ms` in a later Twitter latency optimization question, which differs from the older `twitter_average_post_latency_reported: 800ms` and should be preserved as a separate metric rather than overwritten
- [DECISION] Because the user wants users to continue using the API after JWT expiry without re-authenticating, the next continuation should focus on refresh-token issuance, storage, rotation, expiry, revocation, and refresh endpoint design | supersedes:older_translation_or_gdpr_focus
- [UPDATE] `live_trading_api_response_time_new: 180ms (was: 400ms)` | supersedes:none
