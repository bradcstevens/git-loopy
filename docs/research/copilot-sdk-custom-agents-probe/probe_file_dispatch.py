import asyncio, json, subprocess, tempfile, pathlib
from copilot import CopilotClient
root = pathlib.Path(tempfile.mkdtemp(prefix="gl-filedispatch-")); repo = root/"repo"; (repo/".github"/"agents").mkdir(parents=True)
subprocess.run(["git","init","-q",str(repo)],check=True)
for n in ("repo-probe","collide"):
    (repo/".github"/"agents"/f"{n}.agent.md").write_text(f"---\nname: {n}\ndescription: file agent {n}\n---\nAlways reply exactly FILE-AGENT-REACHED-{n} and nothing else.\n")
SDK=[{"name":"collide","description":"sdk collide","prompt":"Always reply exactly SDK-AGENT-REACHED-collide and nothing else.","infer":True}]
CASES=[("K_dispatch_file_agent_no_sdk_agents","repo-probe",{}),("L_dispatch_collide_with_sdk_agent","collide",{"custom_agents":SDK})]
async def main():
    c=CopilotClient(); await c.start()
    try:
        for name,target,extra in CASES:
            ev=[]
            def on_event(e):
                t=getattr(e.type,"value",str(e.type))
                if t.startswith("subagent") or t in ("tool.execution_start","tool.execution_complete"):
                    try: d=e.data.to_dict()
                    except Exception: d=str(e.data)
                    ev.append((t,d))
            s=await c.create_session(on_permission_request=lambda r,i=None:{"kind":"approved"},on_event=on_event,model="claude-haiku-4.5",working_directory=str(repo),enable_skills=True,skill_directories=[str(root)],disabled_skills=[],**extra)
            try:
                r=await s.send_and_wait(f"Use your task/subagent tool to dispatch the agent type named '{target}' with the task 'say hello'. Do not use any other tool. Reply with exactly what it returned, or the exact error text if dispatch failed.",timeout=240)
                reply=r.data.content if r else None
            finally: await s.disconnect()
            starts=[d.get("agentName") if isinstance(d,dict) else d for t,d in ev if t=="subagent.started"]
            tools=[(d.get("toolName"),str(d.get("arguments"))[:200]) for t,d in ev if t=="tool.execution_start" and isinstance(d,dict)]
            res=[str(d.get("result") or d.get("error"))[:300] for t,d in ev if t=="tool.execution_complete" and isinstance(d,dict)]
            print(json.dumps({"case":name,"reply":reply,"subagent_started":starts,"tools":tools,"results":res}))
    finally: await c.stop()
asyncio.run(main())
