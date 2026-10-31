"""Draft a reply to a new issue.

If GROQ_API_KEY is set, an LLM writes the reply grounded in the suggested labels and
likely duplicates. Otherwise a deterministic template is used. Either way the draft is
only stored as pending: nothing is posted until a maintainer approves it.
"""

import os

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")


def template_reply(issue, labels, duplicates):
    lines = [f"Thanks for opening this, @{issue.get('user', 'there')}!"]
    if duplicates:
        refs = ", ".join(f"#{d.number}" for d in duplicates)
        lines.append(f"This looks related to {refs}. Could you check whether those cover your case?")
    if labels and labels[0][0] == "bug":
        lines.append("To help us reproduce it, please share your version, OS and the exact steps or error output.")
    lines.append("A maintainer will take a look soon.")
    return "\n\n".join(lines)


def llm_reply(issue, labels, duplicates, api_key):
    dup_text = "\n".join(f"- #{d.number}: {d.title} (similarity {d.score})" for d in duplicates) or "none"
    label_text = ", ".join(l for l, _ in labels) or "none"
    prompt = (
        "You are a friendly open-source maintainer. Write a short first reply (max 90 words) to this "
        "new GitHub issue. Mention possible duplicates only if listed. Ask for missing reproduction "
        "details if it is a bug. Do not promise fixes or timelines.\n\n"
        f"Title: {issue.get('title')}\nBody: {(issue.get('body') or '')[:2000]}\n"
        f"Suggested labels: {label_text}\nPossible duplicates:\n{dup_text}"
    )
    r = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def draft_reply(issue, labels, duplicates):
    key = os.getenv("GROQ_API_KEY")
    if key:
        try:
            return llm_reply(issue, labels, duplicates, key), "llm"
        except httpx.HTTPError:
            pass   # fall back to the template rather than failing triage
    return template_reply(issue, labels, duplicates), "template"
