# terminal-agent

## Your AI Coding Assistant

**terminal-agent** is a helpful AI coding partner that lives right in your terminal. It can read your code, make changes, run tests, and help you debug—all through natural language commands.

Think of it as having a smart programmer buddy who can:
- 📖 Read and understand your code
- ✏️ Make changes to files
- ▶️ Run commands and tests
- 🔍 Search for information online
- 💾 Remember your work across sessions

## How It Works (Simple Explanation)

The agent follows a simple loop:
1. **You give it a task** (like "fix this bug" or "add a login feature")
2. **It thinks about what to do** and picks the right tools
3. **It does the work** (reads files, runs commands, makes changes)
4. **It sees what happened** and adjusts if needed
5. **It repeats until the task is done**

This is similar to how other AI coding assistants work, but built with modern AI frameworks.

## Quick Start Guide

### Step 1: Install Everything
```bash
pip install -r requirements.txt
```

### Step 2: Get Your API Key
You need an API key from [OpenRouter](https://openrouter.ai/settings/keys) (it's free to sign up).

### Step 3: Set Up Your Environment
```bash
cp .env.example .env
```

Edit the `.env` file and add your OpenRouter API key:
```
OPENROUTER_API_KEY=your_actual_api_key_here
```

### Step 4: Run the Agent
```bash
python main.py
```

The agent will start and ask for your API key if you haven't set it up yet.

## Two Modes for Safe Work

The agent has two modes to help you work safely:

### 🔍 **Plan Mode** (`/plan`)
- **What it is**: Safe, look-but-don't-touch mode
- **What you can do**: Read files, search for information, explore code
- **What you CANNOT do**: Change files, run commands, make modifications
- **Best for**: Understanding a codebase before making changes

### 🛠️ **Build Mode** (`/build`)
- **What it is**: Full access mode
- **What you can do**: Everything in Plan mode PLUS edit files, run commands, make changes
- **Best for**: Actually implementing fixes and features

Switch modes anytime with `/plan` or `/build`.

## What the Agent Can Do (Tools)

| Tool | What it does | Example use |
|------|-------------|-------------|
| `read_file` | Look at any text file | "Show me the main.py file" |
| `write_file` | Create or replace files | "Create a new config file" |
| `edit_file` | Make precise changes | "Fix the typo in line 42" |
| `list_dir` | See what's in a folder | "What files are in src/?" |
| `bash` | Run terminal commands | "Run the tests" or "Install this package" |
| `run_tests` | Run your test suite | "Are all tests passing?" |
| `tavily_search` | Search the web | "How do I use React hooks?" |
| `todo_write` / `todo_read` | Manage task lists | "Break this feature into steps" |

## Helpful Commands

### Managing Your Work
- `/sessions` - See all your saved conversations
- `/resume <id>` - Continue a previous session
- `/delete <id>` - Delete a saved session
- `--continue` - Automatically resume last session when starting

### Choosing Your AI
- `/models` - See available AI models (free ones first)
- `/model <number>` - Switch to a different model
- `/key` - Change your API key

### Other Useful Commands
- `/help` - Show all available commands
- `/clear` - Start fresh with a new session
- `/usage` - Check how much AI you've used
- `/compact` - Clean up old session data

## Saving and Coming Back Later

Your work is automatically saved! You can:
1. **Start fresh**: Just type your first task when you run the agent
2. **Come back later**: Use `/sessions` to see past work, then `/resume <number>`
3. **Auto-continue**: Run `python main.py --continue` to pick up where you left off

Each session remembers:
- Which AI model you were using
- Whether you were in Plan or Build mode
- A name based on what you first asked for

## Safety First

The agent has built-in protections to keep you safe:

### 🛡️ **Smart Input Filtering**
- Blocks requests for harmful things (like hacking tools)
- Redirects off-topic chat back to coding tasks
- Prevents obvious security risks

### ⚠️ **Command Safety**
- Blocks dangerous commands (like deleting your whole system)
- Stops reverse shells and other risky operations
- Works even if you turn on auto-approval

### 🔍 **Content Checking**
- Scans file changes for malicious code patterns
- Prevents creating harmful software
- Asks for confirmation before risky actions

### ✋ **Permission Prompts**
- Asks before running commands: "Allow agent to run: `npm install`?"
- Asks before writing files: "Allow agent to write 500 chars to `config.json`?"
- You can skip these with `AGENT_AUTO_APPROVE=true` in `.env` (not recommended for beginners)

## Choosing an AI Model

Different AI models work better for coding. Here's a quick guide:

### 🏆 **Top Recommendations**
1. **Qwen/Qwen2.5-72B-Instruct** - Great balance of ability and speed
2. **meta-llama/Meta-Llama-3.1-70B-Instruct** - Very reliable for coding tasks
3. **meta-llama/Meta-Llama-3.1-8B-Instruct** - Fastest/cheapest, but less reliable

### 💰 **Free vs Paid**
- **Free models** (marked `:free`): Cost nothing to use
- **Paid models**: More consistent, but charge per usage

### 🔄 **How to Switch Models**
1. Type `/models` to see the list
2. Type `/model 3` to use the 3rd model in the list
3. Or type the full name: `/model qwen/qwen-2.5-72b-instruct`

## Common Things You Can Do

### 🐛 **Fixing Bugs**
> "Why is my login function throwing an error on line 23?"

### 🔍 **Exploring Code**
> "Show me all the Python files and explain what each one does"

### ✨ **Adding Features**
> "Add a dark mode toggle to this website"

### 🧹 **Cleaning Up Code**
> "Refactor this function to be easier to read"

### 📚 **Writing Documentation**
> "Create a README explaining how to install and use this project"

### ✅ **Testing**
> "Run all the tests and tell me what's failing"

## Troubleshooting Tips

### ❌ "Agent isn't responding"
- Check your internet connection (needed for AI calls)
- Verify your API key is correct in `.env`
- Try a different model: `/model meta-llama/Meta-Llama-3.1-70B-Instruct`

### ⚙️ "Commands aren't working"
- Make sure you're in the right project folder
- Check if files exist: try `list_dir` first
- Start in Plan mode (`/plan`) to explore safely

### 💾 "My work isn't saving"
- Work saves automatically after each turn
- Use `/sessions` to see all saved conversations
- Use `--continue` when starting to resume previous work

### 🤖 "AI keeps making mistakes"
- Try a more reliable model (the paid ones often work better)
- Break your task into smaller steps
- Use Plan mode first to understand the problem

## Advanced Features (When You're Ready)

### 📊 **See What the Agent is Doing**
If you get a LangChain API key (free at smith.langchain.com):
1. Add `LANGCHAIN_API_KEY=your_key` to `.env`
2. Watch detailed traces at https://smith.langchain.com

### 📂 **Work with Git**
Enable with `AGENT_ENABLE_GIT=true` to use:
- `git status` - See what changed
- `git diff` - View exact changes
- `git commit` - Save your work

### 🗂️ **Use a Sandbox Folder**
Keep experiments separate:
```bash
AGENT_WORKDIR=./sandbox python main.py
```
This keeps the agent confined to a specific folder.

## Ready to Try?

Just run:
```bash
python main.py
```

Then try something simple like:
- "What files are in this project?"
- "Explain what this code does" (point it at a file)
- "Help me fix this error" (share the error message)

The agent is here to help you code better and faster. Happy coding! 🚀