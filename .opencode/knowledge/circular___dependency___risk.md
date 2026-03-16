# Circular / Dependency / Risk

- [DECISION] Because circular dependency risk is now the leading suspected cause of `"Maximum call stack size exceeded"` → next agent should first audit import relationships and shared initialization boundaries before micro-optimizing loops.
