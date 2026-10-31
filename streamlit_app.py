"""IssuePilot demo UI.

Point it at any public GitHub repo: it learns that repo's labels from past issues, then triages
an issue you type (labels, likely duplicates, draft reply). Approving a draft here is a dry run:
it shows what would be posted without touching GitHub.

    streamlit run streamlit_app.py
"""

import os

import streamlit as st

from app.drafts import draft_reply
from app.github import fetch_issues
from app.labels import LabelSuggester
from app.similarity import SimilarityIndex

st.set_page_config(page_title="IssuePilot", page_icon="🧭", layout="wide")

# Optional secrets (Streamlit Cloud → App settings → Secrets)
for key in ("GITHUB_TOKEN", "GROQ_API_KEY", "LLM_MODEL"):
    try:
        if key in st.secrets and not os.getenv(key):
            os.environ[key] = st.secrets[key]
    except Exception:   # no secrets file locally
        pass

EXAMPLES = {
    "psf/requests": ("SSL error when using a proxy with verify=False",
                     "Getting SSLError: certificate verify failed when sending requests through a corporate proxy, even with verify=False."),
    "fastapi/fastapi": ("Background task not running after response",
                        "Tasks added with BackgroundTasks never execute when the endpoint raises HTTPException."),
    "streamlit/streamlit": ("st.dataframe does not show column tooltips",
                            "Column help text set in column_config is not displayed on hover in the latest version."),
}


@st.cache_data(ttl=3600, show_spinner=False)
def load_issues(repo, max_issues):
    demo = os.getenv("ISSUEPILOT_DEMO_FILE")   # offline/testing: load issues from a JSON file
    if demo:
        import json
        return json.load(open(demo))
    return fetch_issues(repo, max_issues)


@st.cache_resource(show_spinner=False)
def build_models(repo, max_issues, _issues):
    return SimilarityIndex(_issues, backend="auto"), LabelSuggester(_issues)


# ---------- Sidebar ----------
with st.sidebar:
    st.title("🧭 IssuePilot")
    st.caption("GitHub issue triage with a human in the loop")
    repo = st.text_input("Public repository", value="psf/requests", help="owner/name")
    max_issues = st.slider("Past issues to learn from", 100, 1000, 300, step=100)
    load = st.button("Load repository", type="primary", width="stretch")
    st.divider()
    st.markdown(
        "**How it works**\n"
        "1. Fetches the repo's past issues\n"
        "2. Trains a label classifier on them\n"
        "3. Indexes them for duplicate search\n"
        "4. Drafts a reply; a maintainer approves before anything is posted\n\n"
        "[Source on GitHub](https://github.com/Abhisheklpu1731/issuepilot)"
    )

if load or "issues" not in st.session_state:
    try:
        with st.spinner(f"Fetching issues from {repo}…"):
            issues = load_issues(repo.strip(), max_issues)
        with st.spinner("Training label classifier and building the duplicate index…"):
            index, labeler = build_models(repo.strip(), max_issues, issues)
        st.session_state.update(repo=repo.strip(), issues=issues, index=index, labeler=labeler, last=None)
    except Exception as e:
        st.error(f"Could not load `{repo}`: {e}. Check the name, or add a GITHUB_TOKEN secret if you hit the rate limit.")
        st.stop()

issues = st.session_state["issues"]
index, labeler = st.session_state["index"], st.session_state["labeler"]
st.session_state.setdefault("queue", [])

c1, c2, c3 = st.columns(3)
c1.metric("Issues indexed", len(issues))
c2.metric("Labels learned", len(labeler.labels))
c3.metric("Similarity backend", index.backend)

tab_triage, tab_queue, tab_eval = st.tabs(["Triage an issue", f"Approval queue ({len(st.session_state.queue)})", "Evaluate on history"])

# ---------- Triage ----------
with tab_triage:
    ex_title, ex_body = EXAMPLES.get(st.session_state["repo"], ("", ""))
    title = st.text_input("Issue title", value=ex_title)
    body = st.text_area("Issue body", value=ex_body, height=140)
    if st.button("Triage", type="primary", disabled=not title.strip()):
        issue = {"title": title, "body": body, "user": "new-contributor"}
        labels = labeler.suggest(issue)
        dups = index.search(issue, k=3, threshold=0.25)
        reply, source = draft_reply(issue, labels, dups)
        st.session_state.last = {"issue": issue, "labels": labels, "dups": dups, "reply": reply, "source": source}

    last = st.session_state.get("last")
    if last:
        left, right = st.columns([1, 1])
        with left:
            st.subheader("Suggested labels")
            if last["labels"]:
                for label, score in last["labels"]:
                    st.progress(min(score, 1.0), text=f"**{label}** · {score:.2f}")
            else:
                st.info("Not enough labelled history in this repo to learn labels.")
            st.subheader("Possible duplicates")
            if last["dups"]:
                for d in last["dups"]:
                    st.markdown(f"- [#{d.number} {d.title}]({d.url}) · similarity **{d.score:.2f}**")
            else:
                st.write("No close matches.")
        with right:
            st.subheader("Draft reply")
            st.caption(f"Written by an LLM ({os.getenv('LLM_MODEL') or 'Groq'})" if last["source"] == "llm" else "Template reply (add a GROQ_API_KEY secret for LLM drafts)")
            edited = st.text_area("Edit before sending to the queue", value=last["reply"], height=200, key="edit_reply")
            chosen = st.multiselect("Labels to apply", options=labeler.labels or [l for l, _ in last["labels"]],
                                    default=[l for l, _ in last["labels"]])
            if st.button("Send to approval queue"):
                st.session_state.queue.append({"title": last["issue"]["title"], "reply": edited, "labels": chosen, "status": "pending"})
                st.success("Added to the approval queue.")

# ---------- Approval queue ----------
with tab_queue:
    st.caption("Nothing is posted to GitHub from this demo. Approving shows what the API would post.")
    if not st.session_state.queue:
        st.write("Queue is empty. Triage an issue and send it here.")
    for i, item in enumerate(st.session_state.queue):
        with st.container(border=True):
            st.markdown(f"**{item['title']}** · `{item['status']}`")
            st.markdown(item["reply"])
            st.caption("Labels: " + (", ".join(item["labels"]) or "none"))
            if item["status"] == "pending":
                a, r, _ = st.columns([1, 1, 4])
                if a.button("Approve", key=f"a{i}"):
                    item["status"] = "approved (dry run)"
                    st.rerun()
                if r.button("Reject", key=f"r{i}"):
                    item["status"] = "rejected"
                    st.rerun()

# ---------- Evaluation ----------
with tab_eval:
    st.write("Backtest on this repo's own history: train on the oldest 80% of labelled issues, "
             "test label suggestions on the newest 20%.")
    if st.button("Run evaluation"):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent / "scripts"))
        from evaluate import eval_labels
        with st.spinner("Evaluating…"):
            res = eval_labels(issues)
        m1, m2, m3 = st.columns(3)
        m1.metric("Precision", f"{res['precision']:.2f}")
        m2.metric("Recall", f"{res['recall']:.2f}")
        m3.metric("Test issues", res["test"])
        st.caption("Labels evaluated: " + ", ".join(res["labels"]))
