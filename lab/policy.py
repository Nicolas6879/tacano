"""Code-owned policy: Jev gives P(volume bucket); code computes expected $ saving of delegating."""
from costsim import inline_cost, delegated_cost
K = {"none":1, "few":2, "moderate":5, "heavy":17}
G, OUT, FUT = 1600, 450, 52
def synth(ctx0, k, cw0=0, fut=FUT):
    calls=[[ctx0, cw0, OUT, "x"]] + [[ctx0+i*G, G, OUT, "x"] for i in range(1,k)]
    return {"calls":calls, "future_calls":fut}
def expected_saving(probs, ctx0, model, cw0=0, fut=FUT):
    ev=0
    for b,p in probs.items():
        t=synth(ctx0, K[b], cw0, fut); ev += p*(inline_cost(t)-delegated_cost(t,model))
    return ev
def decide(answers, ctx0, cw0=0, margin=0.0, haiku_th=0.7):
    probs=answers["volume"]["probabilities"]
    model="haiku" if answers["mechanical"]["noul"]>=haiku_th else "sonnet"
    ev=expected_saving(probs, ctx0, model, cw0)
    return ("delegate:"+model if ev>margin else "inline"), ev
