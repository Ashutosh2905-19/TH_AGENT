import json
import threading
import time
from collections import defaultdict
from pathlib import Path

import yaml
from crewai import LLM, Agent, Crew, Process, Task
from crewai.llms import retry as crewai_retry

from .config import price

if hasattr(crewai_retry, "_LLM_RATE_LIMIT_MAX_ATTEMPTS"):
    crewai_retry._LLM_RATE_LIMIT_MAX_ATTEMPTS = 1

CONFIG = Path(__file__).parent / "config"
AGENTS = yaml.safe_load((CONFIG / "agents.yaml").read_text(encoding="utf-8"))
TASKS = yaml.safe_load((CONFIG / "tasks.yaml").read_text(encoding="utf-8"))
FATAL = {400, 401, 403, 404, 413, 422}


def usage_of(llm):
    try:
        u = llm.get_token_usage_summary()
        return u.prompt_tokens, u.completion_tokens, u.cached_prompt_tokens, u.reasoning_tokens
    except Exception:
        return 0, 0, 0, 0


class Runner:
    def __init__(self, settings, tracer, prior=None, calls_dir=None):
        self.s = settings
        self.tracer = tracer
        self.lock = threading.Lock()
        self.spent = 0.0
        self.by_ep = defaultdict(float, prior or {})
        self.calls_dir = calls_dir if settings.save_calls else None
        self.seq = 0
        if self.calls_dir:
            self.calls_dir.mkdir(parents=True, exist_ok=True)
            self.seq = sum(1 for _ in self.calls_dir.iterdir())
        self.models = {
            "architect": (settings.planner_model, settings.planner_max_tokens),
            "showrunner": (settings.planner_model, settings.planner_max_tokens),
            "writer": (settings.writer_model, settings.writer_max_tokens),
            "editor": (settings.editor_model, settings.editor_max_tokens),
        }

    def save_call(self, crew, task, role, model, ep, attempt, tries, out, value, failure, usage):
        if not self.calls_dir:
            return None
        with self.lock:
            self.seq += 1
            name = f"{self.seq:05d}_{task}_ep{ep:03d}_a{attempt}_r{tries}.json"
        job, agent = crew.tasks[0], crew.agents[0]
        record = {
            "task": task, "role": role, "model": model, "episode": ep, "attempt": attempt, "retry": tries,
            "agent": {"role": agent.role, "goal": agent.goal, "backstory": agent.backstory},
            "task_prompt": job.description, "expected_output": job.expected_output,
            "messages_sent": out.tasks_output[0].messages if out and out.tasks_output else None,
            "output": value.model_dump() if hasattr(value, "model_dump") else value,
            "raw_output": out.raw if out else None, "error": failure,
            "prompt_tokens": usage[0], "completion_tokens": usage[1],
        }
        (self.calls_dir / name).write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return f"calls/{name}"

    def crew(self, role, task, schema, model=None):
        default, max_tokens = self.models[role]
        model = model or default
        extra = {k: v for k, v in (("base_url", self.s.llm_base_url), ("api_key", self.s.llm_api_key)) if v}
        llm = LLM(model=model, max_tokens=max_tokens, max_retries=0, timeout=self.s.llm_timeout, **extra)
        agent = Agent(**AGENTS[role], llm=llm, allow_delegation=False, max_iter=self.s.agent_max_iter, max_retry_limit=0, verbose=False)
        job = Task(**TASKS[task], agent=agent, output_pydantic=schema)
        return Crew(agents=[agent], tasks=[job], process=Process.sequential, verbose=False), llm, model

    def charge(self, ep, cost):
        with self.lock:
            self.spent += cost
            self.by_ep[ep] += cost

    def run(self, role, task, inputs, schema=None, ep=0, attempt=0, model=None):
        total, error = 0.0, None
        for tries in range(self.s.llm_retries + 1):
            crew, llm, used = self.crew(role, task, schema, model)
            start = time.perf_counter()
            out, value, failure, status = None, None, None, None
            try:
                out = crew.kickoff(inputs=inputs)
                value = out.pydantic if schema else out.raw.strip()
                if not value:
                    failure = "empty or unparseable output"
            except Exception as e:
                status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
                failure = f"{type(e).__name__}: {e}"[:400]
            latency = round(time.perf_counter() - start, 2)
            pt, ct, cached, reasoning = usage_of(llm)
            cost = price(used, pt, ct)
            total += cost
            self.charge(ep, cost)
            call_file = self.save_call(crew, task, role, used, ep, attempt, tries, out, value, failure, (pt, ct))
            self.tracer.log(
                "llm", step=task, role=role, model=used, ep=ep, attempt=attempt, retry=tries,
                prompt_tokens=pt, completion_tokens=ct, cached_tokens=cached, reasoning_tokens=reasoning,
                cost=round(cost, 6), latency=latency, ok=failure is None, call_file=call_file,
                **({"error": failure, "status": status} if failure else {}),
            )
            if failure is None:
                return value, total
            error = failure
            if status in FATAL:
                break
            if tries < self.s.llm_retries:
                wait = self.s.retry_backoff * 2**tries
                self.tracer.log("retry", step=task, ep=ep, attempt=attempt, retry=tries + 1, wait=wait, reason=failure, status=status)
                time.sleep(wait)
        self.tracer.log("failed", step=task, ep=ep, attempt=attempt, reason=error, cost=round(total, 6))
        raise RuntimeError(f"{task} failed: {error}")
