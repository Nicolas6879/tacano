from qs_v2 import state, QUESTIONS as V2
QUESTIONS = {
 "volume": V2["volume"],
 "intent": V2["intent"],
 "model_fit": {"type":"choice",
   "instructions":"A strong orchestrator model (Opus) can hand the work in `new_user_message` to a helper with a written brief, then review the result. Which is the cheapest helper that would do this work well? Consider what the work actually requires, given `conversation`.",
   "criteria":{
     "haiku":"Pure execution with no real judgment: run or start commands/services, drive a browser through known steps, fetch/collect/search and report information, add items to a tool, generate media in an app, or trivial edits (change a color, text, label, tooltip, add a simple category) where what to do is fully specified.",
     "sonnet":"Engineering work needing code understanding: implement features, build or modify UI with logic, debug something failing, analyze code, data or documents, write scripts, investigate and synthesize findings.",
     "keep_opus":"Should not be delegated: the user wants the orchestrator's own opinion, advice, decision, strategy, plan, evaluation of trade-offs or a status summary of the conversation; or it is a short answer that needs no work."}},
 "sensitive": {"type":"noul",
   "instructions":"Does handling `new_user_message` involve secrets or high-stakes actions: private keys, API keys, passwords, wallets or signing transactions, payments or money movements, security threats or scams, or publishing/sending something on the user's behalf?"},
}
