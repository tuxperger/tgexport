"""Channel posts → LLM fine-tuning dataset (JSONL). Offline, DB only.

Goal: teach a model to write posts in the style of the exported channels.
Every post becomes one example, in one of two formats:

  chat  {"messages": [{"role": "system", ...}, {"role": "user", ...},
                      {"role": "assistant", "content": <post>}]}
        instruction tuning (HF TRL SFTTrainer, Unsloth, axolotl, OpenAI API)
  text  {"text": <post>}
        plain completion / continued pretraining of a base model

Post text is Telethon's markdown rendering, so bold, italics and links survive.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiosqlite

from tgexport.core.config import DialogType
from tgexport.storage.models import Chat, Message
from tgexport.storage.repos.chats import get_all_chats
from tgexport.storage.repos.messages import get_chat_messages_plain

logger = logging.getLogger(__name__)

DatasetFormat = Literal["chat", "text"]
DATASET_FORMATS: tuple[DatasetFormat, ...] = ("chat", "text")

DEFAULT_SYSTEM_PROMPT = "Ты автор Telegram-канала «{chat}». Пиши посты в его стиле."
DEFAULT_USER_PROMPT = "Напиши новый пост для канала."


@dataclass(frozen=True)
class DatasetOptions:
    types: tuple[DialogType, ...] = ("channel",)
    chat_ids: tuple[int, ...] | None = None
    format: DatasetFormat = "chat"
    # "{chat}" is replaced with the channel title in both prompts.
    system_prompt: str | None = DEFAULT_SYSTEM_PROMPT
    user_prompt: str = DEFAULT_USER_PROMPT
    # Posts shorter than this (after stripping) are skipped: "+", "🔥", bare links.
    min_chars: int = 40
    include_forwards: bool = False
    val_ratio: float = 0.05
    seed: int = 42


@dataclass(frozen=True)
class DatasetStats:
    channels: int
    posts: int
    train: int
    val: int
    duplicates: int


def select_posts(messages: list[Message], opts: DatasetOptions) -> list[str]:
    """Post texts usable for training, in chronological order.

    Album items without a caption, service messages, short posts and (unless
    enabled) reposts from other channels are dropped.
    """
    posts: list[str] = []
    for msg in messages:
        if msg.service_type or (msg.fwd_from_name and not opts.include_forwards):
            continue
        text = (msg.text or "").strip()
        if len(text) >= opts.min_chars:
            posts.append(text)
    return posts


def make_example(chat: Chat, post: str, opts: DatasetOptions) -> dict[str, object]:
    if opts.format == "text":
        return {"text": post}
    messages: list[dict[str, str]] = []
    if opts.system_prompt:
        messages.append(
            {"role": "system", "content": opts.system_prompt.replace("{chat}", chat.title)}
        )
    messages.append({"role": "user", "content": opts.user_prompt.replace("{chat}", chat.title)})
    messages.append({"role": "assistant", "content": post})
    return {"messages": messages}


def _write_jsonl(path: Path, examples: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")


async def export_dataset(
    conn: aiosqlite.Connection, out_dir: Path, opts: DatasetOptions
) -> DatasetStats:
    """Write train.jsonl / val.jsonl under out_dir."""
    chats = [c for c in await get_all_chats(conn) if c.type in opts.types]
    if opts.chat_ids is not None:
        wanted = set(opts.chat_ids)
        chats = [c for c in chats if c.id in wanted]

    examples: list[dict[str, object]] = []
    seen: set[str] = set()
    duplicates = 0
    channels = 0
    for chat in chats:
        count = 0
        for post in select_posts(await get_chat_messages_plain(conn, chat.id), opts):
            if post in seen:  # reposted announcements, pinned rules, cross-posts
                duplicates += 1
                continue
            seen.add(post)
            examples.append(make_example(chat, post, opts))
            count += 1
        if count:
            channels += 1
            logger.info("%s: %d post(s)", chat.title, count)

    random.Random(opts.seed).shuffle(examples)
    n_val = round(len(examples) * opts.val_ratio) if len(examples) > 1 else 0
    val, train = examples[:n_val], examples[n_val:]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "train.jsonl", train)
    _write_jsonl(out_dir / "val.jsonl", val)
    return DatasetStats(
        channels=channels,
        posts=len(examples),
        train=len(train),
        val=len(val),
        duplicates=duplicates,
    )
