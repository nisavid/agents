# Ownership reverse edges

Requests in one task ask Versionkeeping to restack a Graphite stack, choose a
review model and delegation shape, review the patch, edit and ready a pull
request, merge that pull request, merge a verified local branch, and synchronize
a maintained fork. The task also asks to delete a merged remote branch after a
verified merge outcome and explicit repository and operator authorization for
the exact remote, full ref, and expected target SHA.

Classify every operation. Versionkeeping owns the verified local Git merge and
fork synchronization, plus the separately authorized terminal remote-ref
deletion route. Graphite, review, model/delegation policy, pull-request
text/readiness, and pull-request merge actuation stay outside the plugin.
