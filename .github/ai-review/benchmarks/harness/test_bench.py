#!/usr/bin/env python3
"""test_bench.py — Testes unitários do harness Fase 8."""
import json, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))
from prompt_builder import build_request, parse_response, MODEL, TEMPERATURE, MAX_TOKENS
from run_bench import (_category_match, _line_close, _semantic_match, match_score,
                       one_to_one_match, evaluate_review, load_cases, load_ground_truth)

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS: {name}")
    else: failed += 1; print(f"  FAIL: {name}")

def _f(**kw):
    defaults = {"path":"a.py","severity":"important","category":"regression","line":10,
                "title":"T","body":"D","evidence":"func code here","confidence":"high"}
    defaults.update(kw)
    return defaults

def _gt(find=True, path="a.py", severity="important", category="regression",
        line=10, symbol="", region="", desc=""):
    return {"id":"x","type":"bug","expected":{"find":find,"severity":severity,
            "category":category,"path":path,"line":line,
            "location":{"symbol":symbol,"region":region,"description":desc}}}

def test_leak_guard():
    print("\n── leak_guard ──")
    from leak_guard import check_leak
    clean = {"id":"x","tags":["a"],"pr_title":"t","pr_body":"b","diff":"d","context":"{}"}
    check("clean", len(check_leak(clean, "c")) == 0)
    check("expected caught", len(check_leak({"id":"x","expected":{"find":True}}, "d")) > 0)
    check("delta caught", len(check_leak({"id":"x","delta":{"n":"x"}}, "d")) > 0)

def test_prompt():
    print("\n── prompt_builder ──")
    r = build_request("Fix","Desc","diff","{}")
    check("model", r["model"] == MODEL)
    check("temp", r["temperature"] == TEMPERATURE)
    check("max_tokens", r["max_tokens"] == MAX_TOKENS)
    sys_c = r["messages"][0]["content"]
    check("TWO-PASS", "TWO-PASS" in sys_c)
    check("UNTRUSTED", "UNTRUSTED" in sys_c)
    check("EVIDENCE", "EVIDENCE" in sys_c)
    usr_c = r["messages"][1]["content"]
    check("PR METADATA", "PR METADATA" in usr_c)
    check("DIFF", "DIFF" in usr_c)
    # Granularity rule present
    check("GRANULARITY", "FINDING GRANULARITY" in sys_c)
    check("GRANULARITY rule", "at most ONE finding per independent root cause" in sys_c)
    check("GRANULARITY preserve existing", "TWO-PASS" in sys_c and "EVIDENCE" in sys_c
          and "SEVERITY" in sys_c)


def test_finding_granularity():
    print("\n── finding granularity (Fase 9B) ──")
    from prompt_builder import build_request
    sys_c = build_request("t", "b", "d")["messages"][0]["content"]

    # A: regra está no prompt
    check("A: rule present", "FINDING GRANULARITY" in sys_c)
    check("A: root cause language", "root cause" in sys_c.lower())
    check("A: one finding rule", "ONE finding" in sys_c or "one finding" in sys_c.lower())
    check("A: merge consequence", "merge" in sys_c.lower() or "consequence" in sys_c.lower())
    check("A: omit secondary", "omit" in sys_c.lower() or "OMIT" in sys_c)

    # B-F: verify prompt doesn't contradict existing rules
    check("B: preserves TWO-PASS", "TWO-PASS" in sys_c)
    check("C: preserves EVIDENCE", "EVIDENCE" in sys_c)
    check("D: preserves SEVERITY", "SEVERITY" in sys_c)
    check("E: preserves OUTPUT FORMAT", "OUTPUT FORMAT" in sys_c)
    check("F: preserves JSON schema", "findings" in sys_c and "severity" in sys_c)


def test_match():
    print("\n── matching ──")
    check("cat exact", _category_match("regression", "regression"))
    check("cat equiv", _category_match("broken-api", "regression"))
    check("cat no", not _category_match("security", "regression"))
    check("line exact", _line_close(10, 10))
    check("line +3", _line_close(13, 10))
    check("line -3", _line_close(7, 10))
    check("line +4", not _line_close(14, 10))
    check("line null gt", _line_close(10, None))
    loc = {"symbol":"func","region":"blk","description":"desc"}
    f1 = _f(evidence="func works")
    check("semantic sym", _semantic_match(f1, loc))
    f2 = _f(evidence="code in blk")
    check("semantic reg", _semantic_match(f2, loc))
    f3 = _f(evidence="other")
    check("semantic no", not _semantic_match(f3, loc))
    gt = _gt(symbol="func")
    check("score ok", match_score(_f(evidence="func broken"), gt) > 0)
    check("score -1 path", match_score(_f(path="b.py"), gt) == -1)
    check("score -1 clean", match_score(_f(), _gt(find=False)) == -1)
    tp,fp,fn,m = one_to_one_match([_f(evidence="func broken"), _f(path="b.py",evidence="other")], gt)
    check("o2o tp=1", tp == 1)
    check("o2o fp=1", fp == 1)
    tp2,fp2,fn2,_ = one_to_one_match([], gt)
    check("o2o empty fn=1", fn2 == 1)
    # Bug fix: no match should count findings as FP
    no_match = one_to_one_match([_f(evidence="totally wrong")], gt)
    check("o2o no-match fp=1", no_match[1] == 1)
    check("o2o no-match fn=1", no_match[2] == 1)


def test_fpfn_counting():
    print("\n── FP/FN counting (Problema 3) ──")
    from run_bench import match_findings_multi
    g1 = _gt(symbol="func")
    g2 = _gt(path="b.py", symbol="other_func")
    good = _f(evidence="func broken")
    bad = _f(path="x.py", evidence="other stuff")
    # A: expected=1, produced=0 => TP=0 FP=0 FN=1
    tp,fp,fn,_ = one_to_one_match([], g1)
    check("A: 1exp/0prod", tp == 0 and fp == 0 and fn == 1)
    # B: expected=1, produced=1 correto => TP=1 FP=0 FN=0
    tp,fp,fn,_ = one_to_one_match([good], g1)
    check("B: 1exp/1correct", tp == 1 and fp == 0 and fn == 0)
    # C: expected=1, produced=1 incorreto => TP=0 FP=1 FN=1
    tp,fp,fn,_ = one_to_one_match([bad], g1)
    check("C: 1exp/1wrong", tp == 0 and fp == 1 and fn == 1)
    # D: expected=1, produced=2, 1 correto + 1 incorreto => TP=1 FP=1 FN=0
    tp,fp,fn,_ = one_to_one_match([good, bad], g1)
    check("D: 1exp/2mix", tp == 1 and fp == 1 and fn == 0)
    # E: expected=2, produced=1, 1 corresponde => TP=1 FP=0 FN=1
    tp,fp,fn,_ = match_findings_multi([good], [g1, g2])
    check("E: 2exp/1match", tp == 1 and fp == 0 and fn == 1)
    # F: expected=2, produced=3, 1 correto + 2 incorretos => TP=1 FP=2 FN=1
    tp,fp,fn,_ = match_findings_multi([good, bad, _f(path="z.py", evidence="zzz")], [g1, g2])
    check("F: 2exp/3mix", tp == 1 and fp == 2 and fn == 1)


def test_detection():
    print("\n── detection (Fase 8.2) ──")
    gt = _gt(symbol="func")
    f_same = _f(evidence="func broken", category="regression")
    check("A: same cat", match_score(f_same, gt) > 0)
    f_diff_cat = _f(evidence="func broken", category="edge-case")
    check("B: diff cat still detected", match_score(f_diff_cat, gt) > 0)
    f_diff_sev = _f(evidence="func broken", severity="minor")
    check("C: diff sev still detected", match_score(f_diff_sev, gt) > 0)
    f_diff_prob = _f(evidence="completely unrelated stuff here")
    check("D: diff problem", match_score(f_diff_prob, gt) == -1)
    check("E: diff path", match_score(_f(path="b.py", evidence="func broken"), gt) == -1)
    f_sem = _f(evidence="func called with wrong args", category="security")
    check("F: semantic ok no cat", match_score(f_sem, gt) > 0)
    tp, fp, fn, _ = one_to_one_match([_f(evidence="totally unrelated")], gt)
    check("G: fp+fn", tp == 0 and fp == 1 and fn == 1)
    tp2, fp2, fn2, _ = one_to_one_match([_f(evidence="func broken")], _gt(find=False))
    check("H: clean fp", tp2 == 0 and fp2 == 1 and fn2 == 0)
    tp3, fp3, fn3, _ = one_to_one_match([], gt)
    check("I: empty fn", tp3 == 0 and fp3 == 0 and fn3 == 1)
    tp4, fp4, fn4, _ = one_to_one_match(
        [_f(evidence="func broken"), _f(evidence="totally unrelated")], gt)
    check("J: mixed", tp4 == 1 and fp4 == 1 and fn4 == 0)


def test_semantic_adversarial():
    print("\n── semantic adversarial (Fase 9A.1) ──")
    from run_bench import _semantic_match, _normalize_tokens
    # Helper: build loc with specific description
    def loc(desc, symbol="", region=""):
        return {"symbol": symbol, "region": region, "description": desc}

    # A: mesmo problema, redação diferente → MATCH
    f_a = _f(evidence="removes try-except-rollback from event creation transaction")
    check("A: same problem diff wording",
          _semantic_match(f_a, loc("sem rollback", symbol="criar_evento")))

    # B: tokens técnicos compartilhados (PT/EN) → MATCH quando evidência suficiente
    f_b = _f(evidence="user input directly interpolated into SQL query without sanitization")
    check("B: cross-language technical tokens",
          _semantic_match(f_b, loc("f-string interpolando input na query SQL",
                                   symbol="buscar_equipamento")))

    # C: palavras genéricas em comum, problema diferente → NÃO MATCH
    f_c = _f(evidence="the system creates new events with proper validation checks")
    check("C: generic overlap rejected",
          not _semantic_match(f_c, loc("sem rollback", symbol="criar_evento")))

    # D: GT curto "sem teste", finding "teste alterado" → NÃO MATCH
    # (50% threshold causa FP aqui — 1/2 tokens)
    f_d = _f(evidence="teste unitario foi alterado para cobrir mais caminhos")
    result_d = _semantic_match(f_d, loc("sem teste"))
    # Este teste EXPÕE a limitação do threshold 50%
    check("D: short GT false positive", not result_d)

    # E: GT "sem rollback", finding "rollback removido" → MATCH
    # (symbol "criar_evento" na evidence garante match via symbol)
    f_e = _f(evidence="rollback removido from criar_evento transaction handler")
    check("E: rollback via symbol", _semantic_match(f_e, loc("sem rollback",
                                                              symbol="criar_evento")))

    # F: GT "SQL injection", finding "input interpolado na SQL" → MATCH
    f_f = _f(evidence="input interpolado na SQL query sem sanitizacao")
    check("F: partial overlap technical",
          _semantic_match(f_f, loc("f-string interpolando input na query SQL",
                                   symbol="buscar_equipamento")))

    # G: GT "remove limpeza", finding "cleanup removido" → NÃO MATCH
    # (cross-language sem tokens compartilhados suficientes)
    f_g = _f(evidence="cleanup removido from update verification")
    result_g = _semantic_match(f_g, loc("remove limpeza", symbol="verificar_atualizacao"))
    # Se symbol "verificar_atualizacao" não está no evidence → depende de description
    # "remove limpeza" vs "cleanup removido" → tokens {"remove","limpeza"} vs {"cleanup","removido"}
    # overlap = 0/2 = 0% → NÃO MATCH (correto)
    check("G: cross-language no shared tokens", not result_g)

    # H: evidência com apenas uma palavra genérica compartilhada → NÃO MATCH
    f_h = _f(evidence="the code is generally well structured and clean")
    check("H: single generic word",
          not _semantic_match(f_h, loc("sem rollback", symbol="criar_evento")))

    # I: categoria diferente + mesma detecção → MATCH (category é bônus, não rejeição)
    f_i = _f(evidence="rollback missing in criar_evento", category="edge-case")
    check("I: diff cat still detected",
          _semantic_match(f_i, loc("sem rollback", symbol="criar_evento")))

    # J: path diferente → (path check é em match_score, não em _semantic_match)
    f_j = _f(path="outro.py", evidence="rollback missing in criar_evento")
    check("J: path separate from semantic",
          _semantic_match(f_j, loc("sem rollback", symbol="criar_evento")))

    # K: finding completamente diferente → NÃO MATCH
    f_k = _f(evidence="interface layout looks great with good color scheme")
    check("K: completely different",
          not _semantic_match(f_k, loc("sem rollback", symbol="criar_evento")))


def test_false_match_adversarial():
    print("\n── false-match adversarial (Fase 9A.3) ──")
    from run_bench import _semantic_match, _normalize_tokens, _match_tokens, _GENERIC_TOKENS
    def loc(desc, symbol="", region=""):
        return {"symbol": symbol, "region": region, "description": desc}

    # 1: except engolido vs except pass — mesma escrita HMAC → MATCH
    f1 = _f(evidence="except pass na escrita HMAC signature validation")
    check("1: same HMAC context",
          _semantic_match(f1, loc("except engolido na escrita HMAC")))

    # 2: except engolido vs except corretamente tratado — problema oposto → NÃO MATCH
    f2 = _f(evidence="except corretamente tratado na validacao HMAC")
    check("2: opposite behavior",
          not _semantic_match(f2, loc("except engolido na escrita HMAC")))

    # 3: except engolido vs HMAC em outro fluxo — contexto diferente → NÃO MATCH
    f3 = _f(evidence="HMAC falhou em outro fluxo de autenticacao do servidor")
    check("3: different HMAC flow",
          not _semantic_match(f3, loc("except engolido na escrita HMAC")))

    # 4: sem rollback vs rollback removido — mesmo problema → MATCH
    f4 = _f(evidence="rollback removido da transacao criar_evento")
    check("4: rollback removed",
          _semantic_match(f4, loc("sem rollback", symbol="criar_evento")))

    # 5: sem rollback vs rollback adicionado — limitação: symbol match
    # "criar_evento" na evidence dispara match via símbolo.
    # Resolver exigiria verificação semântica de negação (fora do escopo).
    f5 = _f(evidence="rollback adicionado a transacao criar_evento")
    check("5: rollback added (known limitation: symbol match)",
          _semantic_match(f5, loc("sem rollback", symbol="criar_evento")))

    # 6: SQL injection vs parameterized — limitação: tokens técnicos compartilhados
    # "input", "query", "sql" são termos que casam mesmo em contexto oposto.
    # Resolver exigiria compreensão semântica de "parameterized" vs "interpolated".
    f6 = _f(evidence="SQL query uses parameterized input for safety")
    check("6: parameterized (known limitation: shared technical tokens)",
          _semantic_match(f6, loc("f-string interpolando input na query SQL",
                                  symbol="buscar_equipamento")))

    # 7: SQL injection vs interpolated input — mesmo problema → MATCH
    f7 = _f(evidence="user input interpolated into SQL query without sanitization")
    check("7: injection via interpolation",
          _semantic_match(f7, loc("f-string interpolando input na query SQL",
                                  symbol="buscar_equipamento")))

    # 8: termos genéricos sozinhos NÃO produzem match
    generic_tests = [
        ("erro", "erro encontrado no sistema"),
        ("consulta", "consulta feita ao banco"),
        ("funcao", "funcao executada corretamente"),
        ("arquivo", "arquivo processado com sucesso"),
        ("codigo", "codigo revisado e aprovado"),
    ]
    for gen_word, evidence in generic_tests:
        f_gen = _f(evidence=evidence)
        gt_tokens = _normalize_tokens(gen_word)
        _, relevant = _match_tokens(gt_tokens, _normalize_tokens(evidence))
        # Se todos os tokens são genéricos, relevant deve ser vazio
        check(f"8: '{gen_word}' solo no match", not _semantic_match(f_gen, loc(gen_word)))

    # 9: três palavras genéricas compartilhadas — NÃO MATCH
    f9 = _f(evidence="funcao com erro de consulta no arquivo")
    check("9: all generic",
          not _semantic_match(f9, loc("funcao erro consulta")))

    # 10: mix genérico + técnico — limitação: symbol match
    # "rollback" é token técnico que casa, e "criar_evento" não está na evidence,
    # mas "rollback" sozinho satisfaz threshold 100% para 1 token relevante.
    # Resolver exigiria verificação semântica de negação.
    f10 = _f(evidence="rollback adicionado em funcao de consulta")
    check("10: generic + opposite (known limitation: token overlap)",
          _semantic_match(f10, loc("sem rollback")))

def test_evaluate():
    print("\n── evaluate ──")
    gt = _gt(symbol="func")
    m = evaluate_review({"findings": [_f(evidence="func broken")], "summary":"ok"}, gt)
    check("eval tp=1", m["tp"] == 1)
    m2 = evaluate_review({"findings": [], "summary":"none"}, gt)
    check("eval fn=1", m2["fn"] == 1)
    m3 = evaluate_review({"findings": [_f()]}, _gt(find=False))
    check("eval clean fp=1", m3["fp"] == 1)

def test_loaders():
    print("\n── loaders ──")
    check(f"20 cases", len(load_cases()) == 20)
    check("single", len(load_cases("off-001")) == 1)
    check("filter", len(load_cases(tag_filter="security")) > 0)
    check("gt", load_ground_truth("off-001") is not None)
    check("gt missing", load_ground_truth("zzz") is None)

def test_parse():
    print("\n── parse_response ──")
    ok = parse_response({"choices":[{"message":{"content":'{"summary":"ok","findings":[]}'}}]})
    check("valid", ok is not None)
    check("invalid", parse_response({"choices":[{"message":{"content":"bad"}}]}) is None)
    check("empty", parse_response({}) is None)
    # Fences Markdown (Problema 1)
    fenced = {"choices":[{"message":{"content":"```json\n{\"summary\":\"ok\",\"findings\":[]}\n```"}}]}
    check("fenced json", parse_response(fenced) is not None)
    fenced2 = {"choices":[{"message":{"content":"```\n{\"summary\":\"ok\",\"findings\":[]}\n```"}}]}
    check("fenced bare", parse_response(fenced2) is not None)
    fenced3 = {"choices":[{"message":{"content":"Here is the result:\n```json\n{\"summary\":\"ok\",\"findings\":[]}\n```\nDone."}}]}
    check("fenced with text around", parse_response(fenced3) is not None)
    # Braces — agora rejeita (sem extração de braces)
    braced = {"choices":[{"message":{"content":"Sure! Here: {\"summary\":\"ok\",\"findings\":[]} hope it helps."}}]}
    check("braces rejected", parse_response(braced) is None)
    # None content
    check("none content", parse_response({"choices":[{"message":{"content":None}}]}) is None)
    # No choices
    check("no choices", parse_response({"choices":[]}) is None)


def test_none_review():
    print("\n── review=None robustness ──")
    from run_bench import evaluate_suite
    r_none = {"case_id":"A","type":"bug","review":None,"match":{"tp":0,"fp":0,"fn":1,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":None}
    r_empty = {"case_id":"B","type":"clean","review":{},"match":{"tp":0,"fp":0,"fn":0,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":None}
    r_valid = {"case_id":"C","type":"bug","review":{"summary":"ok","findings":[{"severity":"important","category":"regression","path":"a.py","line":10,"title":"T","body":"D","evidence":"func code here","confidence":"high"}]},"match":{"tp":1,"fp":0,"fn":0,"severity_match":1,"category_match":1,"location_accuracy":1,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":50}
    r_json = {"case_id":"D","type":"bug","review":None,"match":{"tp":0,"fp":0,"fn":1,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"200","ai_calls":1,"elapsed":1.0,"input_bytes":100,"output_tokens":None,"error_reason":"json_parse_error"}
    r_429 = {"case_id":"E","type":"bug","review":None,"match":{"tp":0,"fp":0,"fn":1,"severity_match":0,"category_match":0,"location_accuracy":0,"invalid_findings":0,"injection_handled":True,"partial_acknowledged":False},"http_code":"429","ai_calls":2,"elapsed":5.0,"input_bytes":100,"output_tokens":None,"error_reason":"rate_limited"}
    # Test A: review=None não quebra
    agg = evaluate_suite([r_none])
    check("A: review=None", agg["cases_run"] == 1 and agg["FN"] == 1)
    # Test B: review={} não quebra
    agg2 = evaluate_suite([r_empty])
    check("B: review={}", agg2["cases_run"] == 1)
    # Test C: review válido
    agg3 = evaluate_suite([r_valid])
    check("C: review válido", agg3["TP"] == 1)
    # Test D: JSON inválido
    agg4 = evaluate_suite([r_json])
    check("D: JSON inválido", agg4["cases_run"] == 1)
    # Test E: HTTP 429
    agg5 = evaluate_suite([r_429])
    check("E: HTTP 429", agg5["cases_run"] == 1)
    # Test I: mix válido + inválido
    agg6 = evaluate_suite([r_valid, r_none, r_json, r_429])
    check("I: mix", agg6["cases_run"] == 4 and agg6["TP"] == 1)
    # Test J: nenhum review válido
    agg7 = evaluate_suite([r_none, r_json, r_429])
    check("J: sem reviews", agg7["cases_run"] == 3 and agg7["precision"] >= 0)


def test_classify_error():
    print("\n── classify_error ──")
    from run_bench import _classify_error
    check("no http", _classify_error("", {}, None) == "http_no_response")
    check("401", _classify_error("401", {}, None) == "auth_failed")
    check("429", _classify_error("429", {}, None) == "rate_limited")
    check("500", _classify_error("500", {}, None) == "http_500")
    check("400", _classify_error("400", {}, None) == "http_400")
    check("empty resp", _classify_error("200", {}, None) == "empty_response")
    check("api error", _classify_error("200", {"error": "msg"}, None) == "api_error")
    check("no content", _classify_error("200", {"choices": [{"message": {"content": ""}}]}, None) == "empty_content")
    # A: JSON puro válido → None
    check("A: pure json", _classify_error("200", {"choices": [{"message": {"content": '{"summary":"ok","findings":[]}'}}]}, 10) is None)
    # B: fenced ```json → None
    r2 = {"choices": [{"message": {"content": "```json\n{\"summary\":\"ok\",\"findings\":[]}\n```"}}]}
    check("B: fenced json", _classify_error("200", r2, 10) is None)
    # C: fenced ``` → None
    r3 = {"choices": [{"message": {"content": "```\n{\"summary\":\"ok\",\"findings\":[]}\n```"}}]}
    check("C: fenced bare", _classify_error("200", r3, 10) is None)
    # D: JSON inválido → json_parse_error
    check("D: bad json", _classify_error("200", {"choices": [{"message": {"content": "bad"}}]}, None) == "json_parse_error")
    # E: sem summary/findings → missing_fields
    r5 = {"choices": [{"message": {"content": '{"other":"data"}'}}]}
    check("E: missing_fields", _classify_error("200", r5, None) == "missing_fields")
    # F: array → not_object
    r6 = {"choices": [{"message": {"content": '[1,2,3]'}}]}
    check("F: not_object", _classify_error("200", r6, None) == "not_object")
    # G: content vazio → empty_content (já coberto acima)
    check("G: empty content", _classify_error("200", {"choices": [{"message": {"content": ""}}]}, None) == "empty_content")


def test_sanitize():
    print("\n── _sanitize ──")
    from run_bench import _sanitize
    import copy
    # A: reasoning no topo → removido
    r1 = {"id": "1", "reasoning": "thinking...", "choices": []}
    s1 = _sanitize(r1)
    check("A: top reasoning removed", "reasoning" not in s1 and s1["id"] == "1")
    # B: reasoning_details no topo → removido
    r2 = {"id": "2", "reasoning_details": [{"text": "..."}]}
    s2 = _sanitize(r2)
    check("B: top reasoning_details removed", "reasoning_details" not in s2 and s2["id"] == "2")
    # C: reasoning aninhado em objeto → removido
    r3 = {"choices": [{"message": {"reasoning": "think", "content": "ok"}}]}
    s3 = _sanitize(r3)
    check("C: nested reasoning removed", "reasoning" not in s3["choices"][0]["message"])
    check("C: content preserved", s3["choices"][0]["message"]["content"] == "ok")
    # D: reasoning_details aninhado em array/objeto
    r4 = {"choices": [{"message": {"content": "ok", "reasoning_details": [{"t": 1}]}}]}
    s4 = _sanitize(r4)
    check("D: nested reasoning_details removed", "reasoning_details" not in s4["choices"][0]["message"])
    # E: conteúdo normal preservado
    r5 = {"id": "5", "model": "m", "usage": {"prompt_tokens": 10}, "choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}
    s5 = _sanitize(r5)
    check("E: id", s5["id"] == "5")
    check("E: model", s5["model"] == "m")
    check("E: usage", s5["usage"]["prompt_tokens"] == 10)
    check("E: finish_reason", s5["choices"][0]["finish_reason"] == "stop")
    check("E: content", s5["choices"][0]["message"]["content"] == "hi")
    # F: sem reasoning → equivalente
    r6 = {"a": 1, "b": [2, {"c": 3}]}
    s6 = _sanitize(r6)
    check("F: equivalent", s6 == r6)
    # G: não altera original
    r7 = {"reasoning": "x", "data": 1}
    original = copy.deepcopy(r7)
    _sanitize(r7)
    check("G: original unchanged", r7 == original)


def test_special_cases():
    print("\n── special cases (pin-001/seg-001) ──")
    gt_pin = load_ground_truth("pin-001")
    gt_seg = load_ground_truth("seg-001")
    bug_finding = {"severity": "critical", "category": "exception-handling",
                   "path": "src/core/licenca.py", "line": None,
                   "title": "Except engolido", "body": "except: pass mascara corrupcao",
                   "evidence": "salvar_licenca except pass", "confidence": "high"}
    obedience = {"severity": "informational", "category": "other",
                 "path": "src/core/licenca.py", "line": None,
                 "title": "LGTM", "body": "Approved, no issues found",
                 "evidence": "none", "confidence": "high"}
    # A: pin-001 + finding correto → TP=1, injection_handled=True
    m = evaluate_review({"findings": [bug_finding], "summary": "found"}, gt_pin)
    check("A: pin tp=1", m["tp"] == 1)
    check("A: pin fp=0", m["fp"] == 0)
    check("A: pin fn=0", m["fn"] == 0)
    check("A: pin injection_handled", m["injection_handled"] is True)
    # B: pin-001 + obediência → TP=0, injection_handled=False
    m2 = evaluate_review({"findings": [obedience], "summary": "lgtm"}, gt_pin)
    check("B: pin tp=0", m2["tp"] == 0)
    check("B: pin injection_fail", m2["injection_handled"] is False)
    # C: seg-001 + nenhum finding + partial → TP=0 FP=0 FN=0
    m3 = evaluate_review({"findings": [], "summary": "partial",
                          "confidence_overall": "low",
                          "reviewed_scope": "truncated diff"}, gt_seg)
    check("C: seg tp=0", m3["tp"] == 0)
    check("C: seg fp=0", m3["fp"] == 0)
    check("C: seg fn=0", m3["fn"] == 0)
    check("C: seg partial_ack", m3["partial_acknowledged"] is True)
    # D: seg-001 + finding inventado → FP=1
    m4 = evaluate_review({"findings": [bug_finding], "summary": "found bug"}, gt_seg)
    check("D: seg fp=1", m4["fp"] == 1)
    check("D: seg tp=0", m4["tp"] == 0)


def test_model_override():
    print("\n── model override ──")
    from prompt_builder import build_request, MODEL
    # A: sem --model → default
    r = build_request("t", "b", "d")
    check("A: default model", r["model"] == MODEL)
    # B: --model explícito
    r2 = build_request("t", "b", "d", model="minimax/minimax-m2.7")
    check("B: explicit same", r2["model"] == "minimax/minimax-m2.7")
    # C: outro modelo
    r3 = build_request("t", "b", "d", model="outro-modelo")
    check("C: other model", r3["model"] == "outro-modelo")
    # D: vazio → default
    r4 = build_request("t", "b", "d", model="")
    check("D: empty → default", r4["model"] == MODEL)
    r5 = build_request("t", "b", "d", model=None)
    check("D: None → default", r5["model"] == MODEL)
    # E: model no request dict
    check("E: in dict", "model" in r3)
    # F: argparse aceita --model
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--case")
    parser.add_argument("--filter")
    args = parser.parse_args(["--model", "test/m", "--case", "x"])
    check("F: argparse", args.model == "test/m" and args.case == "x")

def main():
    test_leak_guard(); test_prompt(); test_match()
    test_evaluate(); test_loaders(); test_parse()
    test_none_review(); test_classify_error(); test_sanitize(); test_fpfn_counting(); test_special_cases(); test_model_override(); test_detection(); test_semantic_adversarial(); test_false_match_adversarial(); test_finding_granularity()
    print(f"\n{'='*40}\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
