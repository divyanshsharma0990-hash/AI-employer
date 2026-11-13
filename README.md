# AI Employer

Manage several AIs (ChatGPT, Claude, Gemini, Grok, Llama on Groq, ...) as one team.
Everyone answers in a shared group chat, or a manager AI splits a task among the others.
All chats are saved and can be reopened from the sidebar.

Features: attach pictures, PDFs, Word files and text files in the chat; edit, test and remove employees;
load each provider's real model list into a dropdown; automatic retries when a provider is busy;
and "Web login" employees (you log in to the AI's website yourself, copy the prompt there, paste the reply back).

## Run locally
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    streamlit run streamlit_app.py

The first visit asks you to create the admin account (8+ character password).

## Put it on GitHub
    git init
    git add .
    git commit -m "AI Employer MVP"
    git branch -M main
    git remote add origin https://github.com/YOUR-NAME/ai-employer.git
    git push -u origin main

Use a private repo. `.gitignore` already keeps the database and key file out of Git.

## Deploy on Streamlit Community Cloud
1. Go to share.streamlit.io, sign in with GitHub, click Create app.
2. Pick the repo, branch `main`, main file `streamlit_app.py`.
3. Open Advanced settings > Secrets and paste:
       ADMIN_USERNAME = "your-username"
       ADMIN_PASSWORD = "a-new-strong-password"
       FERNET_KEY = "paste-a-key-here"
   Make a key with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
4. Deploy. Set the app to private (Share settings) so only you can open it.

Note: Community Cloud wipes local files when the app restarts, so saved chats and employees can disappear.
For permanent history, move the database to a hosted one (Supabase, Neon, Turso).
