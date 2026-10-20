"""Public synthetic example. Exact-match extraction, no LLM-Wiki or model."""
import json
from clean_text import desensitize

SOURCES = {
    "采购确认": ("synthetic/purchase-rule", "采购草稿必须由人工复核后才能确认提交。"),
    "资料范围": ("synthetic/scope-rule", "本演示只包含合成采购规则，不包含客户数据。"),
}


def answer(question):
    # Deliberately exact matching: no claim of semantic retrieval or model quality.
    if question not in SOURCES:
        return {"answer": "合成示例中没有依据，无法回答。", "sources": [], "refused": True}
    source, sentence = SOURCES[question]
    return {"answer": sentence, "sources": [source], "refused": False}


def main():
    cleaned, _ = desensitize("演示服务地址 http://192.168.1.8/demo")
    assert "192.168.1.8" not in cleaned
    known, unknown = answer("采购确认"), answer("供应商实际银行账户")
    assert known["sources"] and not known["refused"]
    assert unknown["refused"] and not unknown["sources"]
    print(json.dumps({"mode": "synthetic_exact_match_no_llm", "sanitized_input": cleaned,
                      "known": known, "out_of_scope": unknown}, ensure_ascii=False, indent=2))
    print("PASS: sanitization, source reference and explicit refusal")


if __name__ == "__main__":
    main()
