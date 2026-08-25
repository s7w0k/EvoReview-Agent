from typing import Any, Dict


def to_markdown(report: Dict[str, Any]) -> str:
    title = "# EvoAgent PR Review"
    if report.get("pull_request") is not None:
        title += " — #%s" % report["pull_request"]
    lines = [
        title,
        "",
        "**Repository:** `%s`  " % report.get("repository", ""),
        "**Risk:** `%s`  " % report.get("risk", "unknown"),
        "**Reviewer:** `%s`" % report.get("reviewer", "unknown"),
        "",
        report.get("summary", ""),
        "",
    ]
    collaboration = report.get("collaboration") or {}
    if collaboration:
        lines.extend([
            "## Multi-agent collaboration",
            "",
            "- Protocol: `%s`" % collaboration.get("protocol", "unknown"),
            "- Assignments: `%s`; dialogue rounds: `%s`; messages: `%s`" % (
                collaboration.get("planned_assignments", 0),
                collaboration.get("dialogue_rounds", 0),
                collaboration.get("messages", 0),
            ),
            "- Retries: `%s`; handoffs: `%s`; rejected by verification: `%s`" % (
                collaboration.get("retries", 0), collaboration.get("handoffs", 0),
                collaboration.get("rejected_findings", 0),
            ),
            "",
        ])
    findings = report.get("findings", [])
    if not findings:
        lines.append("✅ No actionable issue detected in the added lines.")
        return "\n".join(lines) + "\n"
    lines.extend(["## Findings", ""])
    icons = {"critical": "🚨", "high": "🔴", "medium": "🟠", "low": "🟡"}
    for index, item in enumerate(findings, 1):
        severity = item.get("severity", "medium")
        lines.extend(
            [
                "### %d. %s %s" % (index, icons.get(severity, "•"), item.get("title", "Finding")),
                "",
                "`%s:%s` · **%s** · `%s`" % (
                    item.get("path", ""), item.get("line", 0), severity.upper(), item.get("rule_id", "")),
                "",
                item.get("explanation", ""),
                "",
                "**Evidence**",
                "",
                "```text",
                item.get("evidence", ""),
                "```",
                "",
                "**Suggested fix:** %s" % item.get("fix", ""),
                "",
                "**Suggested test:** %s" % item.get("test", ""),
                "",
            ]
        )
    classification = report.get("classification") or {}
    if classification:
        labels = (
            ("confirmed", "确定缺陷"),
            ("needs_review", "需人工确认"),
            ("suggestion", "改进建议"),
        )
        lines.extend(["## 结果分级", ""])
        for bucket, label in labels:
            items = classification.get(bucket) or []
            if not items:
                continue
            lines.append("### %s（%d）" % (label, len(items)))
            for item in items:
                lines.append(
                    "- `%s` %s:%s（置信度 %.2f）" % (
                        item.get("rule_id", ""), item.get("path", ""),
                        item.get("line", 0), item.get("confidence", 0.0),
                    )
                )
            lines.append("")
    return "\n".join(lines) + "\n"
