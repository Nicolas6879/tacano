import json, concurrent.futures as cf, jev, qs_v2
L="P D I I I I D I I D D I D D P D I D D D I I D P D P D D D D D D D D P P D P D P D D".split()
C=json.load(open("cold.json",encoding="utf-8")); assert len(L)==len(C)
Q3={"fresh":{"type":"choice",
  "instructions":"The user returns after a long break to an old, very long chat session and sends `new_user_message`. Starting a brand-new session would lose the chat history but keep files, the project folder and saved memories. Would a fresh session handle `new_user_message` just as well?",
  "criteria":{"fresh_ok":"Yes: it is a new, self-contained request (new topic, new document or message pasted in full, general question) that does not rely on what was discussed.",
              "needs_history":"No: it continues, refers to, replies to or depends on something from the conversation (a thread, a plan, a previous answer, 'it', 'this', 'continue', an ongoing negotiation or task)."}}}
def st(t): return {"conversation":{"previous_user_message":(t["prev_prompt"] or "")[:300],"last_assistant_message":t["last_assistant"][-600:]},"new_user_message":t["prompt"][:1500]}
def run(t): return jev.ask(st(t), {**qs_v2.QUESTIONS, **Q3})
with cf.ThreadPoolExecutor(6) as ex: R=list(ex.map(run,C))
json.dump(R,open("cold_answers.json","w"))
for name,get in (("v2.independent",lambda a:a["independent"]["noul"]),("v3.fresh",lambda a:a["fresh"]["probabilities"]["fresh_ok"])):
    print(name)
    for th in (0.5,0.7,0.85,0.95):
        flag=[(l,c) for l,c,a in zip(L,C,R) if get(a["answers"])>=th]
        tp=sum(l=="I" for l,_ in flag); fpD=sum(l=="D" for l,_ in flag); fpP=sum(l=="P" for l,_ in flag)
        saved=sum(c["calls"][0][1]*8/1e6-0.4 for l,c in flag if l=="I")
        print(f"  th {th}: flagged {len(flag)} | I {tp}/{L.count('I')}  P {fpP}  D {fpD} | $ saved on true I ~{saved:.1f}")
for l,c,a in zip(L,C,R): print(l, round(a["answers"]["fresh"]["probabilities"]["fresh_ok"],2), round(a["answers"]["independent"]["noul"],2), c["prompt"][:60].replace("\n"," "))
