# WhatsApp to Splitwise bot

Send a receipt photo (or a text like "gas $50") to your WhatsApp agent chat. The bot reads it
with Claude, works out who owes what, shows a breakdown, and saves to Splitwise after you reply **Y**.
Every saved expense is also appended to `expenses.csv`.

## Setup
1. Python 3.10+. Then: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill it in:
   - **WHATSAPP_API_KEY**: WhatsApp > Settings > Agents > your agent > Chat info > API key
   - **ANTHROPIC_API_KEY**: from the Claude Console
   - **Splitwise keys**: create an app at https://secure.splitwise.com/apps, copy the consumer key,
     consumer secret, and API key
   - **SPLITWISE_GROUP_NAME**: exact group name. Everyone in `MEMBERS` must be in that group.
3. Run: `python bot.py`. Keep it running (a Mac left on, or a small cloud VM with `systemd`/`tmux`).
4. If you ever see "Not found in group", fix the spelling in `MEMBERS` to match Splitwise first names.

## Using it
| You send | Result |
|---|---|
| `gas $50` | Equal split across all 5, you paid |
| Receipt photo | Equal split of the total |
| Receipt photo + "I had pasta, Vijay and Guhan pizza, rest equal" | Itemized, tax and tip proportional |
| `Y` | Save to Splitwise and CSV |
| "Aravind didn't have dessert" | Recalculates and asks again |
| `N` | Cancel |
| `UNDO` | Deletes your last saved expense from Splitwise (CSV row stays) |

Add "Vijay paid" to a message if someone else paid.

## Notes
- Money math runs in code in whole cents, and shares are checked to equal the receipt total before saving.
- WhatsApp agent chats are not end-to-end encrypted. Receipt images are sent to Claude for reading.
- Meta's agent API is in beta and `whatsapp-agent` is a community SDK, so expect some debugging on the first run.
- Test with `python -m pytest test_splits.py`.
