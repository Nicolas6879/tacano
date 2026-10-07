def state(t):
    return {"conversation":{"previous_user_message":(t["prev_prompt"] or "")[:300],"last_assistant_message":t["last_assistant"][-600:]},"new_user_message":t["prompt"][:1500]}
QUESTIONS={"fresh":{"type":"choice",
  "instructions":"The user sends `new_user_message` in a very long chat session. Starting a brand-new session would lose the chat history but keep files, the project folder and saved memories. Would a fresh session handle `new_user_message` just as well?",
  "criteria":{"fresh_ok":"Yes: it is a new, self-contained request (new topic, new document or message pasted in full, general question) that does not rely on what was discussed.",
              "needs_history":"No: it continues, refers to, replies to or depends on something from the conversation (a thread, a plan, a previous answer, 'it', 'this', 'continue', an ongoing negotiation or task)."}}}
