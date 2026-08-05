# -*- coding: utf-8 -*-
"""
AI 辅助模块：根据功能场景/价值自动生成考题，并自动判定商务回答。

判分策略：
1. 优先调用 OpenAI 兼容接口（配置环境变量 OPENAI_API_KEY 后生效）
2. 未配置 key 时降级为「关键词覆盖率」规则判分，保证本地/无网也能跑

部署到 Render 后，在环境变量里填 OPENAI_API_KEY 即可启用真 AI 判分。
"""
import os
import json
import re
import urllib.request


def _llm_chat(system, user, max_tokens=600):
    """调用 OpenAI 兼容接口，返回文本；未配置 key 或调用失败返回 None。"""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    # 部分模型支持 JSON 模式；不支持时忽略（调用失败会自动走兜底）
    try:
        payload["response_format"] = {"type": "json_object"}
    except Exception:
        pass
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def ai_generate_quiz(feature_name, scenario, value_point):
    """根据功能的场景与价值，生成一道抢答题 + 参考答案要点。"""
    system = (
        "你是电子签名/电子合同行业的资深售前专家。"
        "根据用户提供的功能使用场景与价值点，生成一道用于考核商务的抢答题，"
        "题目应考察商务对该功能『在什么场景使用』及『带来什么价值』的理解，"
        "并给出可作为判分依据的参考答案要点。"
    )
    user = (
        f"功能名称：{feature_name}\n"
        f"使用场景：{scenario or '（未填写）'}\n"
        f"价值点：{value_point or '（未填写）'}\n\n"
        "请生成 JSON：\n"
        '{"question":"一道考察场景+价值的开放题",'
        '"reference":"2-4个关键得分点，商务回答覆盖核心意思即可算对"}\n'
        "只输出 JSON。"
    )
    raw = _llm_chat(system, user)
    if raw:
        try:
            d = json.loads(raw)
            q, ref = d.get("question", "").strip(), d.get("reference", "").strip()
            if q:
                return {"question": q, "reference": ref}
        except Exception:
            pass
    return _fallback_generate(feature_name, scenario, value_point)


def _fallback_generate(name, scenario, value_point):
    q = (
        f"关于「{name}」功能：请说明它主要解决客户的什么场景或痛点，"
        f"以及能给客户带来哪些核心价值？"
    )
    ref = value_point or scenario or "该功能的使用场景与价值"
    return {"question": q, "reference": ref}


def ai_judge(question, reference, user_answer):
    """判定商务回答是否正确理解了场景与价值。返回 {correct, reason}。"""
    system = (
        "你是电子签名行业的售前考官。判断商务的回答是否正确理解了该功能的使用场景与价值。"
        "只要回答覆盖了参考答案的关键意思（不必逐字相同、不必完整），即判为正确。"
    )
    user = (
        f"题目：{question}\n"
        f"参考答案要点：{reference}\n"
        f"商务回答：{user_answer}\n\n"
        "请判断并输出 JSON：\n"
        '{"correct": true或false, "reason": "一句话说明判分依据"}\n'
        "只输出 JSON。"
    )
    raw = _llm_chat(system, user)
    if raw:
        try:
            d = json.loads(raw)
            return {"correct": bool(d.get("correct")), "reason": d.get("reason", "")}
        except Exception:
            pass
    return _fallback_judge(reference, user_answer)


def _fallback_judge(reference, user_answer):
    """无 AI key 时的规则判分：按语义块滑动匹配，对长价值点更友好。"""
    if not reference:
        return {"correct": len(user_answer.strip()) >= 5,
                "reason": "（规则判分）回答非空即判通过"}
    # 按标点切块
    seps = re.compile(r"[，,。；;、\n\r：:（）()]+")
    blocks = [b.strip() for b in seps.split(reference) if len(b.strip()) >= 2]
    if not blocks:
        blocks = [reference.strip()]
    hit = 0
    for b in blocks:
        covered = False
        if len(b) >= 4:
            # 取块内长度>=4的滑动窗口子串，回答命中任一即算覆盖该块
            for i in range(0, len(b) - 3, 2):
                if b[i:i + 4] in user_answer:
                    covered = True
                    break
        elif b in user_answer:
            covered = True
        if covered:
            hit += 1
    ratio = hit / len(blocks)
    correct = ratio >= 0.4
    return {"correct": correct,
            "reason": f"（规则判分）覆盖 {hit}/{len(blocks)} 个要点，{'达标' if correct else '未达标'}（阈值40%）"}
