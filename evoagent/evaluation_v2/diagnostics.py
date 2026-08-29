'''Matcher diagnostics and false-negative attribution for Evaluation V2.'''
from typing import Any, Dict, Iterable, List

from evoagent.evaluation_harness import RULE_TO_CWE


def cwe_for_rule(rule_id: str) -> str:
    rule_id = str(rule_id or '')
    return RULE_TO_CWE.get(rule_id, rule_id if rule_id.startswith('CWE-') else '')


def _path(value: Any) -> str:
    return str(value or '').replace(chr(92), '/')


def finding_schema_violations(findings: Iterable[dict]) -> List[Dict[str, Any]]:
    violations = []
    for index, finding in enumerate(findings):
        errors = []
        if not str(finding.get('path') or ''):
            errors.append('path')
        if int(finding.get('line') or 0) <= 0:
            errors.append('line')
        if not str(finding.get('rule_id') or ''):
            errors.append('rule_id')
        if str(finding.get('severity') or '').lower() not in {
                'low', 'medium', 'high', 'critical'}:
            errors.append('severity')
        if errors:
            violations.append({'index': index, 'fields': errors})
    return violations


def produced_rule_mapping_coverage(case_results: Iterable[dict]) -> Dict[str, Any]:
    produced = sorted({
        str(finding.get('rule_id') or '')
        for result in case_results
        for finding in result.get('prediction_details') or []
        if finding.get('rule_id')
    })
    unmapped = sorted(rule for rule in produced if not cwe_for_rule(rule))
    mapped = len(produced) - len(unmapped)
    return {
        'produced_rule_ids': produced,
        'mapped_rule_ids': mapped,
        'unmapped_rule_ids': unmapped,
        'coverage': round(mapped / len(produced), 4) if produced else 1.0,
    }


def per_cwe_recall(case_results: Iterable[dict]) -> Dict[str, Dict[str, Any]]:
    counts: Dict[str, Dict[str, int]] = {}
    for result in case_results:
        matched = {item.get('expected_index') for item in result.get('matches') or []}
        for index, truth in enumerate(result.get('expected_findings') or []):
            cwe = str(truth.get('cwe') or cwe_for_rule(truth.get('rule_id')))
            bucket = counts.setdefault(cwe, {'total': 0, 'hits': 0})
            bucket['total'] += 1
            bucket['hits'] += int(index in matched)
    return {
        cwe: {**value, 'misses': value['total'] - value['hits'],
              'recall': round(value['hits'] / value['total'], 4)
              if value['total'] else 1.0}
        for cwe, value in sorted(counts.items())
    }


def _same_gold(finding: dict, truth: dict, line_tolerance: int = 2) -> bool:
    return (
        _path(finding.get('path')) == _path(truth.get('path'))
        and cwe_for_rule(finding.get('rule_id')) == str(truth.get('cwe') or '')
        and int(truth.get('start_line') or 0) - line_tolerance
        <= int(finding.get('line') or 0)
        <= int(truth.get('end_line') or 0) + line_tolerance
    )


def _root_cause(result: dict, truth: dict) -> str:
    called = set(result.get('called_agents') or [])
    expected_rule = str(truth.get('rule_id') or '')
    required = ('security-agent' if expected_rule.startswith('SEC-')
                else 'reliability-agent' if expected_rule.startswith('REL-')
                else '')
    if required and required not in called:
        return 'NO_AGENT_ROUTED'

    predictions = list(result.get('prediction_details') or [])
    if finding_schema_violations(predictions):
        return 'MATCHER_SCHEMA_MISMATCH'
    if any(not cwe_for_rule(item.get('rule_id')) for item in predictions):
        return 'CWE_MAPPING_MISMATCH'
    if any(
        _path(item.get('path')) == _path(truth.get('path'))
        and cwe_for_rule(item.get('rule_id')) == str(truth.get('cwe') or '')
        for item in predictions
    ):
        return 'LINE_MISMATCH'

    runtime = result.get('collaboration') or {}
    rejected = list(runtime.get('rejected_findings') or [])
    if any(_same_gold(item, truth) for item in rejected):
        verifier_artifacts = [
            item for item in runtime.get('runtime_artifacts') or []
            if item.get('task_type') == 'verify.findings']
        if verifier_artifacts:
            return 'FINDING_REJECTED_BY_VERIFIER'
        return 'FINDING_DROPPED_BY_CRITIC'
    specialist_findings = [
        finding
        for artifact in runtime.get('runtime_artifacts') or []
        if str(artifact.get('task_type') or '').startswith('review.')
        for finding in (artifact.get('content') or {}).get('findings') or []
    ]
    if any(_same_gold(item, truth) for item in specialist_findings):
        return 'FINDING_DROPPED_BY_CRITIC'
    return 'RULE_NOT_TRIGGERED'


def analyze_false_negatives(case_results: Iterable[dict]) -> List[Dict[str, Any]]:
    analysis = []
    for result in case_results:
        for index in result.get('unmatched_expected_indices') or []:
            truths = result.get('expected_findings') or []
            if index >= len(truths):
                continue
            truth = truths[index]
            invocations = result.get('skill_invocations') or {}
            analysis.append({
                'case_id': result.get('id'),
                'cwe': truth.get('cwe') or cwe_for_rule(truth.get('rule_id')),
                'severity': str(truth.get('severity') or '').lower(),
                'reason': _root_cause(result, truth),
                'agents_called': list(result.get('called_agents') or []),
                'tools_called': [
                    '%s:%s' % pair for pair in
                    sorted((result.get('loop_steps_by_agent') or {}).items())],
                'candidate_skill_hit': any(
                    key != 'security-rule@1' and int(value) > 0
                    for key, value in invocations.items()),
            })
    return analysis


def diagnostic_metrics(case_results: List[dict]) -> Dict[str, Any]:
    per_cwe = per_cwe_recall(case_results)
    macro = (sum(item['recall'] for item in per_cwe.values()) / len(per_cwe)
             if per_cwe else 1.0)
    runtime_artifacts = [
        artifact for result in case_results
        for artifact in (result.get('collaboration') or {}).get(
            'runtime_artifacts') or []]
    verifier_decisions = [
        decision for artifact in runtime_artifacts
        if artifact.get('task_type') == 'verify.findings'
        for decision in ((artifact.get('content') or {}).get(
            'decisions') or {}).values()]
    rejected = sum(not bool(item.get('verified')) for item in verifier_decisions)
    total_predictions = sum(int(item.get('predicted') or 0) for item in case_results)
    return {
        'per_cwe_recall': per_cwe,
        'macro_recall_by_cwe': round(macro, 4),
        'rule_hit_rate': round(sum(bool(item.get('predicted')) for item in case_results)
                               / len(case_results), 4) if case_results else 0.0,
        'finding_yield': total_predictions,
        'verifier_rejection_rate': round(rejected / len(verifier_decisions), 4)
        if verifier_decisions else 0.0,
        'rule_mapping': produced_rule_mapping_coverage(case_results),
        'fn_analysis': analyze_false_negatives(case_results),
    }


__all__ = [
    'analyze_false_negatives', 'cwe_for_rule', 'diagnostic_metrics',
    'finding_schema_violations', 'per_cwe_recall',
    'produced_rule_mapping_coverage',
]
