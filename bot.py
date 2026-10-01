"""WhatsApp receipt/bill -> Splitwise bot with quick Y confirmation and CSV backup."""
from __future__ import annotations

import base64
import csv
import json
import logging
import os
import re
import tempfile
import time
from datetime import date
from pathlib import Path

import anthropic
import pillow_heif
from dotenv import load_dotenv
from PIL import Image
from splitwise import Splitwise
from splitwise.expense import Expense
from splitwise.user import ExpenseUser
from whatsapp_agent import WhatsAppAgentClient

import splits

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bot")
pillow_heif.register_heif_opener()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CSV_PATH = Path(os.getenv("CSV_PATH", "expenses.csv"))
MEMBERS = [m.strip() for m in os.environ["MEMBERS"].split(",") if m.strip()]
ME = os.environ.get("ME_NAME", MEMBERS[0])
GROUP_NAME = os.environ["SPLITWISE_GROUP_NAME"]

llm = anthropic.Anthropic()
wa = WhatsAppAgentClient()
sw = Splitwise(os.environ["SPLITWISE_CONSUMER_KEY"], os.environ["SPLITWISE_CONSUMER_SECRET"],
               api_key=os.environ["SPLITWISE_API_KEY"])

SYSTEM = """You turn receipts and bill messages into structured expense data.
Today is {today}. Group members: {members}. The sender is {me}.
Return ONLY a JSON object, no prose, with this shape:
{{
  "merchant": str,
  "date": "YYYY-MM-DD" (use today if unknown),
  "total": number (final amount paid, dollars),
  "tax": number or 0,
  "tip": number or 0,
  "paid_by": one of the member names (default {me}),
  "category": short label like Groceries, Dining, Gas, Utilities, Other,
  "mode": "equal" or "itemized",
  "people": [member names sharing the cost, for mode equal; all members if unspecified],
  "items": [{{"name": str, "price": number, "people": [member names] or [] for everyone}}] (mode itemized only; prices exclude tax and tip),
  "question": null, or a short question if a name is unknown or the amount is unreadable
}}
Rules: use "itemized" only when the caption assigns specific items to people.
"split equally", "everyone" or no instruction means all members. "except X" removes X.
Use only the member names given, matched case-insensitively. Never invent amounts."""


def parse(image_b64: str | None, mime: str | None, text: str,
          previous: dict | None = None) -> dict:
    content = []
    if image_b64:
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": mime or "image/jpeg",
                                   "data": image_b64}})
    prompt = text or "(no caption)"
    if previous:
        prompt = (f"Previous parse:\n{json.dumps(previous)}\n\n"
                  f"Apply this correction and return the full updated JSON: {text}")
    content.append({"type": "text", "text": prompt})
    r = llm.messages.create(
        model=MODEL, max_tokens=1500,
        system=SYSTEM.format(today=date.today().isoformat(), members=", ".join(MEMBERS), me=ME),
        messages=[{"role": "user", "content": content}])
    raw = next(b.text for b in r.content if b.type == "text").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


CLAUDE_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def load_receipt_image(media_id: str, msg_id: str, mime_type: str) -> tuple[str, str]:
    """Download an inbound media object and return (base64_data, mime) that
    Claude's vision API accepts, converting HEIC/other formats to JPEG."""
    path = Path(tempfile.gettempdir()) / f"receipt_{msg_id}"
    wa.download_media(media_id, path)
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    if mime_type in CLAUDE_IMAGE_MIMES:
        return base64.b64encode(data).decode(), mime_type
    import io
    img = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


def canon(name: str) -> str:
    for m in MEMBERS:
        if m.lower() == name.strip().lower():
            return m
    raise ValueError(f"Unknown name '{name}'. Members: {', '.join(MEMBERS)}")


def compute(p: dict) -> tuple[dict[str, int], int]:
    total = splits.to_cents(p["total"])
    if p.get("mode") == "itemized" and p.get("items"):
        items = [{**it, "people": [canon(n) for n in it.get("people", [])]} for it in p["items"]]
        shares = splits.itemized_split(items, splits.to_cents(p.get("tax", 0)),
                                       splits.to_cents(p.get("tip", 0)), MEMBERS)
    else:
        people = [canon(n) for n in (p.get("people") or MEMBERS)]
        shares = splits.equal_split(total, people)
    splits.check(shares, total)
    return shares, total


def summary(p: dict, shares: dict[str, int], total: int) -> str:
    payer = canon(p.get("paid_by") or ME)
    lines = [f"{p.get('merchant', 'Expense')} ({p.get('category', 'Other')}), {p['date']}",
             f"Total {splits.fmt(total)}, paid by {payer}"]
    if p.get("tax") or p.get("tip"):
        lines.append(f"(tax {splits.fmt(splits.to_cents(p.get('tax', 0)))}, "
                     f"tip {splits.fmt(splits.to_cents(p.get('tip', 0)))})")
    lines += [f"{n}: {splits.fmt(c)}" for n, c in shares.items()]
    lines.append("Reply Y to save, or tell me what to fix.")
    return "\n".join(lines)


def resolve_ids() -> tuple[int, dict[str, int]]:
    """Map first names to Splitwise user IDs using the group's member list."""
    group = next((g for g in sw.getGroups() if g.getName().lower() == GROUP_NAME.lower()), None)
    if not group:
        raise SystemExit(f"Splitwise group '{GROUP_NAME}' not found.")
    ids, missing = {}, []
    for name in MEMBERS:
        hit = next((m for m in group.getMembers()
                    if (m.getFirstName() or "").lower().startswith(name.lower())), None)
        if hit:
            ids[name] = hit.getId()
        else:
            missing.append(name)
    if missing:
        raise SystemExit(f"Not found in group '{GROUP_NAME}': {', '.join(missing)}. "
                         "Check spelling or add them to the group.")
    return group.getId(), ids


def save(p: dict, shares: dict[str, int], total: int, group_id: int, ids: dict[str, int]) -> int:
    payer = canon(p.get("paid_by") or ME)
    e = Expense()
    e.setCost(f"{total / 100:.2f}")
    e.setDescription(p.get("merchant") or "Expense")
    e.setDetails(f"Category: {p.get('category', 'Other')} (added via WhatsApp bot)")
    e.setDate(p["date"])
    e.setGroupId(group_id)
    users = []
    for name in MEMBERS:
        owed = shares.get(name, 0)
        paid = total if name == payer else 0
        if owed == 0 and paid == 0:
            continue
        u = ExpenseUser()
        u.setId(ids[name])
        u.setPaidShare(f"{paid / 100:.2f}")
        u.setOwedShare(f"{owed / 100:.2f}")
        users.append(u)
    e.setUsers(users)
    created, errors = sw.createExpense(e)
    if errors:
        raise RuntimeError(f"Splitwise rejected the expense: {errors.getErrors()}")
    new_id = created.getId()
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["splitwise_id", "date", "merchant", "category", "total", "paid_by",
                        *MEMBERS])
        w.writerow([new_id, p["date"], p.get("merchant"), p.get("category"), f"{total/100:.2f}",
                    payer, *[f"{shares.get(m, 0)/100:.2f}" for m in MEMBERS]])
    return new_id


def main() -> None:
    group_id, ids = resolve_ids()
    log.info("Ready. Group %s, members %s", GROUP_NAME, list(ids))
    pending: dict[str, dict] = {}   # sender -> {parsed, shares, total, image_b64, mime}
    last_saved: dict[str, int] = {}

    while True:
        try:
            run(group_id, ids, pending, last_saved)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Listen loop crashed; reconnecting in 5s")
            time.sleep(5)


def run(group_id: int, ids: dict[str, int], pending: dict[str, dict],
        last_saved: dict[str, int]) -> None:
    for msg in wa.listen(auto_mark_read=True):
        log.info("incoming type=%s from=%s", msg.type, msg.from_)
        sender = msg.from_
        caption = (msg.image or msg.document).caption if (msg.image or msg.document) else None
        text = (msg.text or caption or "").strip()
        try:
            low = text.lower()
            if msg.type == "text" and low in ("undo", "delete last") and sender in last_saved:
                sw.deleteExpense(last_saved.pop(sender))
                wa.reply_text(msg, "Deleted the last expense from Splitwise. "
                                   "(The CSV row stays; remove it by hand if needed.)")
                continue
            if sender in pending and msg.type == "text":
                if low in ("y", "yes", "ok", "save") and pending[sender]["shares"]:
                    st = pending.pop(sender)
                    eid = save(st["parsed"], st["shares"], st["total"], group_id, ids)
                    last_saved[sender] = eid
                    wa.reply_text(msg, f"Saved to Splitwise ({splits.fmt(st['total'])}). "
                                       "Reply UNDO to delete.")
                    continue
                if low in ("n", "no", "cancel"):
                    pending.pop(sender)
                    wa.reply_text(msg, "Cancelled. Nothing saved.")
                    continue
                st = pending[sender]
                p = parse(st["image_b64"], st["mime"], text, previous=st["parsed"])
            elif msg.type == "image" and msg.image:
                b64, mime = load_receipt_image(msg.image.id, msg.id, msg.image.mime_type)
                p = parse(b64, mime, text)
                st = {"image_b64": b64, "mime": mime}
            elif msg.type == "document" and msg.document:
                b64, mime = load_receipt_image(msg.document.id, msg.id, msg.document.mime_type)
                p = parse(b64, mime, text)
                st = {"image_b64": b64, "mime": mime}
            elif msg.type == "text" and text:
                p = parse(None, None, text)
                st = {"image_b64": None, "mime": None}
            else:
                continue

            if p.get("question"):
                pending[sender] = {**st, "parsed": p, "shares": {}, "total": 0}
                wa.reply_text(msg, p["question"])
                continue
            shares, total = compute(p)
            pending[sender] = {**st, "parsed": p, "shares": shares, "total": total}
            wa.reply_text(msg, summary(p, shares, total))
        except Exception as ex:  # keep the bot alive; report the problem in chat
            log.exception("failed")
            wa.reply_text(msg, f"Sorry, that failed: {ex}. Nothing was saved.")


if __name__ == "__main__":
    main()
