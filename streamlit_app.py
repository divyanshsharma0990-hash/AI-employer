import asyncio
import streamlit as st
import core

st.set_page_config(page_title="AI Employer", page_icon="🧑‍💼", layout="wide")

PRESETS = {
    "openai": ("OpenAI (ChatGPT)", "https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("Anthropic (Claude)", "", "claude-sonnet-5-5"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    "xai": ("xAI (Grok)", "https://api.x.ai/v1", "grok-3-mini"),
    "groq": ("Groq (Llama and other open models)", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "custom": ("Other (OpenAI-compatible)", "", ""),
}

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

# ---------- sidebar: chats and team ----------
with st.sidebar:
    st.subheader("Chats")
    if st.button("New chat", use_container_width=True, type="primary"):
        st.session_state.cid = core.new_conv()
        st.rerun()
    for c in core.list_convs():
        if st.button(c["title"], key=f"c{c['id']}", use_container_width=True):
            st.session_state.cid = c["id"]
            st.rerun()

    st.subheader("Your AI team")
    for a in agents:
        left, right = st.columns([4, 1])
        left.write(f"**{a['name']}**  \n{a['model']}")
        if right.button("✕", key=f"d{a['id']}", help=f"Remove {a['name']}"):
            core.del_agent(a["id"])
            st.rerun()

    with st.expander("Add an AI employee", expanded=not agents):
        prov = st.selectbox("Provider", list(PRESETS), format_func=lambda k: PRESETS[k][0])
        with st.form("add_agent", clear_on_submit=True):
            name = st.text_input("Name (no spaces)", placeholder="Claude")
            model = st.text_input("Model", value=PRESETS[prov][2], key=f"model_{prov}")
            url = st.text_input("Base URL", value=PRESETS[prov][1], key=f"url_{prov}")
            key = st.text_input("API key", type="password")
            role = st.text_input("Role (optional)", placeholder="Researcher, writer, coder...")
            if st.form_submit_button("Add employee"):
                try:
                    core.add_agent(name, prov, model, url, key, role)
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

c1, c2, c3 = st.columns([3, 2, 2])
mode_label = c1.radio("How should they work?", ["Everyone answers", "Team task (manager splits the work)"], horizontal=True)
mode = "team" if mode_label.startswith("Team") else "all"
manager = None
if mode == "team":
    manager = c2.selectbox("Manager", agents, format_func=lambda a: a["name"])["id"]

if cid:
    for m in core.get_msgs(cid):
        if m["kind"] == "user":
            st.chat_message("user").write(m["content"])
        elif m["kind"] == "ai":
            with st.chat_message("assistant"):
                st.markdown(f"**{m['sender']}**")
                st.markdown(m["content"])
        elif m["kind"] == "system":
            st.caption(m["content"])
        else:
            st.error(f"{m['sender']}: {m['content']}")
    b1, b2, _ = st.columns([1, 1, 4])
    discuss = b1.button("Let them discuss")
    delete = b2.button("Delete this chat")
else:
    st.write("Ask a question or give a task. Use @Name to talk to one employee.")
    discuss = delete = False

prompt = st.chat_input("Ask a question or give a task. Use @Name to talk to one employee.")

if delete and cid:
    core.del_conv(cid)
    st.session_state.cid = None
    st.rerun()

if prompt or discuss:
    if not cid:
        cid = st.session_state.cid = core.new_conv()
    try:
        with st.spinner("Your team is working..."):
            asyncio.run(core.send(cid, prompt or "", "all" if discuss else mode, manager))
        st.rerun()
    except ValueError as e:
        st.error(str(e))
