---
type: llm
weight: 3
---

The report must correctly say that in WAL mode readers do not block the writer and the writer does not block readers, but there is still only one writer at a time; that changes are appended to a separate -wal file and moved back into the database by checkpoints (automatic by default at about 1000 pages); that WAL requires shared memory (the -shm file) so all processes must be on the same host and it does not work over a network filesystem; and that in rollback-journal mode a writer needs an exclusive lock that blocks readers while it commits. It must not claim WAL allows multiple concurrent writers. Citations should point at sqlite.org documentation for the core claims.
