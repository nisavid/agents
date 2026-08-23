# Waiting for dispatched children

Three bounded native children are running in disjoint scopes. The leader can
still prepare the next review package, inspect an already-returned report, and
update local integration notes. The wait surface supports repeated five-second
checks, a bounded multi-minute wait, and an open-ended wait. In an earlier run,
a child reached a terminal state but its completion notification was lost even
though it remained visible in the live child inventory. Choose the leader's
work and waiting sequence, including how terminal child state is recovered.
