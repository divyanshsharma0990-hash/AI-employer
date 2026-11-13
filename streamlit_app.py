import asyncio, base64
import streamlit as st
import core

st.set_page_config(page_title="AI Employer", page_icon="🧑‍💼", layout="wide")

# label, base URL (or website for web-login), suggested model (leave empty and press "Load model list")
PRESETS = {
    "gemini": ("Google Gemini (free tier)", "https://generativelanguage.googleapis.com/v1beta/openai", ""),
    "groq": ("Groq (free tier, Llama and others)", "https://api.groq.com/openai/v1", ""),
    "openrouter": ("OpenRouter (has free models)", "https://openrouter.ai/api/v1", ""),
    "openai": ("OpenAI (ChatGPT)", "https://api.openai.com/v1", ""),
    "anthropic": ("Anthropic (Claude)", "", ""),
    "xai": ("xAI (Grok)", "https://api.x.ai/v1", ""),
    "custom": ("Other (OpenAI-compatible)", "", ""),
    "manual": ("Web login (copy and paste, no API)", "https://chatgpt.com", ""),
}
FILE_TYPES = ["png", "jpg", "jpeg", "webp", "gif", "pdf", "docx", "txt", "md", "csv"]

# ---------- login ----------
if "user" not in st.session_state:
    st.title("AI Employer")
    first_run = not core.has_users()
    st.write("Create the admin account." if first_run else "Log in to manage your AI team.")
    with st.form("auth"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Create admin account" if first_run else "Log in"):
            try:
                core.create_admin(u, p) if first_run else core.check_login(u, p)
                st.session_state.user = u.strip()
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    st.stop()

agents = core.list_agents()
cid = st.session_state.get("cid")


def model_picker(pfx, provider, base_url, get_key, current):
    """Text box for the model, or a dropdown once the provider's model list is loaded."""
    if st.button("Load model list", key=pfx + "_load"):
        try:
            st.session_state[pfx + "_models"] = core.fetch_models(provider, base_url, get_key())
        except ValueError as e:
            st.error(str(e))
    models = st.session_state.get(pfx + "_models")
    if models:
        opts = models if (current in models or not current) else [current] + models
        return st.selectbox("Model", opts, index=opts.index(current) if current in opts else 0, key=pfx + "_sel")
    return st.text_input("Model", value=current, key=pfx + "_txt")


# ---------- sidebar ----------
with st.sidebar:
    st.subheader("Chats")
    if st.button("New chat", use_container_width=True, type="primary"):
        st.session_state.cid = core.new_conv()
        st.rerun()
    convs = core.list_convs()
    for c in convs:
        left, right = st.columns([5, 1])
        if left.button(c["title"], key=f"c{c['id']}", use_container_width=True):
            st.session_state.cid = c["id"]
            st.rerun()
        if right.button("🗑", key=f"x{c['id']}", help="Delete this chat"):
            core.del_conv(c["id"])
            if st.session_state.get("cid") == c["id"]:
                st.session_state.cid = None
            st.rerun()
    if st.session_state.get("confirm_all"):
        st.warning("Delete ALL chat history?")
        y, n = st.columns(2)
        if y.button("Yes, delete all", key="yes_all"):
            core.del_all_convs()
            st.session_state.cid = None
            st.session_state.confirm_all = False
            st.rerun()
        if n.button("Cancel", key="no_all"):
            st.session_state.confirm_all = False
            st.rerun()
    elif convs and st.button("Delete all chats", key="del_all", use_container_width=True):
        st.session_state.confirm_all = True
        st.rerun()

    st.subheader("Your AI team")
    for a in agents:
        manual = a["provider"] == "manual"
        with st.expander(f"{a['name']} · {'web login' if manual else a['model']}"):
            pfx = f"e{a['id']}"
            url = st.text_input("Website to open" if manual else "Base URL", value=a["base_url"], key=pfx + "_url")
            new_key = "" if manual else st.text_input("New API key (leave empty to keep)", type="password", key=pfx + "_key")
            model = a["model"] if manual else model_picker(
                pfx, a["provider"], url, lambda: new_key or core.get_key(a["id"]), a["model"])
            role = st.text_input("Role", value=a["role"], key=pfx + "_role")
            b1, b2, b3 = st.columns(3)
            if b1.button("Save", key=pfx + "_save"):
                core.update_agent(a["id"], model, url, role, new_key)
                st.success("Saved")
            if b2.button("Test", key=pfx + "_test"):
                with st.spinner("Testing..."):
                    ok, msg = asyncio.run(core.test_agent(a["id"]))
                (st.success if ok else st.error)(msg[:200])
            if b3.button("Remove", key=pfx + "_del"):
                core.del_agent(a["id"])
                st.rerun()

    with st.expander("Add an AI employee", expanded=not agents):
        prov = st.selectbox("Provider", list(PRESETS), format_func=lambda k: PRESETS[k][0], key="new_prov")
        pfx = f"new_{prov}"
        name = st.text_input("Name (no spaces)", placeholder="Gemini", key=pfx + "_name")
        key = ""
        if prov == "manual":
            url = st.text_input("Website to open", value=PRESETS[prov][1], key=pfx + "_url")
            model = ""
            st.caption("Log in on the site yourself. The app gives you the prompt to paste and you paste the reply back.")
        else:
            key = st.text_input("API key", type="password", key=pfx + "_key")
            url = st.text_input("Base URL", value=PRESETS[prov][1], key=pfx + "_url")
            model = model_picker(pfx, prov, url, lambda: st.session_state.get(pfx + "_key", ""), PRESETS[prov][2])
        role = st.text_input("Role (optional)", placeholder="Researcher, writer, coder...", key=pfx + "_role")
        if st.button("Add employee", key=pfx + "_add"):
            try:
                core.add_agent(name, prov, model, url, key, role)
                for k in [k for k in st.session_state if k.startswith("new_")]:
                    del st.session_state[k]
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    if st.button("Log out", use_container_width=True):
        st.session_state.clear()
        st.rerun()

# ---------- main chat ----------
st.title("AI Employer")
if not agents:
    st.info("Add your first AI employee in the sidebar to get started.")
    st.stop()

sites = {a["name"]: a["base_url"] for a in agents}
api_agents = [a for a in agents if a["provider"] != "manual"]
c1, c2, _ = st.columns([3, 2, 2])
mode_label = c1.radio("How should they work?", ["Everyone answers", "Team task (manager splits the work)"], horizontal=True)
mode = "team" if mode_label.startswith("Team") else "all"
manager = None
if mode == "team" and api_agents:
    manager = c2.selectbox("Manager", api_agents, format_func=lambda a: a["name"])["id"]

if cid:
    for m in core.get_msgs(cid):
        if m["kind"] == "user":
            with st.chat_message("user"):
                if m["content"] != "(attachment)":
                    st.write(m["content"])
                for f in m["files"]:
                    if f["kind"] == "image":
                        st.image(base64.b64decode(f["data"]), caption=f["name"], width=260)
                    else:
                        st.caption(f"📎 {f['name']}")
        elif m["kind"] == "ai":
            with st.chat_message("assistant"):
                st.markdown(f"**{m['sender']}**")
                st.markdown(m["content"])
        elif m["kind"] == "system":
            st.caption(m["content"])
        elif m["kind"] == "pending":
            with st.container(border=True):
                st.markdown(f"**{m['sender']}** works through the website. Copy the prompt, paste it there, then paste the reply here.")
                with st.expander("Prompt to copy"):
                    st.code(m["content"], language=None)
                if sites.get(m["sender"]):
                    st.link_button(f"Open {m['sender']}'s site", sites[m["sender"]])
                reply = st.text_area("Paste the reply", key=f"r{m['id']}")
                d1, d2, _ = st.columns([1, 1, 4])
                if d1.button("Use this reply", key=f"u{m['id']}"):
                    core.resolve_pending(m["id"], reply)
                    st.rerun()
                if d2.button("Skip", key=f"s{m['id']}"):
                    core.skip_pending(m["id"])
                    st.rerun()
        else:
            st.error(f"{m['sender']}: {m['content']}")
    b1, b2, _ = st.columns([1, 1, 4])
    discuss = b1.button("Let them discuss")
    delete = b2.button("Delete this chat")
else:
    st.write("Ask a question, give a task, or attach a picture or document. Use @Name to talk to one employee.")
    discuss = delete = False

prompt = st.chat_input("Ask a question or give a task. Use @Name to talk to one employee.",
                       accept_file="multiple", file_type=FILE_TYPES)

if delete and cid:
    core.del_conv(cid)
    st.session_state.cid = None
    st.rerun()

if prompt or discuss:
    text = prompt["text"] if prompt else ""
    files = [(f.name, f.getvalue()) for f in prompt["files"]] if prompt else []
    if not cid:
        cid = st.session_state.cid = core.new_conv()
    try:
        with st.spinner("Your team is working..."):
            asyncio.run(core.send(cid, text, "all" if discuss else mode, manager, files))
        st.rerun()
    except ValueError as e:
        st.error(str(e))
