# User / Seeing / Event

- [DECISION] because the user was seeing `"Event loop delay"` spikes in `ChatController.js` → moved from `fs.readFileSync` to `fs.promises.readFile` using `async/await` | supersedes:none
