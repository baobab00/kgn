# KGN — Getting Started Guide

A step-by-step guide anyone can follow, even with no prior experience.

---

## What is KGN?

KGN is a tool that gives AI agents (like Claude) **long-term memory**.

Normally, AI forgets everything once a conversation ends. KGN solves this by saving knowledge into a database so AI can remember and build on past work.

**In short:** You write notes → KGN stores them → AI can read and search them anytime.

---

## Before You Begin

You'll need to install three things. Don't worry — we'll walk through each one.

| What | Why | Time |
|---|---|---|
| Python 3.12+ | KGN is written in Python | ~5 min |
| Docker Desktop | Runs the database automatically | ~10 min |
| KGN itself | The tool you'll be using | ~2 min |

---

## Step 1: Install Python

### Windows

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click the big yellow **"Download Python 3.12.x"** button
3. Run the installer
4. **⚠️ IMPORTANT:** Check the box that says **"Add python.exe to PATH"** at the bottom
5. Click **"Install Now"**

### Mac

```bash
# If you have Homebrew:
brew install python@3.12

# If not, download from python.org/downloads
```

### Verify it worked

Open a terminal (Command Prompt on Windows, Terminal on Mac) and type:

```bash
python --version
```

You should see something like `Python 3.12.x`. If you see an error, restart your computer and try again.

---

## Step 2: Install Docker Desktop

Docker runs the database (PostgreSQL) for you. You don't need to know anything about databases.

1. Go to [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
2. Download for your OS (Windows / Mac / Linux)
3. Install and open Docker Desktop
4. Wait until the whale icon in your taskbar shows **"Docker Desktop is running"**

> **💡 Tip:** Docker Desktop needs to be running whenever you use KGN. You can set it to start automatically on login.

---

## Step 3: Install KGN

Open your terminal and run:

```bash
pip install kgn-mcp
```

That's it! Verify the installation:

```bash
kgn --help
```

You should see a list of available commands.

---

## Step 4: Start the Database

KGN comes with a ready-to-use database setup. You just need to start it:

```bash
# First, get the KGN source code (for the Docker config)
git clone https://github.com/baobab00/kgn.git
cd kgn

# Start the database
docker compose -f docker/docker-compose.yml up -d postgres
```

> **What just happened?** Docker downloaded and started a PostgreSQL database in the background. It runs quietly — you won't see a window for it.

### Verify the database is running

```bash
docker ps
```

You should see a container with "postgres" in its name showing status "Up".

---

## Step 5: Create Your First Project

```bash
kgn init --project my-first-project
```

You should see a success message. Your project is ready!

---

## Step 6: Add Your First Knowledge Node

KGN uses simple text files with a `.kgn` extension. Let's create one:

### Create a file called `my-note.kgn`

Open any text editor (Notepad, VS Code, etc.) and paste this:

```yaml
---
kgn_version: "0.1"
id: "new:my-first-note"
type: SPEC
title: "My First Knowledge Node"
status: ACTIVE
project_id: "my-first-project"
agent_id: "human"
tags: ["getting-started", "hello"]
confidence: 0.9
---

## Context

This is my first time using KGN. I'm learning how knowledge nodes work.

## Content

Knowledge nodes are like smart notes that AI can understand and search through.
Each node has:
- A **type** (GOAL, SPEC, TASK, etc.)
- A **status** (ACTIVE, DEPRECATED, etc.)
- **Tags** for easy searching
- A **body** with your actual content
```

### Save the file, then ingest it

```bash
kgn ingest my-note.kgn --project my-first-project
```

🎉 **Congratulations!** You just added your first piece of knowledge that AI can access.

---

## Step 7: Check Your Project

```bash
# See project overview
kgn status --project my-first-project

# Check graph health
kgn health --project my-first-project

# Search for your node
kgn query nodes --project my-first-project --type SPEC
```

---

## Step 8: Connect to Claude (Optional)

This is where KGN really shines — letting Claude access your knowledge graph.

### For Claude Desktop

1. Find your Claude Desktop config file:
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

2. Add this to the file:

```json
{
  "mcpServers": {
    "kgn": {
      "command": "kgn",
      "args": ["mcp", "serve", "--project", "my-first-project", "--role", "admin"]
    }
  }
}
```

> **💡 Available roles:** `admin` (full access), `genesis` (GOAL/SPEC/ARCH/CONSTRAINT), `worker` (LOGIC/DECISION), `reviewer` (ISSUE/SUMMARY), `indexer` (SUMMARY only). When omitted, defaults to `admin`.

3. Restart Claude Desktop

4. Look for the 🔨 (hammer) icon — it means KGN tools are available!

Now Claude can read, search, and write to your knowledge graph.

### For Claude Code (Terminal)

```bash
claude mcp add kgn -- kgn mcp serve --project my-first-project --role admin
```

---

## What's Next?

Now that you have KGN running, here are some things to try:

### 📁 Ingest example files

KGN comes with example files you can try:

```bash
kgn ingest examples/ --project my-first-project --recursive
```

### 🔍 Search with AI similarity

If you have an OpenAI API key, you can enable semantic search:

```bash
# Set your API key (create a .env file)
echo "KGN_OPENAI_API_KEY=sk-your-key-here" > .env

# Re-ingest with embeddings
kgn ingest examples/ --project my-first-project --recursive --embed

# Find similar nodes
kgn query similar <node-id> --project my-first-project --top 5
```

### 🌐 Web Dashboard

See your knowledge graph visually:

```bash
pip install kgn-mcp[web]
kgn web serve --project my-first-project --port 8080
```

Open http://localhost:8080 in your browser.

### 📝 VS Code Extension

Get syntax highlighting and validation for `.kgn` files:

```bash
code --install-extension baobab00.vscode-kgn
```

---

## Common Issues

<details>
<summary><b>"kgn" is not recognized</b></summary>

This usually means Python's Scripts folder isn't in your PATH.

**Fix (Windows):**
1. Press `Win + R`, type `sysdm.cpl`, press Enter
2. Go to **Advanced** → **Environment Variables**
3. Under **Path**, add: `C:\Users\<YourName>\AppData\Local\Programs\Python\Python312\Scripts`
4. Restart your terminal

**Fix (Mac/Linux):**
```bash
export PATH="$HOME/.local/bin:$PATH"
```
Add this line to your `~/.bashrc` or `~/.zshrc` file.

</details>

<details>
<summary><b>Docker: "Cannot connect to the Docker daemon"</b></summary>

Docker Desktop isn't running. Open Docker Desktop and wait for it to fully start (whale icon in taskbar).

</details>

<details>
<summary><b>Database connection error</b></summary>

Make sure the PostgreSQL container is running:

```bash
docker ps
```

If it's not listed, start it again:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
```

</details>

<details>
<summary><b>"Permission denied" errors</b></summary>

**Windows:** Run your terminal as Administrator.

**Mac/Linux:** Add `sudo` before the command, or use `pip install --user kgn-mcp`.

</details>

---

## Quick Reference

| What you want to do | Command |
|---|---|
| Create a project | `kgn init --project <name>` |
| Add knowledge | `kgn ingest <file.kgn> --project <name>` |
| Add a whole folder | `kgn ingest <folder>/ --project <name> --recursive` |
| Check status | `kgn status --project <name>` |
| Search nodes | `kgn query nodes --project <name>` |
| Start MCP server | `kgn mcp serve --project <name> [--role admin]` |
| Web dashboard | `kgn web serve --project <name>` |
| See all commands | `kgn --help` |

---

**Need more help?** Check the [full README](README.md) or open an [issue on GitHub](https://github.com/baobab00/kgn/issues).
