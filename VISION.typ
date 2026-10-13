#import "@preview/merman:0.4.0": mermaid

= Vision

So the idea here is we have set up a research environment where we're just using crow-cli as a dependency to show off what we can do with our agent harness. /home/thomas/.agents/crow/src/crow-cli/sandbox/repl-agent/client.py is a simple example of a client. we have them in crow_cli.client.

== Wake and deliveries
We have a whole wake and deliveries infrastructure just waiting to be exploited and turned into an optimal pattern for agent message passing.


== Agent-Client idea won't die
We could also just be really simplistic and create a workflow around a set of agents where we specialize/simplify base system prompt of crow-cli to make a worker agent (no specialization), a verifier agent (very much video skill pre-loaded system prompt tuned to using software and finding bug to report more than fixing anything) and of course an explorer agent that's basically the verifier but it's SUPER tuned to searching the web and cloning and examining new repos and crawling the research+oss communities  and building something new on top of what the previous agents created. and probably have like more than one agent type and do rounds where you have a researcher who isn't thinking as much about implementation but what are the interesting scientific problems/bottlenecks that our efforts can ameliorate, where can our combination of insight into applied mathematical methods like optimization and simulation (the two domains of applied math that make it pretty much all powerful lol) to real world domains like robotics and molecular and cellular biology.

And the idea is we could build this into a sort of framework where we define what happens on session/prompt, which agents get what to start with. We could probably work on some way of setting up ways to select which agent to start with in the sequence too via some extension of session options update and we will want to think about that as we get into this. But the idea is to create some kind of framework on top of crow-cli that doesn't have to touch it because it's a method for ACP agents. you basically emit the Name/Type of agent when a transition happens so user knows something is up. Might use the compaction channel we set up in the TUI which we need to port into the extension because compaction is the stuff that goes into an agent handoff after all (this might be a crucial insight into how we make agent-agent handoff extremely visible behind a single interface) but yeah we basically just want to use our agent-client thing we put together, update for v2, see if there's a better/easier way to do this with ACP conductor and proxies/how they fit into this plan because what we want do is pretty straightforward to my mind anyway. 

#mermaid("
flowchart TD
  B[Extensible AgentClient that spawns agents in sequence and passes through updates to upstream client] --> A[Spawns specialized ACP agents]
  D[Define loop which orchestrates multiagent workflows in python] --> B
  B --> C[Upstream client displays work of spawned agents and uses _meta of update to track agent type]
")

This way we can define a work loop where we can define the character of what the agent does and when the agent finishes its react loop or when `/goal` is *done* we do what the user always does, fire up another agent that has no memory of the work and just wants to poke holes in it. This is a customized/customizable loop so we can engineer the prompt we want to send. we can create custom LLM based prompting agent that is solely responsible for crafting custom, optimal messages between agents to minimize any confusion, though I personally very confident that memory subtools make handoffs between agents nearly trivial since they can read each others memories. So simple "agent {session-id} has finished" along with a canned "validate their work" type system prompt that fits with their system prompt instructions should suffice. But yeah the idea is we can use simple 
```python
async def test():
    c = ReplClient(AGENT_C_CMD, *AGENT_C_ARGS)
    d = ReplClient(AGENT_D_CMD, *AGENT_D_ARGS)
    e = ReplClient(AGENT_E_CMD, *AGENT_E_ARGS)

    compact_prompt = (
        "Summarize the conversation in RESTful markdown format and the "
        "steps you have taken, This is an interagent summary/compaction "
        "event. Respond directly. Call no tools"
    )
    c_prompt = (
        "investigate agent client protocol on internet and give your "
        "assessment of its current and future potential. "
    )
    await c.send(c_prompt)
    await c.send(compact_prompt)

    print("\n=== Conversation State ===")
    conversation = c.conversation.get(c._session_id, [])
    last_message = conversation[-1]["content"]
    d_prompt = (
        "You have been given a summary of the previous actions "
        "of another agent. Please evaluate the actions of that agent "
        "for correctness against the original request.\n"
        f"original request:\n{c_prompt}\n"
        "Here is the summary of the actions it took \n"
        f"agent summary:\n{last_message}\n"
    )
    print(last_message)
    await d.send(d_prompt)
    await d.send(compact_prompt)
    d_conversation = d.conversation.get(d._session_id, [])
    d_last_message = d_conversation[-1]["content"]

    e_prompt = (
        "You have been given a summary of the previous actions "
        "of another agent. Please evaluate the actions of that agent "
        "for correctness against the original request.\n"
        f"original request:\n{d_prompt}\n"
        "Here is the summary of the actions it took \n"
        f"agent summary:\n{d_last_message}\n"
        "ACTUAL TASK:\n"
        "But I don't really care about that. "
        "I want you to look at run_repl_client.py, which "
        "is the file that has invoked all three of the previous agents. "
        "Express appropriate shock and awe"
    )
    await e.send(e_prompt)
    await e.send(compact_prompt)
    e_conversation = e.conversation.get(e._session_id, [])
    e_last_message = e_conversation[-1]["content"]
    print(e_last_message)
    await c.close()
    await d.close()
    await e.close()
```

But of course make it more general and then wrap into a convenience method/callable that is basically a fully functioning `AcpAgent` but we overwrite the normal react loop of a standard agent with our workflow. It needs to be an async callable with a defined input and output types so we can wire up all the different `session/*` types to what are really a sequence of clients that pass back the updates to the actual client on the user's side. This is just where the routing logic lives for these standard, predefined workflows live.

And we want to do this because we're going to create an agent that is always on. Always running. And to do this we're going to need to use code to do it. And this code is going to live not inside crow-cli, but inside 
```python
classAgentClient(abc.whatever)
```
that is initialized with a callable that defines what happens on `session/prompt` and exposes the necessary seams so `session/cancel` works flawlessly.

We don't really have that right now, but I feel like this is emminently doable. It doesn't necessarily have to be elegant. We could write a simple while loop to iterate through the agents indefinitely.

But we want to build something that we can reuse. Something modular. 