# -*- coding: utf-8 -*-
"""
AI 辅助模块：根据功能场景/价值自动生成考题，并自动判定商务回答。

判分策略：
1. 优先调用 OpenAI 兼容接口（配置环境变量 OPENAI_API_KEY 后生效）
2. 未配置 key 时降级为「关键词覆盖率」规则判分，保证本地/无网也能跑

部署到 Render 后，在环境变量里填 OPENAI_API_KEY 即可启用真 AI 判分。
"""
import os
import sys
import json
import re
import urllib.request
import urllib.error


# 最近一次 AI 调用的状态（供前端/调试展示）
_LAST_AI_STATUS = {"ok": False, "msg": "尚未调用"}


def get_ai_status():
    """返回最近一次 AI 调用状态，供前端展示「AI 是否可用」。"""
    return dict(_LAST_AI_STATUS)


def _llm_chat(system, user, max_tokens=600):
    """调用 OpenAI 兼容接口，返回文本；任何异常都不会向外冒。"""
    global _LAST_AI_STATUS
    try:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            _LAST_AI_STATUS = {"ok": False, "msg": "未配置 OPENAI_API_KEY（规则判分）"}
            return None
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        def _do_request(use_json_mode):
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "max_tokens": max_tokens,
            }
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}
            req = urllib.request.Request(
                base + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

        # 第一次带 JSON 模式；若失败去掉重试一次
        for attempt, use_json in enumerate([True, False]):
            try:
                content = _do_request(use_json)
                _LAST_AI_STATUS = {"ok": True, "msg": f"AI 调用成功（model={model}）"}
                return content
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")[:300]
                except Exception:
                    pass
                msg = f"AI 调用失败 HTTP {e.code}: {body}"
                print(f"[ai_helper] {msg}", file=sys.stderr, flush=True)
                _LAST_AI_STATUS = {"ok": False, "msg": msg}
                return None  # 不再重试，避免给中转站造成压力
            except Exception as e:
                msg = f"AI 调用异常: {type(e).__name__}: {e}"
                print(f"[ai_helper] attempt={attempt} {msg}", file=sys.stderr, flush=True)
                if attempt == 0:
                    continue
                _LAST_AI_STATUS = {"ok": False, "msg": msg}
                return None
        _LAST_AI_STATUS = {"ok": False, "msg": "AI 调用失败（已重试，详见 Render Logs）"}
        return None
    except BaseException as e:  # 兜底：任何异常都吞掉，绝不外冒
        msg = f"AI 兜底异常: {type(e).__name__}: {e}"
        try:
            print(f"[ai_helper] {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass
        try:
            _LAST_AI_STATUS = {"ok": False, "msg": msg}
        except Exception:
            pass
        return None


def ai_test():
    """主动发一次极小请求，验证 AI 配置是否可用。返回 {ok, msg, detail}。"""
    try:
        key = os.environ.get("OPENAI_API_KEY")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        info = {"configured": bool(key), "base_url": base, "model": model}
        if not key:
            return {"ok": False, "msg": "未配置 OPENAI_API_KEY", "detail": info}
        raw = _llm_chat("你是测试助手", "回复两个字的JSON: {\"ok\":true}", max_tokens=20)
        status = get_ai_status()
        return {"ok": status.get("ok", False),
                "msg": status.get("msg", ""),
                "detail": info,
                "response": (raw[:200] if raw else None)}
    except BaseException as e:
        return {"ok": False, "msg": f"自检接口异常: {type(e).__name__}: {e}",
                "detail": {"configured": bool(os.environ.get("OPENAI_API_KEY")),
                           "base_url": os.environ.get("OPENAI_BASE_URL", ""),
                           "model": os.environ.get("OPENAI_MODEL", "")}}


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
        '"reference":"2-4个关键得分点，用逗号分隔，判分时按覆盖率打分"}\n'
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
    """判定商务回答是否正确理解了场景与价值。返回 {correct, reason}。

    判分规则（严格覆盖率制）：
    - 将参考答案拆分为独立关键要点，逐个判断商务回答是否覆盖
    - 覆盖率 ≥60% 判正确，<60% 判不正确
    - reason 中需说明覆盖率明细（已覆盖/遗漏了哪些要点）
    """
    system = (
        "你是电子签名行业的售前考官。判断商务的回答是否正确理解了该功能的使用场景与价值。\n"
        "判分规则（严格覆盖率制）：\n"
        "1. 将参考答案拆分为 2-4 个独立的关键要点\n"
        "2. 逐个判断商务回答是否覆盖了该要点（语义匹配，不必逐字相同）\n"
        "3. 覆盖率 = 已覆盖要点数 / 总要点数\n"
        "4. 覆盖率 ≥60% 才判 correct=true；<60% 判 correct=false\n"
        "5. reason 中必须说明：总要点数、已覆盖哪些、遗漏了哪些、覆盖率百分比"
    )
    user = (
        f"题目：{question}\n"
        f"参考答案要点：{reference}\n"
        f"商务回答：{user_answer}\n\n"
        "请判断并输出 JSON：\n"
        '{"correct": true或false, "reason": "覆盖X/Y个要点(覆盖率Z%)，已覆盖：...；遗漏：..."}\n'
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
    correct = ratio >= 0.5
    return {"correct": correct,
            "reason": f"（规则判分）覆盖 {hit}/{len(blocks)} 个要点（{ratio:.0%}），{'达标' if correct else '未达标'}（阈值50%）"}
