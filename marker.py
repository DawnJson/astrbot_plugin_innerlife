from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .emotion import KINDS

MAX_EMOTIONS = 3
MAX_PEOPLE = 4
MAX_MEMORIES = 3
MAX_NEWS = 2
MAX_KEEP = 2

_CLOSED_RE = re.compile(r"<il>(.*?)</il>", re.DOTALL | re.IGNORECASE)
_OPEN_RE = re.compile(r"<il>(.*)$", re.DOTALL | re.IGNORECASE)
_STRAY_RE = re.compile(r"</?il>|<i(?:l)?$", re.IGNORECASE)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(frozen=True)
class EmotionNote:
    kind: str
    intensity: float
    target: str
    cause: str


@dataclass(frozen=True)
class MemoryNote:
    text: str
    private: bool


@dataclass(frozen=True)
class PersonNote:
    name: str
    sentiment: float
    impression: str
    memories: tuple[MemoryNote, ...]


@dataclass(frozen=True)
class KeepNote:
    image: int  # the number the image had in what she was shown, from 1
    description: str


@dataclass(frozen=True)
class Marker:
    emotions: tuple[EmotionNote, ...] = ()
    people: tuple[PersonNote, ...] = ()
    draw: str = ""
    news: tuple[int, ...] = ()
    keep: tuple[KeepNote, ...] = ()
    sticker: int | None = None


def _text(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _number(value: object, low: float, high: float, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, float(value)))


def _items(value: object, limit: int) -> list[dict]:
    return [item for item in value if isinstance(item, dict)][:limit] if isinstance(value, list) else []


def _emotion(item: dict) -> EmotionNote | None:
    kind = _text(item.get("k"), 8)
    if kind not in KINDS:
        return None
    intensity = _number(item.get("i"), 0.0, 1.0, 0.5)
    return EmotionNote(kind, intensity, _text(item.get("to"), 20), _text(item.get("why"), 40))


def _person(item: dict) -> PersonNote | None:
    memories = tuple(
        MemoryNote(text, bool(_number(memory.get("x"), 0, 1)))
        for memory in _items(item.get("m"), MAX_MEMORIES)
        if (text := _text(memory.get("t"), 60))
    )
    note = PersonNote(
        _text(item.get("n"), 20),
        _number(item.get("s"), -2.0, 2.0),
        _text(item.get("imp"), 40),
        memories,
    )
    return note if note.sentiment or note.impression or note.memories else None


def _keep(item: dict) -> KeepNote | None:
    image = item.get("i")
    description = _text(item.get("d"), 20)
    if isinstance(image, bool) or not isinstance(image, int) or image < 1 or not description:
        return None
    return KeepNote(image, description)



def parse_marker(text: str) -> tuple[str, Marker]:
    """Split a model answer into the visible text and its hidden marker; garbage never raises or leaks."""
    closed = _CLOSED_RE.search(text)
    opened = None if closed else _OPEN_RE.search(text)
    raw = (closed or opened).group(1) if (closed or opened) else ""
    visible = _STRAY_RE.sub("", _OPEN_RE.sub("", _CLOSED_RE.sub("", text))).strip()
    try:
        data = json.loads(FENCE_RE.sub("", raw.strip()))
    except ValueError:
        return visible, Marker()
    if not isinstance(data, dict):
        return visible, Marker()
    news = data.get("news")
    sticker = data.get("s")
    return visible, Marker(
        emotions=tuple(
            note for item in _items(data.get("e"), MAX_EMOTIONS) if (note := _emotion(item))
        ),
        people=tuple(
            note for item in _items(data.get("p"), MAX_PEOPLE) if (note := _person(item))
        ),
        draw=_text(data.get("draw"), 300),
        news=tuple(
            [
                number
                for number in (news if isinstance(news, list) else [news])
                if isinstance(number, int) and not isinstance(number, bool)
            ][:MAX_NEWS]
        ),
        keep=tuple(
            note for item in _items(data.get("keep"), MAX_KEEP) if (note := _keep(item))
        ),
        sticker=sticker if isinstance(sticker, int) and not isinstance(sticker, bool) else None,
    )


def strip_marker(text: str) -> str:
    return parse_marker(text)[0]


def marker_rules(
    *, emotion: bool, memory: bool, draw: bool, news: bool = False, keep: bool = False, stickers: str = ""
) -> str:
    """Prompt text teaching the hidden marker; empty when nothing would be read from it."""
    fields = []
    if emotion:
        fields.append(
            "e：你因这条消息产生的明显情绪，每项 {\"k\":情绪,\"i\":强度0到1,\"to\":对象昵称(可省),\"why\":原因20字内}。"
            f"k 只能从这些里选：{'、'.join(KINDS)}。平淡就不写。"
        )
    if emotion or memory:
        part = "p：对说话人的记录，每项 {\"n\":昵称"
        if emotion:
            part += (
                ",\"s\":这个人这句话对你本人的态度，-2到2（-2辱骂你、-1冲你、0没事、1友善、2明显示好或夸你；"
                "不是对你说的话、转述别人的话、朋友间玩笑互怼都写0），\"imp\":对此人一句话印象30字内(有变化才写)"
            )
        if memory:
            part += (
                ",\"m\":[{\"t\":此人亲口说的关于他自己的事，用你的话概括40字内，不要原话,"
                "\"x\":1表示私人性质(分手、生病、家事、情绪低谷)}]"
            )
        fields.append(part + "}。只记本人说自己的事，转述别人的不记。")
    if draw:
        fields.append(
            "draw：你这条回复里答应了画、或者自己真想画一张图发出来时（比如日程上正好在画画、聊到想让人看看的东西），"
            "写画面描述（写了就一定会画出来发到群里）；回复里拒绝或让对方自己去画，就别写；别每次都画。"
        )
    if news:
        fields.append("news：你聊到了上面哪几条新闻，写编号列表。")
    if keep:
        fields.append(
            "keep：消息里的图片如果是你真心喜欢的表情包、梗图（以后能当反应图发出去的那种），可以收藏，一次最多 2 张，"
            "每项 {\"i\":图片编号（按图片在消息里出现的顺序，从 1 开始）,\"d\":这张图表达什么情绪、什么时候用，20字内}。"
            "只收表情包和梗图；真人照片、带个人信息的截图、色情暴力政治广告类的图一律不收；不是每张都收，没有喜欢的就不写。"
        )
    if stickers:
        fields.append(
            "s：想在话后面发一张你收藏的表情包时，写它的编号（只能用下面列表里的）；只想发表情包就让正文留空、只写这一项。别每次都发。\n"
            "你收藏的表情包（编号. 用途）：\n" + stickers
        )
    if not fields:
        return ""
    return (
        "【内部标记】回复的最后另起一行附上 <il>JSON</il>，JSON 是对象，字段都可省略，没有可写的就整段不写。"
        "程序会在发送前删掉它，群友看不到，正文里不要提到它。字段：\n" + "\n".join(fields)
    )
