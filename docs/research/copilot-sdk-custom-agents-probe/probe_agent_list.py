"""Probe: which agents does a git-loopy-shaped session list, and SDK vs file name precedence."""
import asyncio, json, subprocess, tempfile, pathlib, importlib.metadata as md
from copilot import CopilotClient, _cli_version

root = pathlib.Path(tempfile.mkdtemp(prefix="gl-agentlist-probe-"))
repo = root / "repo"; (repo / ".github" / "agents").mkdir(parents=True)
subprocess.run(["git", "init", "-q", str(repo)], check=True)
(repo / ".github" / "agents" / "repo-probe.agent.md").write_text(
    "---\nname: repo-probe\ndescription: FILE-DEFINED repo agent\n---\nFILE PROMPT\n")
(repo / ".github" / "agents" / "collide.agent.md").write_text(
    "---\nname: collide\ndescription: FILE-DEFINED collide\n---\nFILE PROMPT\n")
skills = root / "skills"; skills.mkdir()

SDK_AGENTS = [{"name": "collide", "description": "SDK-DEFINED collide", "prompt": "SDK PROMPT"},
              {"name": "sdk-only", "description": "SDK-DEFINED only", "prompt": "SDK PROMPT"}]

CASES = [
    ("G_gitloopy_shaped_no_custom_agents", {}),
    ("H_with_sdk_agents", {"custom_agents": SDK_AGENTS}),
    ("I_sdk_agents_local_only", {"custom_agents": SDK_AGENTS, "custom_agents_local_only": True}),
    ("J_sdk_agents_no_config_discovery", {"custom_agents": SDK_AGENTS, "enable_config_discovery": False}),
]

async def main():
    print(json.dumps({"sdk": md.version("github-copilot-sdk"), "cli": _cli_version.CLI_VERSION}))
    c = CopilotClient(); await c.start()
    try:
        for name, extra in CASES:
            s = await c.create_session(on_permission_request=lambda r, i=None: {"kind": "approved"},
                                       model="claude-haiku-4.5", working_directory=str(repo),
                                       enable_skills=True, skill_directories=[str(skills)],
                                       disabled_skills=[], **extra)
            try:
                lst = await s.rpc.agent.list()
                d = lst.to_dict() if hasattr(lst, "to_dict") else lst
                agents = d.get("agents", d)
                rows = [(a.get("name"), a.get("source"), a.get("description"), a.get("path")) for a in agents
                        if a.get("source") != "builtin"]
                builtins = sorted(a.get("name") for a in agents if a.get("source") == "builtin")
                print(json.dumps({"case": name, "non_builtin": rows, "builtin": builtins}))
            except Exception as e:
                print(json.dumps({"case": name, "error": repr(e)}))
            finally:
                await s.disconnect()
    finally:
        await c.stop()

asyncio.run(main())
