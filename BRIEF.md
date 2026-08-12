We need to improve the way that parallelism works. I'd like to remove the parallel parameter completely, and the interactive interface completely to no longer be optional parameters. Instead, make them core to how git-loopy works. In other words, every single time git-loopy runs, it should always run in interactive tui mode.

Currently, when the parallel parameter is passed in when the user runs git-loopy, it's only queuing and running issues that are labeled as `parallel-safe`, and it doesn't include a queue for any other issues that it could be working on sequentially.

For example, if there are three issues labeled as `parallel-safe`, when git-loopy runs, it should automatically begin working on all three of those issues labeled `parallel-safe`. It should also queue up all of the other issues that are not labeled `parallel-safe`, in the sequential ordering that they're most appropriate to be worked on.

Currently, when a user runs git-loopy, only the parallel safe issues are loaded, and no other issues at all, whatsoever.

What I'd like is for the user to have confidence that when they run git-loopy, all `parallel-safe` issues will be worked on upfront. Once the `parallel-safe` issues are completed, the issues that are not `parallel-safe` will be worked on sequentially, all within the same GitLoopy run.

