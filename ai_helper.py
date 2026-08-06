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
                print(f"[ai_helper] attempt={attempt} {msg}", file=sys.stderr, flush=True)
                _LAST_AI_STATUS = {"ok": False, "msg": msg}
                # 401/403 是凭证问题，不重试；其它错误（如 JSON 模式 400）重试一次去掉 response_format
                if e.code in (401, 403) or attempt == 1:
                    return None
                continue
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


# e签宝产品能力知识库：供场景/价值生成时锚定到具体产品功能，避免输出空泛的通用话术
ESIGN_PRODUCT_KNOWLEDGE = (
    "e签宝核心产品能力（生成时必须结合其中 2-3 项具体能力）：\n"
    "1. 实名认证：个人实名（手机号三要素、银行卡四要素、人脸活体）、企业实名（营业执照、对公打款验证、法人授权），是签署前身份可信的基础。\n"
    "2. 电子签名/电子合同签署：SaaS 网页签署、微信签、短信签、链接签、批量签署、顺序/无序签署、骑缝章、表单签，支持 PC/移动全端。\n"
    "3. 数字证书：CA 证书签发与托管、国密 SM2 证书、UKey 证书，满足等保/国密合规要求。\n"
    "4. 电子印章：印章制作与管控、智能印控、用印审批流、印章权限分级，防止乱盖章。\n"
    "5. 合同管理：合同模板库、在线起草/编辑、合同审批流、合同分类与检索、到期提醒、合同到期自动续签提醒。\n"
    "6. 存证出证：区块链存证、可信时间戳、原文加密保全、公证处/司法鉴定直连、一键出证，保障司法采信。\n"
    "7. 智能审核（AI）：合同风险条款 AI 审查、关键信息提取、合规校验。\n"
    "8. 开放平台/API：与企业 OA、ERP、CRM、HR、业务系统深度集成，支持私有化/混合云部署。\n"
    "9. 行业方案：政务、金融、人力资源、医疗、房地产、物流、制造等垂直场景模板。\n"
    "10. 合规资质：符合《电子签名法》、等保三级、国密算法、ISO27001，具备 CA 牌照。"
)


def ai_generate_scenario_value(feature_name, category="", code=""):
    """根据功能名称，AI 生成使用场景和价值点，供 SFR 参考和修改。

    返回 {"scenario": str, "value_point": str, "related_products": str}，AI 不可用时返回空字符串。
    关键点：必须结合 e签宝 的具体产品能力，不能只写泛泛的"提升效率/降低成本"。
    """
    system = (
        "你是e签宝（中国领先的电子签名平台）的资深产品专家和售前顾问。\n"
        "你精通电子签章、电子合同、数字证书、实名认证、印章管理、存证出证等产品的业务场景和价值。\n"
        "下面是 e签宝 的核心产品能力，你在生成场景和价值时必须结合其中具体的能力点，"
        "不能只写泛泛的“提升效率、降低成本”，而要落到 e签宝 实际能做什么上：\n"
        f"{ESIGN_PRODUCT_KNOWLEDGE}\n\n"
        "根据功能名称，为售前团队生成该功能的使用场景描述和价值点提炼，要求：\n"
        "- 场景：写清楚什么类型客户、什么业务流程、传统方式的具体痛点（如纸质合同邮寄3天、印章乱用难追溯）\n"
        "- 价值：从效率、成本、合规、协同维度提炼，必须点名 e签宝 的对应产品能力（如“通过微信签+批量签署，10分钟完成原需3天的跨地域签署”）\n"
        "- related_products：列出该功能最相关的 2-4 个 e签宝 产品能力名称（用顿号分隔）\n"
        "- 语气：专业但通俗，售前可直接用来跟客户沟通"
    )
    user = (
        f"功能名称：{feature_name}\n"
        f"功能编码：{code or '（无）'}\n"
        f"功能分类：{category or '（无）'}\n\n"
        "请生成 JSON：\n"
        '{"scenario":"2-4句话描述客户业务场景和具体痛点，结合 e签宝 产品能力",'
        '"value_point":"3-4条核心价值，每条一行并点名对应 e签宝 产品能力，带量化数据",'
        '"related_products":"2-4个相关 e签宝 产品能力，顿号分隔"}\n'
        "只输出 JSON。"
    )
    raw = _llm_chat(system, user, max_tokens=800)
    if raw:
        # 去掉可能包裹的 markdown 代码块
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[cleaned.find("\n") + 1:]
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].strip()
        try:
            d = json.loads(cleaned)
            s = d.get("scenario", "").strip()
            v = d.get("value_point", "").strip()
            rp = d.get("related_products", "").strip()
            if s or v:
                return {"scenario": s, "value_point": v, "related_products": rp}
            return {"scenario": "", "value_point": "", "error": f"AI 返回为空字段，raw: {raw[:300]}"}
        except Exception as e:
            return {"scenario": "", "value_point": "", "error": f"JSON 解析失败: {e}; raw: {raw[:300]}"}
    return {"scenario": "", "value_point": "", "error": get_ai_status().get("msg", "AI 无返回")}


def ai_explain_wrong(question, reference, user_answer, judge_reason=""):
    """商务答错时，生成标准正确答案与总结点评，帮助学习吸收。

    返回 {"correct_answer": str, "summary": str}。AI 不可用时返回空字符串。
    """
    system = (
        "你是e签宝的售前教练。商务在抢答考核中答错了，请基于题目、参考答案要点和商务的实际回答，"
        "生成『标准正确答案』和『总结点评』，帮助商务理解吸收。\n"
        "要求：\n"
        "- correct_answer：用 1-2 句话给出该题目的标准正确回答，必须结合 e签宝 的具体产品功能"
        "（如实名认证、电子合同签署、数字证书、电子印章、存证出证、API 集成等），不要泛泛而谈\n"
        "- summary：3-4 句话总结：①商务为什么答错或答得不足 ②本功能对应的 e签宝 核心产品能力与客户价值 ③一句便于记忆的要点\n"
        "- 语气：鼓励式、专业、贴近售前实战"
    )
    user = (
        f"题目：{question}\n"
        f"参考答案要点：{reference or '（无）'}\n"
        f"商务回答：{user_answer}\n"
        f"判分依据：{judge_reason or '（无）'}\n\n"
        "请生成 JSON：\n"
        '{"correct_answer":"标准正确答案（结合 e签宝 具体产品功能）",'
        '"summary":"总结点评：错在哪、对应 e签宝 产品能力、记忆要点"}\n'
        "只输出 JSON。"
    )
    raw = _llm_chat(system, user, max_tokens=500)
    if raw:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[cleaned.find("\n") + 1:]
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].strip()
        try:
            d = json.loads(cleaned)
            ca = d.get("correct_answer", "").strip()
            sm = d.get("summary", "").strip()
            if ca or sm:
                return {"correct_answer": ca, "summary": sm}
        except Exception:
            pass
    return {"correct_answer": "", "summary": ""}


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
