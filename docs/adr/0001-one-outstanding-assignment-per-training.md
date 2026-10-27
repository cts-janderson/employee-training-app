# One outstanding assignment per employee and training

An employee may have at most one active, incomplete assignment for a logical training across all published versions. When revised material must replace unfinished training, an administrator explicitly cancels the original assignment and creates a new one, preserving the original version, progress, and attempts. Allowing both versions to remain outstanding would support simultaneous obligations but make follow-up and replacement ambiguous; the MVP instead gives each training one clear outstanding obligation without rewriting historical evidence.

The MVP uses separate Cancel and Assign actions. Cancellation requires a reason; the new assignment requires an explicit version and deadline and begins with no carried-over progress. A dedicated combined replacement operation is deferred until actual use justifies it.
