I'd like to remove the terminology. For an issue status that currently uses the word "advanced," I'd like to change "advanced" to "completed". Currently, the dashboard has a header for a column called "Closed," which doesn't seem to be producing any values within that field. So, I think we could probably remove this, as the status should be reflected under the "Status" header column.

We need to improve the way that parallelism works. I'd like to remove the parallel parameter completely, and the interactive interface completely to no longer be optional parameters. Instead, make them core to how git-loopy works. In other words, every single time git-loopy runs, it should always run in interactive tui mode.

Currently, when the parallel parameter is passed in when the user runs git-loopy, it's only queuing and running issues that are labeled as `parallel-safe`, and it doesn't include a queue for any other issues that it could be working on sequentially.

For example, if there are three issues labeled as `parallel-safe`, when git-loopy runs, it should automatically begin working on all three of those issues labeled `parallel-safe`. It should also queue up all of the other issues that are not labeled `parallel-safe`, in the sequential ordering that they're most appropriate to be worked on.

Currently, when a user runs git-loopy, only the parallel safe issues are loaded, and no other issues at all, whatsoever.

What I'd like is for the user to have confidence that when they run git-loopy, all `parallel-safe` issues will be worked on upfront. Once the `parallel-safe` issues are completed, the issues that are not `parallel-safe` will be worked on sequentially, all within the same git-loopy run.

Right now, I know that we've set up GitLab to produce a dashboard that shows how many credits are being consumed, as well as the number that is premium. While I think these data points are important in the dashboard, I'd also like to add a column for the estimated or actual accurate USD dollar amount that these credits are incurring as cost.

We need to generate this cost value in USD based off of some kind of live API call to determine what the current pricing is, based off the input/output tokens, as well as the model and reasoning level that was used to calculate the value. We may need to update existing ADRs, purities, or wayfinder issues to accommodate for this.

At the point where we want to add a couple of new capabilities to git-loopy, specifically: right now, git-loopy is dependent on a user running git-loopy from their terminal command line, and wherever they're doing that, they need to ensure that machine never goes to sleep and always stays on throughout the entire duration that git-loopy is running, in order to maintain progress on filling and completing a queue of issues that git-loopy picks up.

What's the support for running git-loopy inside of a GitHub Actions workflow? Let's also add support for running git-loopy inside of an Azure Container Apps instance, which means we'll need to deploy an Azure Container App with a container image that has git-loopy installed on it already, to be able to do this.

The reason why I'm choosing Azure Container Apps is because I also wonder if there's a way for us to scale performance, potentially with how many issues that are deemed parallel-safe to be worked on at any given time. I don't know what this will look like for GitHub Actions, but I'd like to try to find ways to improve the current performance of how quickly issues are worked on and completed when git-loopy is run, as well as offload the workloads from the user's local developer workstation to either, like I said, a GitHub Actions workflow or workflows (meaning many in parallel), as well as within an Azure Container App or multiple Azure Container Apps, whatever makes the most sense.

I'd like for us to consolidate and deviate from using worktrees altogether as part of how Git Loopy works.

Instead, we need to update the way that multiple issues and workflows for those issues are instantiated by Git Loopy, either through something like GitHub Actions or Azure Container Apps, where Git Loopy is installed and able to run and authenticate against the GitHub repository. They should run independently and in parallel on some kind of sandbox. "/Users/bradcstevens/code/github/bradcstevens/git-loopy/.reference/agentic-developer-workflows-video-reference.md" Details what parallel agent sandboxes would look like.

Those sandboxes should be either individual GitHub Actions workflows, if the users specify that this is the mechanism they want the agent workflows to be conducted within, or an Azure Container App.

Both the GitHub Actions and Azure Container App approaches should use the same base container image, where a fresh instance of git-loopy is installed. The most appropriate and lightweight container image should be used to support efficient, low-overhead compute, so git-loopy is available to work on the issues in its queue.

GitHub Copilot CLI has a sandbox capability that I think we would want to use primarily for the sandboxes. However, I'm open to exploring how we define "sandbox" in other ways—whether through the GitHub Copilot SDK, or by creating so-called "sandboxes" that we represent inside of the GitHub Actions pipeline workflow, or within an isolated Azure Container Apps environment.

This is especially important when multiple issues are being worked on in parallel—each should have its own sandbox pipeline.

GitLoop should support ticket-in-driven delivery workflow number eight, production hop-flow workflows number nine, specialized workflow portfolios number ten, and of course, number eleven: ultimately the full software factory workflow, together with the agentic layer and meta-engineering.

We need to determine how we do this so that it aligns to the most efficient, optimized way to create an agentic software factory driven by GitLoop, and all the skills that GitLoop uses that the human-in-the-loop would generate collateral for GitLoop to use, to become as autonomous as possible.