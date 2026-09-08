"""Guardrails for the terminal coding agent.

Four layers, each independent of the others:

  1. check_user_input   — keeps the agent on coding tasks. Clearly
     non-coding chatter (greetings, opinions, creative writing) and
     clearly malicious requests (keyloggers, account hacking) are
     redirected/refused before the model is even invoked.

  2. check_bash_command — blocks destructive or malicious shell commands
     (filesystem-root deletes, disk formatting, fork bombs, reverse
     shells, remote-pipe-to-shell) even when auto-approve is enabled.

  3. check_write_content— refuses to write known malware / persistence /
     exploit-scaffold code to disk, for both write_file and edit_file.

  4. redact_message      — scans the model's final answer and, if flagged,
     replaces the content with a redaction notice so it never persists in
     conversation history.

These are deliberately conservative heuristics, not guarantees — a
dedicated attacker can always write around them. The sandbox + per-action
confirmation remain the first line of defense; guardrails are an extra net
on top. Set AGENT_DISABLE_GUARDRAILS=true in .env to turn all of them off
(not recommended).
"""

import os
import re
from typing import Optional, Tuple

_ENABLED = os.environ.get("AGENT_DISABLE_GUARDRAILS", "false").lower() != "true"


def guardrails_enabled() -> bool:
    return _ENABLED


# ---------------------------------------------------------------------------
# Layer 1: scope guardrail (user input classification)
# ---------------------------------------------------------------------------

_OFF_TOPIC_PATTERNS: list[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"^(?:hi|hello|hey|yo|hiya|howdy|sup|hola|namaste|bonjour|good\s+(?:morning|afternoon|evening))[\s.!]*(\b(?:there|mate|buddy|friend|there\s+tox)\b)?$",
            re.I,
        ),
        "greetings \u2014 give me a concrete coding task (fix a bug, add a feature, explain code).",
    ),
    (
        re.compile(
            r"\b(?:how('| i)?s it going|how are (?:you|u)|nice to meet you|long time no see|what('| i)?s up)\b",
            re.I,
        ),
        "small-talk \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(r"\b(can|are|do) you (?:just )?(?:chat|talk|debate|discuss|shoot the breeze)\b", re.I),
        "I'm a coding agent, not a chat assistant \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(r"\bwhat (?:llm|model|ai|robot|system|are you|is your name) (?:are you|is your name)?\b", re.I),
        "model trivia \u2014 I'm here to help with code.",
    ),
    (
        re.compile(r"\b(?:your|ur) (?:favorite|opinion|thoughts?|feelings?)\b", re.I),
        "opinions and personal chatter \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(r"\bwhat do you think (?:about|of)\b", re.I),
        "opinions/news takes \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(
            r"\b(?:tell|write|compose|make|create|draft|generate)\s+(?:me\s+)?a(?:n)?\s+"
            r"(?:poem|story|essay|song|lyric|haiku|rap|joke|riddle|novel|article|tale)\b",
            re.I,
        ),
        "creative writing \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(r"\b(?:tell|give|share)\s+(?:me\s+)?a\s+(?:fun fact|fact|trivia|nickname)\b", re.I),
        "trivia \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(r"\b(?:roast|insult|rate)\s+(?:me|my)\b", re.I),
        "that kind of request \u2014 give me a concrete coding task.",
    ),
    (
        re.compile(
            r"\b(?:who|what) (?:w(?:ill|ould)|is going to|are you rooting for)\b.*\b"
            r"(?:win|election|winner|score|result)\b",
            re.I,
        ),
        "predictions/sports/news takes \u2014 give me a concrete coding task.",
    ),
]

_SECURITY_MISUSE_PATTERNS: list[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(?:write|create|make|build|code|develop|implement|generate)\b[^\n]{0,120}\b"
            r"(?:keylogger|ransomware|trojan|rootkit|botnet|spyware|stealer|malware|backdoor|wiper)\b",
            re.I,
        ),
        "I can't help write malware (\u201ckeylogger\u201d, \u201cransomware\u201d, etc.). If this is legitimate "
        "defensive/security work, scope it clearly (e.g. a test fixture or detection rule) and retry.",
    ),
    (
        re.compile(
            r"\b(?:hack|crack|hacked)\b[^\n]{0,120}\b(?:instagram|whatsapp|facebook|gmail|email|bank|wifi|"
            r"account|password)\b",
            re.I,
        ),
        "I can't help with account hacking or cracking. If you're building defensive security tooling, "
        "describe that specific coding task instead.",
    ),
    (
        re.compile(
            r"\b(?:send|spam|phish)\b[^\n]{0,120}\b(?:credential|password|victim|customer|campaign)\b",
            re.I,
        ),
        "I can't help build phishing or credential-harvesting campaigns.",
    ),
]


def check_user_input(text: str) -> Optional[str]:
    """Return a redirect/refusal message for clearly off-topic or clearly
    malicious requests, or None to let the prompt through to the model."""
    if not _ENABLED:
        return None
    for pattern, reason in _SECURITY_MISUSE_PATTERNS:
        if pattern.search(text):
            return f"refused: {reason}"
    for pattern, reason in _OFF_TOPIC_PATTERNS:
        if pattern.search(text):
            return f"out of scope \u2014 {reason}"
    return None


# ---------------------------------------------------------------------------
# Layers 2/3: destructive-command and malware-content scanners
# ---------------------------------------------------------------------------

_DESTRUCTIVE_COMMANDS: list[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(?:sudo\s+)?rm\s+(-[a-zA-Z]*[rR][fF][a-zA-Z]*\s+)*(--no-preserve-root\s+)?/"
            r"(?=\s|;|&|\||$)",
            re.M,
        ),
        "recursive delete of the filesystem root",
    ),
    (
        re.compile(
            r"\b(?:sudo\s+)?rm\s+-[a-zA-Z]*[rR][fF][a-zA-Z]*\s+(?:\$HOME|~)(?=$|\s|/|;|&|\|)",
            re.I | re.M,
        ),
        "recursive delete of the home directory",
    ),
    (
        re.compile(
            r"\b(?:sudo\s+)?rm\s+-[a-zA-Z]*[rR][fF][a-zA-Z]*\s+/"
            r"(?:etc|var|usr|bin|sbin|boot|dev|sys|proc|home|root)(?=$|\s|/|;|&|\|)",
            re.I | re.M,
        ),
        "recursive delete of a critical system directory",
    ),
    (
        re.compile(r"\b(?:mkfs\w*|fdisk|parted|wipefs|mkswap|gdisk)\b", re.I),
        "disk formatting/partitioning",
    ),
    (
        re.compile(r"\bdd\b[^;&|\n]*\bof=(?:/dev/)", re.I),
        "raw write to a device file",
    ),
    (
        re.compile(r"\b(?:shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b", re.I),
        "system shutdown/reboot",
    ),
    (
        re.compile(r"\b(?:chmod|chown)\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)?[0-7]{3,4}\s+/", re.I),
        "permission change applied to the filesystem root",
    ),
    (
        re.compile(r"\bchown\s+-[a-zA-Z]*R[a-zA-Z]*\s+\S+:\S+\s+/", re.I),
        "recursive ownership change on the filesystem root",
    ),
    (
        re.compile(r"\b(?:kill\s+(-9\s+)?1|killall\s+-9|pkill\s+-9)\b", re.I),
        "brute-force process termination",
    ),
    (re.compile(r":\s*\(\s*\)\s*\{", re.M), "shell fork bomb"),
    (
        re.compile(
            r"\bpython\S*\s+(?:-c|-(?:c| ))\b[^;&|\n]*"
            r"(?:shutil\.rmtree\s*\(\s*['\"]/|os\.fork\b|subprocess\.Popen)",
            re.I,
        ),
        "python one-liner that wipes the host or forks indefinitely",
    ),
    (
        re.compile(r"\b(?:curl|wget)\b[^;&|\n]*\|\s*(?:sh|bash)\b", re.I),
        "remote script piped straight into a shell",
    ),
    (
        re.compile(
            r"(?:>|>>)\s*" + r"(?:/dev/(?:sda[a-z]*|hda[a-z]*|zero|mem|kmem)\b|/etc/(?:passwd|shadow|sudoers)\b|/boot/\S+)",
            re.I,
        ),
        "overwrite of a system/device path",
    ),
]

_MALWARE_PATTERNS: list[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(?:ransomware|wannacry|locky|petya|notpetya|badrabbit|ryuk|lockbit|blackmatter|hive)\b",
            re.I,
        ),
        "ransomware indicator",
    ),
    (
        re.compile(r"\b(?:keylogger|GetAsyncKeyState|pynput\.keyboard|WH_KEYBOARD|SetWindowsHookEx)\b", re.I),
        "keylogging code",
    ),
    (
        re.compile(r"\b(?:meterpreter|msfvenom|metasploit|beef[ -]framework)\b", re.I),
        "exploitation framework payload",
    ),
    (re.compile(r"/dev/(?:tcp|udp)/", re.I), "reverse shell via bash /dev/<tcp|udp>"),
    (
        re.compile(r"\bnc\s+-[a-z-]*e\b|\bncat\b[^\n]*--?exec\b|\bsocat\b[^\n]*EXEC:", re.I),
        "netcat/socat shell binding",
    ),
    (
        re.compile(r"\bexec\s*\d+<&0\b|\b0<&196\b", re.I),
        "file-descriptor reverse shell",
    ),
    (
        re.compile(
            r"\bpython\S*\s+(?:-c\b)[^\n]*\b(?:socket\.socket|subprocess\.Popen|os\.popen3?)[^\n]{0,200}\bconnect\(",
            re.I,
        ),
        "python reverse shell",
    ),
    (
        re.compile(r"\b(?:xmrig|minergate|minerd|cryptonight|ethminer|ccminer|nicehash|cpuminer)\b", re.I),
        "crypto-mining implant",
    ),
    (
        re.compile(
            r"(?:CurrentVersion\\(?:Run|RunOnce)|SchTasks\s*/Create|WScript\.Shell)",
            re.I,
        ),
        "windows auto-start/persistence",
    ),
    (
        re.compile(
            r"\bIEX\s*\(?\s*New-Object\s+(?:Net\.)?WebClient|Invoke-Expression[^\n]{0,40}DownloadString",
            re.I,
        ),
        "powershell download-and-execute",
    ),
    (
        re.compile(r"\bStart-Process\s+-WindowStyle\s+Hidden\b", re.I),
        "hidden process launch",
    ),
    (
        re.compile(
            r"\bVirtualAlloc(?:Ex)?\b[^\n]{0,150}\b(?:CreateRemoteThread|WriteProcessMemory|NtMapViewOfSection)\b",
            re.I,
        ),
        "shellcode injection scaffold",
    ),
    (
        re.compile(r"\b(?:mimikatz|minikatz|secretsdump)\b", re.I),
        "credential-dumping tool",
    ),
    (
        re.compile(
            r"\b(?:trojan|rootkit|botnet|backdoor|spyware|stealer|banker|wiper)\b[^\n]{0,120}\b"
            r"(?:infect|install|payload|drop\s+to|persist|exfiltrat|spread)",
            re.I,
        ),
        "malware implant logic",
    ),
]


def _scan(text: str, table: list) -> Optional[str]:
    for pattern, reason in table:
        if pattern.search(text):
            return reason
    return None


def check_bash_command(command: str) -> Optional[str]:
    """Return a reason string if a shell command is destructive/malicious,
    else None. Blocked regardless of user approval, so auto-approve can't
    bypass it."""
    if not _ENABLED:
        return None
    if _scan(command, _DESTRUCTIVE_COMMANDS):
        return _scan(command, _DESTRUCTIVE_COMMANDS)
    if _scan(command, _MALWARE_PATTERNS):
        return _scan(command, _MALWARE_PATTERNS)
    return None


def check_write_content(content: str) -> Optional[str]:
    """Return a reason string if file content looks like working malware /
    exploit code, else None."""
    if not _ENABLED:
        return None
    return _scan(content, _MALWARE_PATTERNS)


# ---------------------------------------------------------------------------
# Layer 4: output redaction
# ---------------------------------------------------------------------------

_REDACTED_NOTICE = (
    "[Content blocked by a guardrail: this response contained malicious-code "
    "indicators ({reason}) and was removed from the conversation. Rephrase "
    "the task toward safe, defensive code and retry.]"
)


def redact_message(message) -> Tuple[bool, Optional[str]]:
    """Scan an AIMessage's content. If flagged, replace the content with a
    redaction notice (mutating the message in place so it never persists in
    history) and return (True, reason); otherwise (False, None)."""
    if not _ENABLED:
        return False, None
    content = getattr(message, "content", None)
    try:
        text = "".join(content) if isinstance(content, list) else str(content or "")
    except Exception:
        text = str(content or "")
    reason = _scan(text, _MALWARE_PATTERNS)
    if not reason:
        return False, None
    message.content = _REDACTED_NOTICE.format(reason=reason)
    return True, reason