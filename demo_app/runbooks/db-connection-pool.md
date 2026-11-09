# DB connection pool exhausted
Symptoms: QueuePool limit reached, connection timed out, 500s on endpoints using the database, many idle in transaction connections.
Steps: 1) find transactions held open (idle in transaction) and the code path that holds them; 2) terminate long-idle sessions as a short-term relief; 3) ensure transactions are closed before slow external calls; 4) consider raising the pool size only after fixing leaks.
