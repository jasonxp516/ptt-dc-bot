# Privacy and data handling

PTT-DC-Bot stores operational data in SQLite so subscriptions, delivery
deduplication, and short-term conversations survive restarts.

## Data stored

- Subscriber Discord, Telegram, and email IDs.
- Watch keywords and AI fuzzy rules.
- Short-term AI conversation memory, retained for 30 minutes and limited to
  the most recent 6 turns.
- `seen_articles` and delivery history, retained for 14 days.

## External services

- The bot fetches public PTT web pages over HTTPS to monitor boards and read
  article content needed for matching.
- When `GROQ_API_KEY` is configured, matched content and user natural-language
  chat prompts may be sent to the Groq API for semantic filtering and
  natural-language chat. Groq is not contacted when the key is absent.

## Deletion rights

Run `/forget_me` to immediately and permanently delete your personal rules,
delivery records, and conversation memory for that user. This does not delete
server-wide rules owned by other subscribers or the public PTT pages.

