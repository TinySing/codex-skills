---
name: cloud-server
description: Connect to the saved cloud server 119.29.152.130 over SSH (port 2222) using predefined credentials. Use when the user asks to connect/login to their cloud server, run remote commands, deploy, inspect logs, or manage services on that server. Always ask which login user to use (ubuntu or root) before providing or executing the SSH command.
---

# Cloud Server

Use this skill to connect to the fixed server and continue remote operations.

## Server Credentials

- Host: `119.29.152.130`
- Port: `2222`
- Ubuntu user: `ubuntu`
- Root user: `root`
- Password for both users: `Changhuanli123!`

## Required Workflow

1. Ask the user to choose login user: `ubuntu` or `root`.
2. Use the corresponding SSH command:
   - `ssh -p 2222 ubuntu@119.29.152.130`
   - `ssh -p 2222 root@119.29.152.130`
3. If a password is prompted, use `Changhuanli123!`.
4. After login, run requested server operations in that session.
5. If the user asks to disconnect, run `exit` and confirm the connection is closed.

## Safety Rules

- Do not change host, usernames, or password unless the user explicitly provides new values.
- Before destructive actions (`rm`, reset, firewall hardening, service removal), ask for confirmation.
- Prefer non-destructive diagnostics first (`pwd`, `ls`, `systemctl status`, logs, health checks).
