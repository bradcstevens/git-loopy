"""Probe: does disabled_skills block a custom agent's Skill preload on the pinned runtime?"""
import asyncio, json, sys, tempfile, pathlib, importlib.metadata as md
from copilot import CopilotClient
from copilot import _cli_version

TOKEN = "ZEBRA-4471-QUOKKA"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-4.5"

root = pathlib.Path(tempfile.mkdtemp(prefix="gl-preload-probe-"))
skills = root / "skills"; (skills / "canary-skill").mkdir(parents=True)
(skills / "canary-skill" / "SKILL.md").write_text(
    "---\nname: canary-skill\ndescription: Holds the canary token for the preload probe.\n---\n\n"
    f"# Canary\n\nThe canary token is {TOKEN}. When asked for the canary token, answer with it exactly.\n")
work = root / "work"; work.mkdir()

ASK = ("What is the canary token? Answer only from what is already in your instructions or context. "
       "Do not call any tools, do not read files, do not invoke skills. "
       "If you do not know it, reply exactly NONE.")
DISPATCH = ("Dispatch the custom agent named 'probe' as a subagent with this exact task: \"" + ASK +
            "\" Then reply with exactly what it returned and nothing else.")

def agent(preload):
    a = {"name": "probe", "display_name": "probe", "description": "Preload probe agent",
         "prompt": "You are a probe. Answer the user's question tersely.", "infer": True}
    if preload: a["skills"] = ["canary-skill"]
    return a

CASES = [
    # name, preload, disabled, mode
    ("A_select_preload_enabled",   True,  False, "select"),
    ("B_select_preload_disabled",  True,  True,  "select"),
    ("C_select_nopreload_enabled", False, False, "select"),
    ("D_subagent_preload_enabled", True,  False, "subagent"),
    ("E_subagent_preload_disabled",True,  True,  "subagent"),
]

async def run_case(client, name, preload, disabled, mode):
    events, perms = [], []
    def on_event(ev):
        t = getattr(ev.type, "value", str(ev.type))
        if t.startswith(("skill", "subagent", "session.skills")) or t in ("assistant.message",):
            try: d = ev.data.to_dict() if hasattr(ev.data, "to_dict") else str(ev.data)
            except Exception: d = str(ev.data)
            events.append({"type": t, "data": d})
    def on_perm(req, inv=None):
        k = getattr(req, "kind", None); perms.append(str(getattr(k, "value", k)))
        return {"kind": "approved"}
    kw = dict(on_permission_request=on_perm, on_event=on_event, model=MODEL,
              working_directory=str(work), enable_skills=True,
              skill_directories=[str(skills)],
              disabled_skills=["canary-skill"] if disabled else [],
              custom_agents=[agent(preload)])
    if mode == "select": kw["agent"] = "probe"
    s = await client.create_session(**kw)
    try:
        r = await s.send_and_wait(DISPATCH if mode == "subagent" else ASK, timeout=240)
        reply = r.data.content if r else None
    finally:
        await s.disconnect()
    leaked = TOKEN in json.dumps(events) or (reply and TOKEN in reply)
    return {"case": name, "preload": preload, "disabled": disabled, "mode": mode,
            "reply": reply, "token_seen": bool(leaked), "permission_kinds": perms,
            "event_types": [e["type"] for e in events],
            "skill_events": [e for e in events if e["type"].startswith(("skill", "session.skills"))],
            "subagent_events": [e for e in events if e["type"].startswith("subagent")]}

async def main():
    print(json.dumps({"sdk": md.version("github-copilot-sdk"), "cli": _cli_version.CLI_VERSION, "model": MODEL}))
    client = CopilotClient(); await client.start()
    try:
        for c in CASES:
            try: res = await run_case(client, *c)
            except Exception as e: res = {"case": c[0], "error": repr(e)}
            print(json.dumps(res, default=str)); sys.stdout.flush()
    finally:
        await client.stop()

asyncio.run(main())
