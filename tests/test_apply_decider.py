"""Option decider: long lists are answered in plain words and matched locally (one cheap call, no 240-item enum)."""
import asyncio

from job_agent.apply.llm import LLM, FlashDecider

COUNTRIES = [f"Country{i} +{i}" for i in range(200)] + ["Indonesia +62", "India +91", "British Indian Ocean Territory +246"]


class FakeLLM(LLM):
    def __init__(self, answer):
        super().__init__(model="fake", provider="openrouter")
        self.answer, self.purposes = answer, []

    async def complete_json(self, purpose, system, user, schema, name="answer"):
        self.purposes.append(purpose)
        if purpose == "decide_long_answer":
            return {"answer": self.answer, "confidence": 0.95, "rationale": "from facts"}
        return {"choice": schema["properties"]["choice"]["enum"][0], "confidence": 0.8, "rationale": "picked"}


def test_long_list_is_matched_locally_from_a_plain_answer():
    llm = FakeLLM("India")
    d = asyncio.run(FlashDecider(llm).decide("Country", COUNTRIES, "facts"))
    assert d.choice == "India +91" and d.confidence == 0.95
    assert llm.purposes == ["decide_long_answer"]                    # a single call; no enum over 200+ options


def test_long_list_with_no_supported_answer_returns_none_option():
    d = asyncio.run(FlashDecider(FakeLLM("NONE")).decide("Q", COUNTRIES + ["NONE OF THESE"], "facts"))
    assert d.choice == "NONE OF THESE"


def test_short_list_uses_an_enum_of_real_options():
    llm = FakeLLM("")
    d = asyncio.run(FlashDecider(llm).decide("Notice period", ["Immediately", "30 days"], "facts"))
    assert d.choice in {"Immediately", "30 days"} and llm.purposes == ["decide_option"]
