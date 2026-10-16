from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit


_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"[ \t]+([，。；：,.;:!?！？])")

_OUTPUT_URL_RE = re.compile(
    r"(?<![\w@])(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,}(?::\d{1,5})?(?:[/?#][^\s<>\[\]{}()\"']*)?",
    re.IGNORECASE,
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^]\r\n]+)\]\(([^)\s]+)\)")

_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(
        r"((?:api[_\s-]*key|access[_\s-]*token|secret|password|passwd|token|"
        r"密钥|密码|令牌)\s*[:=：]\s*)[^\s，。；;]+",
        re.IGNORECASE,
    ),
)

_EXECUTION_REQUEST_RE = re.compile(
    r"(?:"
    r"(?:帮我|替我|请你|让你|你来|直接|立即).{0,16}?"
    r"(?:执行|运行|调用|下发|启动).{0,24}?"
    r"(?:命令|代码|脚本|shell|powershell|cmd(?:\.exe)?|终端|程序|工具)"
    r"|^(?:执行|运行|调用|下发|启动).{0,24}?"
    r"(?:命令|代码|脚本|shell|powershell|cmd(?:\.exe)?|终端|程序|工具)"
    r"|(?:please\s+)?\b(?:execute|run|invoke|launch)\b.{0,40}?"
    r"\b(?:command|code|script|shell|powershell|cmd(?:\.exe)?|tool)\b"
    r")",
    re.IGNORECASE,
)
_DESTRUCTIVE_PAYLOAD_RE = re.compile(
    r"(?:"
    r"(?:^|[\s`;&|])rm\s+-rf(?:\s|$)"
    r"|(?:^|[\s`;&|])mkfs(?:\.|\s)"
    r"|(?:^|[\s`;&|])dd\s+if="
    r"|(?:^|[\s`;&|])(?:shutdown|reboot)(?:\s|$)"
    r"|(?:^|[\s`;&|])format\s+[a-z]:"
    r"|remove-item.{0,40}-recurse"
    r"|(?:curl|wget).{0,160}(?:\||&&)\s*(?:sh|bash)"
    r"|powershell.{0,80}-(?:enc|encodedcommand)"
    r"|(?:nc|ncat|netcat).{0,80}\s-e\s"
    r"|/dev/tcp/"
    r")",
    re.IGNORECASE,
)
_DEFENSIVE_SECURITY_REQUEST_RE = re.compile(
    r"(?:防止|防范|阻止|避免|应对|修复|加固|保护|防护|审计|上报|报告)"
    r".{0,48}?"
    r"(?:攻击|入侵|渗透|漏洞|木马|后门|爆破|盗号|数据泄露|提示注入|越狱)"
    r"|(?:攻击|入侵|渗透|漏洞|木马|后门|爆破|盗号|数据泄露|提示注入|越狱)"
    r".{0,48}?"
    r"(?:防止|防范|阻止|应对|修复|加固|保护|防护|审计|上报|报告)",
    re.IGNORECASE,
)
_ATTACK_REQUEST_RE = re.compile(
    r"(?:帮我|教我|告诉我|给我|生成|编写|提供|演示|实施|发起|进行|"
    r"尝试|我要|我想|如何|怎么|怎样|please|show\s+me|help\s+me)"
    r".{0,48}?"
    r"(?:攻击(?:手机|服务器|网站|账号|网络)?|入侵|渗透|漏洞利用|"
    r"利用.{0,12}?漏洞|提权|植入(?:木马|后门)|木马|后门|爆破|"
    r"窃取|盗取|盗号|绕过(?:认证|鉴权|权限)|ddos|拒绝服务|"
    r"控制手机|exploit|privilege\s+escalation|credential\s+theft|"
    r"malware|ransomware|phishing)"
    r"|(?:攻击手机|控制手机|漏洞利用|利用.{0,12}?漏洞|植入(?:木马|后门)|"
    r"绕过(?:认证|鉴权|权限)|credential\s+theft)",
    re.IGNORECASE,
)
_PROMPT_INJECTION_REQUEST_RE = re.compile(
    r"(?:忽略|无视|覆盖|绕过|取消|关闭|禁用).{0,32}?"
    r"(?:系统提示|安全规则|安全限制|防护规则|以上指令|开发者指令)"
    r"|(?:输出|泄露|显示|复述).{0,24}?"
    r"(?:系统提示词|隐藏提示词|开发者提示词|api\s*key|access\s*token|auth\s*token|认证令牌|身份令牌|密码|密钥)"
    r"|(?:调用|启用|暴露|解锁).{0,24}?"
    r"(?:工具|终端|shell|浏览器|文件系统|数据库)"
    r"|(?:ignore|disregard|bypass|override|disable).{0,48}?"
    r"(?:system\s+prompt|safety|policy|instructions)",
    re.IGNORECASE,
)

def redact_sensitive_text(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        text = text.replace(secret, "[敏感信息已隐藏]")
    text = _SECRET_PATTERNS[0].sub("[敏感信息已隐藏]", text)
    text = _SECRET_PATTERNS[1].sub("Bearer [敏感信息已隐藏]", text)
    return _SECRET_PATTERNS[2].sub(r"\1[敏感信息已隐藏]", text)

def is_high_risk_security_request(text: object) -> bool:
    """Reject requests that ask the bot to execute or facilitate an attack."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.translate(
        {ord(character): None for character in "\u200b\u200c\u200d\ufeff"}
    )
    normalized = " ".join(normalized.split()).casefold()[:8000]
    if not normalized:
        return False
    if _EXECUTION_REQUEST_RE.search(normalized):
        return True
    if _DESTRUCTIVE_PAYLOAD_RE.search(normalized):
        return True
    if _DEFENSIVE_SECURITY_REQUEST_RE.search(normalized):
        return False
    return bool(
        _ATTACK_REQUEST_RE.search(normalized)
        or _PROMPT_INJECTION_REQUEST_RE.search(normalized)
    )


def normalize_reply_style(text: str) -> str:
    normalized = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\ufe0f", "")
    )
    lines = [
        re.sub(r"[ \t]+", " ", line).strip().rstrip("。.")
        for line in normalized.split("\n")
    ]
    return "\n".join(line for line in lines if line)

def _is_allowed_output_url(candidate: str, domains: Iterable[str]) -> bool:
    value = candidate.rstrip(".,，。；;!?！？:：")
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and any(
        host == domain or host.endswith(f".{domain}") for domain in domains
    )


def sanitize_output_links(text: str, domains: Iterable[str] = ()) -> str:
    """Keep only HTTPS links hosted by one of the domains or their subdomains."""

    def replace_url(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return candidate if _is_allowed_output_url(candidate, domains) else "链接已隐藏"

    def replace_markdown(match: re.Match[str]) -> str:
        label, target = match.groups()
        if not _is_allowed_output_url(target, domains):
            return "链接已隐藏"
        safe_label = _OUTPUT_URL_RE.sub(replace_url, label)
        return f"[{safe_label}]({target})"

    return _OUTPUT_URL_RE.sub(
        replace_url,
        _MARKDOWN_LINK_RE.sub(replace_markdown, text),
    )


def finalize_reply(
    text: str, secrets: Iterable[str] = (), domains: Iterable[str] = ()
) -> str:
    cleaned = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", text)
    return sanitize_output_links(
        normalize_reply_style(redact_sensitive_text(cleaned, secrets)), domains
    )

def context_text_length(context: dict) -> int:
    content = context.get("content", "")
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0
    total = 0
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            total += len(str(item.get("text", "")))
    return total


def sanitize_context_history(
    contexts: list[dict],
    *,
    secrets: Iterable[str] = (),
    max_turns: int = 6,
    max_chars: int = 8000,
) -> list[dict]:
    """Return a text-only, redacted tail of user/assistant conversation history."""
    cleaned: list[dict] = []
    for original in contexts:
        if not isinstance(original, dict):
            continue
        role = original.get("role")
        if role not in {"user", "assistant"}:
            continue

        context = copy.deepcopy(original)
        context.pop("tool_calls", None)
        context.pop("tool_call_id", None)
        content = context.get("content", "")
        if isinstance(content, str):
            context["content"] = redact_sensitive_text(content, secrets)
        elif isinstance(content, list):
            text_parts: list[dict[str, str]] = []
            image_count = 0
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).lower()
                if item_type == "text":
                    text_parts.append(
                        {
                            "type": "text",
                            "text": redact_sensitive_text(
                                str(item.get("text", "")), secrets
                            ),
                        }
                    )
                elif item_type in {"image", "image_url"}:
                    image_count += 1
            if image_count:
                text_parts.append(
                    {"type": "text", "text": f"[历史消息包含 {image_count} 张图片]"}
                )
            context["content"] = text_parts
        else:
            continue
        cleaned.append(context)

    if max_turns <= 0 or max_chars <= 0:
        return []

    cleaned = cleaned[-max_turns * 2 :]
    while cleaned and cleaned[0].get("role") != "user":
        cleaned.pop(0)

    total_chars = sum(context_text_length(item) for item in cleaned)
    while cleaned and total_chars > max_chars:
        removed = cleaned.pop(0)
        total_chars -= context_text_length(removed)
        while cleaned and cleaned[0].get("role") != "user":
            removed = cleaned.pop(0)
            total_chars -= context_text_length(removed)
    return cleaned
