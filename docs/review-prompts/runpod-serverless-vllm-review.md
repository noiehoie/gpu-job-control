# Review Prompt: RunPod Serverless vLLM / Hub Template Blocker

You are reviewing a GPU workload orchestration project as a senior infrastructure engineer.

Read these two files first:

- `docs/runpod-support-question.md`
- `docs/runpod-self-hosted-research.md`

Scope:

- Focus only on the unresolved `RunPod Serverless vLLM / Hub template` path.
- Do not review general coding style, README quality, packaging, or unrelated architecture.
- Treat the already-proven paths as established facts: RunPod Pod lifecycle, Pod HTTP worker, Pod `llm_heavy`, Network Volume attach, Modal, local Ollama, and RunPod public endpoint.

Questions to answer:

1. Is the diagnosis in `docs/runpod-support-question.md` technically coherent?
2. What are the most likely missing fields, wrong assumptions, or incorrect template/runtime expectations in the GraphQL-created Serverless vLLM endpoint?
3. What exact evidence should be added before sending this to RunPod Support?
4. What exact experiments should be run next, in order, while preserving scale-to-zero and avoiding billable idle resources?
5. Is there any cheaper or more deterministic path to prove RunPod self-hosted vLLM than the current Hub/template-diff approach?

Output format:

- Findings first, ordered by importance.
- Then recommended next experiments.
- Then wording changes for `docs/runpod-support-question.md`.
- Be concise and concrete. Do not speculate without labeling it as speculation.
