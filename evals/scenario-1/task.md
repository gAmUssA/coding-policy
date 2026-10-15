# Pre-Release Shell Script Readiness Check

## Problem/Feature Description

Your team is preparing to open a pull request for a batch of infrastructure changes that touch several shell scripts in the `inputs/` directory. Before the PR can go out, the release process requires all changed shell scripts to pass a syntax validation gate. A colleague has flagged that some of the scripts may have been edited in a hurry and should be verified before the branch is published.

Your job is to perform the shell script readiness check on every `.sh` file in the `inputs/` directory and produce a report that the team can act on. The check must be carried out using the appropriate validation tool for shell scripts. Scripts that pass are ready; scripts that fail must be identified along with a description of the problem found, since they need to be fixed before a PR can be created. The team's process is explicit: nothing proceeds until failures are resolved.

## Output Specification

Run the validation check on all shell scripts found in `inputs/` and produce a file named `readiness-report.md` in the working directory.

The report should include:
- A summary of the overall readiness status (ready to proceed, or not)
- A per-script section listing the script name, whether it passed or failed, and — for any failure — the exact error message produced by the validation tool
- A clear statement for any failing scripts that they must be fixed before the PR can proceed
