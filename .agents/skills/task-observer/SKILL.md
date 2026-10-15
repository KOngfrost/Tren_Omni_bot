---
name: task-observer
description: Monitors background tasks, observes execution logs, diagnoses hanging processes, and verifies task completion. Use when supervising long-running commands, tracking background build/test tasks, or managing autonomous loops.
---

# Task Observer: Process & Task Supervision

Task Observer provides disciplined guidelines for supervising background tasks and autonomous processes without wasting context or CPU cycles.

## Core Rules for Task Observation

1. **Reactive Wakeups (Zero Polling)**:
   - When a command is dispatched to the background via `run_command`, the runtime automatically notifies the agent when it finishes.
   - **Do NOT poll `manage_task(Action='status')` in a tight loop.** Stop calling tools to end the turn, and wait for the system notification.
2. **One-Shot Timers**:
   - If an operation may hang or requires periodic liveness verification, use the `schedule` tool with `TimerCondition='task-<id>'`.
   - Never run blocking `sleep` commands in the shell.
3. **Log Inspection**:
   - When a task completes with failure (exit code != 0), read the task log using `view_file` on the log URI.
   - Focus on the last 50-100 lines containing tracebacks and error messages.

## Diagnostic Workflow

1. **Check Task List**: `manage_task(Action='list')` to see active background tasks.
2. **Inspect Task State**: `manage_task(Action='status', TaskId=...)` to check status and log file location.
3. **Cancel Runaway Tasks**: `manage_task(Action='kill', TaskId=...)` if a process has timed out or hung indefinitely.
