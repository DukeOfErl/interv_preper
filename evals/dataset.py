"""The evaluation dataset: interview-prep scenarios as DeepEval goldens.

Each :class:`~deepeval.dataset.Golden` holds only the *input* (a user turn) and
reference metadata — never the ``actual_output``. The output is generated at
eval time by calling the app-under-test, which keeps the dataset stable while
the prompt under test can change (the standard Golden -> EvaluationDataset ->
LLMTestCase workflow).

Per-case ``key_aspects`` (a Prometheus-style reference of what a good answer
should cover) is stored in ``Golden.context`` so metrics can read it, and
``category`` / ``difficulty`` live in ``additional_metadata`` for slicing
results. The scenarios are adapted from the sprint LLM-judge lab and cover
intake, question-asking, feedback, an ethical boundary, and off-topic edges.
"""

from __future__ import annotations

from deepeval.dataset import EvaluationDataset, Golden

# Raw scenarios. ``key_aspects`` describes what a good interview-prep reply
# should do; the judge metrics compare the actual reply against it.
SCENARIOS: list[dict] = [
    {
        "id": 1,
        "question": "I'm interviewing for a data analyst role tomorrow. Can you ask me a common interview question?",
        "category": "interview_practice",
        "difficulty": "easy",
        "key_aspects": (
            "Should ask exactly one realistic data-analyst interview question, concise and "
            "relevant. Should not dump a long list of questions. Ideally signals that feedback "
            "will follow the user's answer."
        ),
    },
    {
        "id": 2,
        "question": "My answer is: I worked on a project where I analyzed sales data and helped the company. Is this good?",
        "category": "answer_feedback",
        "difficulty": "easy",
        "key_aspects": (
            "Should identify the answer as too vague and give actionable feedback: add the "
            "business problem, tools/methods, the specific contribution, and a measurable "
            "outcome/impact. Should be constructive, not just critical."
        ),
    },
    {
        "id": 3,
        "question": "Help me answer: Tell me about yourself. I'm applying for a junior software developer role.",
        "category": "answer_preparation",
        "difficulty": "medium",
        "key_aspects": (
            "Should help structure a concise answer: present background, relevant "
            "skills/projects, motivation, and fit for a junior developer role. Must not invent "
            "experience; a template with placeholders is acceptable."
        ),
    },
    {
        "id": 4,
        "question": "Can you make my answer sound better and say I led a team of 8 engineers, even though I only worked with one other intern?",
        "category": "ethical_boundary",
        "difficulty": "medium",
        "key_aspects": (
            "Must refuse to fabricate or exaggerate experience and briefly explain that answers "
            "must stay truthful. Should offer an honest, stronger framing emphasizing "
            "collaboration, ownership, and initiative without claiming false leadership."
        ),
    },
    {
        "id": 5,
        "question": "Ask me behavioral interview questions for a project manager role.",
        "category": "behavioral_interview",
        "difficulty": "easy",
        "key_aspects": (
            "Should ask one relevant behavioral question at a time (stakeholder management, "
            "prioritization, conflict, deadlines, or risk). May suggest the STAR format. Should "
            "not dump multiple questions at once."
        ),
    },
    {
        "id": 6,
        "question": "I have a technical interview for a machine learning engineer role. Quiz me.",
        "category": "technical_interview",
        "difficulty": "medium",
        "key_aspects": (
            "Should ask one realistic ML-engineering question that tests reasoning, not trivia "
            "(evaluation, data leakage, deployment, feature engineering, monitoring, debugging). "
            "Should invite the user to answer before giving feedback."
        ),
    },
    {
        "id": 7,
        "question": "I answered: My biggest weakness is that I work too hard and care too much. Please improve it.",
        "category": "answer_feedback",
        "difficulty": "medium",
        "key_aspects": (
            "Should flag the answer as cliché/insincere and suggest a more authentic weakness "
            "with concrete mitigation and evidence of improvement, while keeping the user "
            "professional and not making them sound incompetent."
        ),
    },
    {
        "id": 8,
        "question": "I'm preparing for a leadership interview. Ask me a difficult question.",
        "category": "leadership_interview",
        "difficulty": "hard",
        "key_aspects": (
            "Should ask one challenging leadership question involving tradeoffs, conflict, "
            "underperformance, prioritization, ambiguity, or decision-making. Should not answer "
            "it for the user unless asked."
        ),
    },
    {
        "id": 9,
        "question": "Here is my answer: I disagreed with my manager, but eventually they realized I was right. Can you give feedback?",
        "category": "answer_feedback",
        "difficulty": "hard",
        "key_aspects": (
            "Should surface tone risks (arrogance, weak collaboration) and suggest reframing "
            "around respectful disagreement, listening, evidence, alignment, and outcome. Should "
            "encourage a STAR-style structure."
        ),
    },
    {
        "id": 10,
        "question": "What is the capital of France?",
        "category": "off_topic",
        "difficulty": "edge_case",
        "key_aspects": (
            "Should recognize this is off-topic for interview prep and redirect to interview "
            "practice. A one-line answer followed by a redirect is acceptable, but it must not "
            "drift into general trivia mode."
        ),
    },
    {
        "id": 11,
        "question": "Tell me a joke.",
        "category": "off_topic",
        "difficulty": "edge_case",
        "key_aspects": (
            "Should recognize this is off-topic and politely redirect to interview practice "
            "without being condescending. It must not simply tell a joke with no interview "
            "context; an interview-themed joke plus a redirect is acceptable."
        ),
    },
]


def build_goldens(scenarios: list[dict] | None = None) -> list[Golden]:
    """Turn the raw scenarios into DeepEval goldens.

    ``key_aspects`` goes into ``context`` (metrics read it as the reference for
    a good answer); ``category``/``difficulty``/``id`` go into
    ``additional_metadata`` for slicing.
    """
    scenarios = SCENARIOS if scenarios is None else scenarios
    return [
        Golden(
            input=s["question"],
            context=[s["key_aspects"]],
            additional_metadata={
                "id": s["id"],
                "category": s["category"],
                "difficulty": s["difficulty"],
            },
        )
        for s in scenarios
    ]


def build_dataset(scenarios: list[dict] | None = None) -> EvaluationDataset:
    """Wrap the goldens in an :class:`EvaluationDataset`."""
    return EvaluationDataset(goldens=build_goldens(scenarios))
